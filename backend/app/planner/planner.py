"""The prompt→column planner (plan §4.4).

Natural-language request → validated column plan. One structured-output
Groq call produces a draft; server-side validation then re-checks
EVERYTHING — ``depends_on`` is recomputed from the templates via
:mod:`app.engine.graph` (planner claims are never trusted), names must be
unique and unreserved, and unknown references, self-references and cycles
are rejected.

Exactly one repair retry on unparseable or domain-invalid output, then a
structured failure — never a crash, never a guess. Provider failures
(``LlmError``, e.g. ``groq_not_configured`` → 503 at the API layer)
propagate unchanged.

The planner is stateless: it returns a preview spec the user can edit;
the apply path re-validates everything through ``sheets/service.py``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.engine.budget import estimate_run_cost
from app.engine.graph import RESERVED_REFS, ColumnGraph, TemplateError
from app.planner.prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from app.planner.spec import (
    PLAN_JSON_SCHEMA,
    PlanColumn,
    PlanResult,
    RawPlanResponse,
    SheetColumn,
)
from app.providers.types import LlmProvider, LlmRequest, LlmResult
from app.sheets.models import Column

DEFAULT_PLANNER_MODEL = "openai/gpt-oss-120b"
"""Matches ``Settings.planner_model`` (D5); the API layer passes the
configured value — this default exists for direct construction."""

_MAX_PROBLEMS_IN_PROMPT = 8


@dataclass(frozen=True)
class _Problem:
    """One domain-validation failure, fed back to the model on repair."""

    detail: str


class Planner:
    """Prompt→column planner over an ``LlmProvider`` (Groq)."""

    def __init__(
        self,
        llm: LlmProvider,
        *,
        model: str = DEFAULT_PLANNER_MODEL,
        max_columns: int = 30,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_columns = max_columns

    async def plan(
        self,
        request: str,
        *,
        columns: Sequence[Column],
        samples: Mapping[str, Sequence[str]] | None = None,
        row_count: int = 0,
        pending_identity_rows: int = 0,
    ) -> PlanResult:
        """Turn a natural-language request into a validated column plan.

        ``columns`` are the sheet's existing (non-deleted) columns —
        needed for name uniqueness and reference resolution. ``samples``
        optionally maps a column name to ≤3 example cell values shown to
        the model as DATA. ``row_count``/``pending_identity_rows`` feed the
        labelled cost estimate.
        """
        if not request.strip():
            return PlanResult(
                intent="needs_clarification",
                question="Describe the column(s) you want to add.",
            )
        context = [
            SheetColumn(
                name=column.name,
                output_type=column.output_type,
                samples=list((samples or {}).get(column.name, [])),
            )
            for column in columns
        ]
        result = await self._complete(build_user_prompt(request, context))
        outcome = self._validate(
            result.content,
            columns,
            request=request,
            row_count=row_count,
            pending_identity_rows=pending_identity_rows,
        )
        if isinstance(outcome, PlanResult):
            return outcome.model_copy(update={"mock": result.mock})

        # One repair retry: the failures go back to the model verbatim.
        repair = build_repair_prompt(
            request, context, [p.detail for p in outcome[:_MAX_PROBLEMS_IN_PROMPT]]
        )
        retry = await self._complete(repair)
        retried = self._validate(
            retry.content,
            columns,
            request=request,
            row_count=row_count,
            pending_identity_rows=pending_identity_rows,
        )
        if isinstance(retried, PlanResult):
            return retried.model_copy(update={"mock": retry.mock})
        return PlanResult(
            intent="failed",
            reason="the planner's response could not be validated: "
            + "; ".join(p.detail for p in retried[:_MAX_PROBLEMS_IN_PROMPT]),
            mock=retry.mock,
        )

    async def _complete(self, user_prompt: str) -> LlmResult:
        return await self._llm.complete(
            LlmRequest(
                model=self._model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=PLAN_JSON_SCHEMA,
            )
        )

    def _validate(
        self,
        content: Any,
        existing: Sequence[Column],
        *,
        request: str,
        row_count: int,
        pending_identity_rows: int,
    ) -> PlanResult | list[_Problem]:
        """Server-side re-validation of the planner's draft.

        Returns the finished ``PlanResult`` for valid output — including
        ``needs_clarification`` and ``unsupported`` intents — or the list
        of problems for the repair retry.
        """
        try:
            raw = RawPlanResponse.model_validate(content)
        except ValidationError as exc:
            return [_Problem(detail) for detail in _shape_problems(exc)]

        if raw.intent == "needs_clarification":
            return PlanResult(intent="needs_clarification", question=raw.question)
        if raw.intent == "unsupported":
            return PlanResult(intent="unsupported", reason=raw.reason)

        problems: list[_Problem] = []

        existing_names = {column.name for column in existing}
        templates = {column.name: column.prompt_template for column in existing}
        plan_names: set[str] = set()
        for planned in raw.columns:
            name = planned.name
            if name in RESERVED_REFS:
                problems.append(
                    _Problem(f"{name!r} is reserved for the row identity, not a column")
                )
            elif name in existing_names:
                problems.append(_Problem(f"a column named {name!r} already exists"))
            elif name in plan_names:
                problems.append(_Problem(f"duplicate column name {name!r} in this plan"))
            else:
                plan_names.add(name)
            if planned.output_type == "enum" and not planned.enum_options:
                problems.append(_Problem(f"enum column {name!r} needs enum_options"))
            templates[name] = planned.prompt_template

        if len(existing) + len(raw.columns) > self._max_columns:
            problems.append(_Problem(f"a sheet holds at most {self._max_columns} columns"))

        graph: ColumnGraph | None = None
        try:
            graph = ColumnGraph(templates)
        except TemplateError as exc:
            problems.append(_Problem(exc.message))
        if problems:
            return problems

        assert graph is not None
        planned_columns = [
            PlanColumn(
                name=planned.name,
                prompt_template=planned.prompt_template,
                output_type=planned.output_type,
                enum_options=planned.enum_options,
                source_scope=planned.source_scope,
                depth=planned.depth,
                depends_on=graph.dependencies(planned.name),
            )
            for planned in raw.columns
        ]
        return PlanResult(
            intent="add_columns",
            columns=planned_columns,
            estimated_cost_usd=estimate_run_cost(
                pending_identity_rows, row_count * len(planned_columns)
            ),
        )


def _shape_problems(exc: ValidationError) -> list[str]:
    """Compact, model-facing descriptions of a schema/parse failure —
    locations and messages only, never echoed input values."""
    problems = []
    for error in exc.errors()[:_MAX_PROBLEMS_IN_PROMPT]:
        loc = ".".join(str(part) for part in error["loc"])
        problems.append(f"{loc or 'response'}: {error['msg']}")
    return problems or ["response did not match the required JSON shape"]
