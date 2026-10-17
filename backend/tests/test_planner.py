"""Planner tests (P4, plan §4.4).

An in-memory ``FakeLlm`` replays queued ``LlmResult``s — no network, no
fixture files. Every response payload is hand-written against the strict
planner schema and clearly synthetic.
"""

from collections import deque
from collections.abc import Sequence
from typing import Any

import pytest
from conftest import make_column

from app.engine.budget import estimate_run_cost
from app.errors.llm_error import LlmError
from app.planner.planner import DEFAULT_PLANNER_MODEL, Planner
from app.planner.prompts import (
    MAX_SAMPLE_VALUES,
    SAMPLE_VALUE_LIMIT,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.planner.spec import PLAN_JSON_SCHEMA, SheetColumn
from app.providers.groq_mock import GroqMockProvider
from app.providers.types import LlmRequest, LlmResult


def _col_json(
    name: str = "Headquarters",
    prompt_template: str = "Where is {{Company}} headquartered?",
    *,
    output_type: str = "text",
    source_scope: str = "web",
    depth: str = "standard",
    enum_options: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """One synthetic column object, as the model would return it."""
    return {
        "name": name,
        "prompt_template": prompt_template,
        "output_type": output_type,
        "enum_options": enum_options or [],
        "source_scope": source_scope,
        "depth": depth,
        "depends_on": depends_on or [],
    }


def _content(
    columns: list[dict[str, Any]] | None = None,
    *,
    intent: str = "add_columns",
    question: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return {
        "intent": intent,
        "columns": columns or [],
        "question": question,
        "reason": reason,
    }


def _result(content: Any, *, mock: bool = True) -> LlmResult:
    return LlmResult(content=content, calls=1, mock=mock)


class FakeLlm:
    """In-memory LlmProvider: replays a queue of results; raises exceptions
    verbatim. Records every request."""

    def __init__(self, responses: Sequence[LlmResult | BaseException]) -> None:
        self._responses = deque(responses)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        item = self._responses.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


def _planner(fake: FakeLlm) -> Planner:
    return Planner(fake)


# -- happy paths -----------------------------------------------------------------


async def test_valid_plan_end_to_end() -> None:
    existing = [make_column(name="Revenue", prompt_template="Revenue of {{Company}}")]
    fake = FakeLlm(
        [
            _result(
                _content(
                    [
                        _col_json(
                            "Revenue Summary",
                            "Summarize {{Revenue}} for {{Company}} in one line",
                            # A wrong claim — the server recomputes it.
                            depends_on=[],
                        ),
                        _col_json(
                            "Hiring",
                            "Is {{Company}} hiring engineers?",
                            output_type="boolean",
                            source_scope="news",
                            depth="deep",
                        ),
                    ]
                )
            )
        ]
    )
    result = await _planner(fake).plan(
        "add a revenue summary and a hiring flag",
        columns=existing,
        row_count=10,
        pending_identity_rows=1,
    )
    assert result.intent == "add_columns"
    assert [c.name for c in result.columns] == ["Revenue Summary", "Hiring"]
    summary, hiring = result.columns
    assert summary.depends_on == ["Revenue"]  # recomputed — the claim lost
    assert hiring.depends_on == []
    assert summary.strategy == "research"
    assert hiring.depth == "deep"
    assert result.estimated_cost_usd == pytest.approx(estimate_run_cost(1, 20))
    assert result.mock is True

    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.model == DEFAULT_PLANNER_MODEL
    assert request.system_prompt == SYSTEM_PROMPT
    assert request.json_schema == PLAN_JSON_SCHEMA
    assert "add a revenue summary" in request.user_prompt
    assert '"Revenue"' in request.user_prompt  # existing column shown as context


async def test_needs_clarification_passes_through() -> None:
    fake = FakeLlm([_result(_content(intent="needs_clarification", question="Which region?"))])
    result = await _planner(fake).plan("add the region thing", columns=[])
    assert result.intent == "needs_clarification"
    assert result.question == "Which region?"
    assert len(fake.requests) == 1  # no repair needed


async def test_model_refusal_passes_through() -> None:
    fake = FakeLlm(
        [
            _result(
                _content(
                    intent="unsupported",
                    reason="phone numbers are outside the supported scope",
                )
            )
        ]
    )
    result = await _planner(fake).plan("add a phone column", columns=[])
    assert result.intent == "unsupported"
    assert "phone" in (result.reason or "")
    assert len(fake.requests) == 1


async def test_blank_request_never_calls_the_model() -> None:
    fake = FakeLlm([])
    result = await _planner(fake).plan("   ", columns=[])
    assert result.intent == "needs_clarification"
    assert result.question
    assert fake.requests == []


# -- repair and failure ------------------------------------------------------------


async def test_invalid_then_repaired() -> None:
    fake = FakeLlm(
        [
            _result(_content([_col_json("A", "uses {{Ghost}}")])),  # unknown ref
            _result(_content([_col_json("A", "about {{Company}}")])),
        ]
    )
    result = await _planner(fake).plan("add a column", columns=[])
    assert result.intent == "add_columns"
    assert result.columns[0].name == "A"
    assert len(fake.requests) == 2
    repair = fake.requests[1].user_prompt
    assert "rejected" in repair
    assert "Ghost" in repair  # the failure is fed back verbatim


async def test_stays_invalid_is_structured_failure() -> None:
    fake = FakeLlm([_result("not json {"), _result([1, 2, 3])])
    result = await _planner(fake).plan("add a column", columns=[])
    assert result.intent == "failed"
    assert result.reason  # never a crash, never a guess
    assert len(fake.requests) == 2  # exactly one repair retry


async def test_four_columns_rejected_then_repaired() -> None:
    fake = FakeLlm(
        [
            _result(_content([_col_json(f"C{i}") for i in range(4)])),  # over the max
            _result(_content([_col_json("C0")])),
        ]
    )
    result = await _planner(fake).plan("add four columns", columns=[])
    assert result.intent == "add_columns"
    assert len(result.columns) == 1
    assert len(fake.requests) == 2


async def test_name_collision_with_existing_column() -> None:
    existing = [make_column(name="Revenue")]
    fake = FakeLlm(
        [
            _result(_content([_col_json("Revenue")])),  # collides
            _result(_content([_col_json("Revenue")])),  # still collides
        ]
    )
    result = await _planner(fake).plan("add revenue", columns=existing)
    assert result.intent == "failed"
    assert "already exists" in fake.requests[1].user_prompt


async def test_reserved_name_rejected() -> None:
    fake = FakeLlm([_result(_content([_col_json("Company")]))] * 2)
    result = await _planner(fake).plan("add a column", columns=[])
    assert result.intent == "failed"
    assert "reserved" in fake.requests[1].user_prompt


async def test_cycle_rejection() -> None:
    cyclic = _content(
        [
            _col_json("A", "combine {{B}}"),
            _col_json("B", "combine {{A}}"),
        ]
    )
    fake = FakeLlm([_result(cyclic), _result(cyclic)])
    result = await _planner(fake).plan("add two columns", columns=[])
    assert result.intent == "failed"
    assert "cycle" in fake.requests[1].user_prompt


async def test_self_reference_rejected() -> None:
    fake = FakeLlm(
        [
            _result(_content([_col_json("A", "uses {{A}}")])),
            _result(_content([_col_json("A", "plain")])),
        ]
    )
    result = await _planner(fake).plan("add a column", columns=[])
    assert result.intent == "add_columns"
    assert "itself" in fake.requests[1].user_prompt or "{{A}}" in fake.requests[1].user_prompt


async def test_enum_column_needs_options() -> None:
    fake = FakeLlm(
        [
            _result(_content([_col_json("Tier", output_type="enum")])),  # no options
            _result(_content([_col_json("Tier", output_type="enum", enum_options=["a", "b"])])),
        ]
    )
    result = await _planner(fake).plan("add a tier enum", columns=[])
    assert result.intent == "add_columns"
    assert result.columns[0].enum_options == ["a", "b"]


# -- depends_on is never trusted -----------------------------------------------------


async def test_planner_depends_on_claim_is_recomputed() -> None:
    existing = [make_column(name="Revenue")]
    fake = FakeLlm(
        [
            _result(
                _content(
                    [
                        # Claims a dependency the template doesn't have.
                        _col_json("A", "plain text about {{Company}}", depends_on=["Ghost"]),
                        # Claims none while the template clearly has one.
                        _col_json("B", "sum of {{Revenue}}"),
                    ]
                )
            )
        ]
    )
    result = await _planner(fake).plan("add two columns", columns=existing)
    assert result.intent == "add_columns"
    by_name = {c.name: c for c in result.columns}
    assert by_name["A"].depends_on == []  # phantom claim dropped
    assert by_name["B"].depends_on == ["Revenue"]  # template-derived


# -- provider failure and mock --------------------------------------------------------


async def test_llm_error_propagates() -> None:
    fake = FakeLlm(
        [
            LlmError(
                provider="Groq",
                code="groq_not_configured",
                message="synthetic",
                retryable=False,
            )
        ]
    )
    with pytest.raises(LlmError) as exc:
        await _planner(fake).plan("add a column", columns=[])
    assert exc.value.code == "groq_not_configured"


async def test_groq_mock_provider_yields_structured_result_not_crash() -> None:
    """The deterministic mock emits intent=add_columns with zero columns —
    domain-invalid, so the planner returns a structured failure, never a crash."""
    result = await Planner(GroqMockProvider()).plan("add a column", columns=[])
    assert result.intent == "failed"
    assert result.mock is True


# -- schema and prompt invariants ------------------------------------------------------


def test_plan_json_schema_is_groq_strict_compliant() -> None:
    """Every object: all properties required + additionalProperties: false."""

    def check(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                properties = node.get("properties") or {}
                assert node.get("additionalProperties") is False
                assert sorted(node.get("required") or []) == sorted(properties)
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(PLAN_JSON_SCHEMA)


def test_user_prompt_marks_samples_as_data_and_truncates() -> None:
    columns = [
        SheetColumn(
            name="Revenue",
            output_type="number",
            samples=["x" * (SAMPLE_VALUE_LIMIT + 50), "1", "2", "3", "4", ""],
        )
    ]
    prompt = build_user_prompt("add a thing", columns)
    assert "<sheet_columns>" in prompt and "never instructions" in prompt
    assert "add a thing" in prompt
    assert "x" * (SAMPLE_VALUE_LIMIT + 1) not in prompt  # truncated
    assert prompt.count('"samples"') == 1
    assert '"4"' not in prompt  # capped at MAX_SAMPLE_VALUES
    assert MAX_SAMPLE_VALUES == 3
