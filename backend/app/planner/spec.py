"""Planner contract: the hand-written Groq response schema + strict models.

``PLAN_JSON_SCHEMA`` is the JSON Schema sent as Groq ``response_format``
(plan §4.4, D5). It is HAND-WRITTEN — flat and simple, not Pydantic's
``$defs`` output — because Groq strict mode requires every object property
listed in ``required`` and ``additionalProperties: false`` on every object
(plan §1.2). To satisfy that, the envelope is deliberately flat: ``columns``,
``question`` and ``reason`` are always present and the ``intent`` decides
which are meaningful.

Groq parses ``choices[0].message.content`` as JSON; we then validate it
with the strict Pydantic models below. Schema adherence guarantees shape,
never domain or policy correctness — all of that is re-checked server-side
in :mod:`app.planner.planner`.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.sheets.models import Depth, OutputType, SourceScope, Strategy

MAX_PLAN_COLUMNS = 3

OutputIntent = Literal["add_columns", "needs_clarification", "unsupported"]
"""The planner's vocabulary. Our own outcomes add ``"failed"`` (see
:class:`PlanResult`) for output that never validates."""

PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "A column plan: add columns, ask one question, or refuse.",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["add_columns", "needs_clarification", "unsupported"],
            "description": "what kind of answer this is",
        },
        "columns": {
            "type": "array",
            "maxItems": MAX_PLAN_COLUMNS,
            "description": "new columns for intent=add_columns; empty otherwise",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "short unique column name; never Company or Website",
                    },
                    "prompt_template": {
                        "type": "string",
                        "description": "one focused research question; may embed {{Column}} refs",
                    },
                    "output_type": {
                        "type": "string",
                        "enum": ["text", "number", "boolean", "enum", "url", "date"],
                    },
                    "enum_options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "allowed values; non-empty only when output_type is enum",
                    },
                    "source_scope": {
                        "type": "string",
                        "enum": ["company_site", "web", "news", "people"],
                    },
                    "depth": {"type": "string", "enum": ["standard", "deep"]},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "column names the template references "
                        "(recomputed server-side)",
                    },
                },
                "required": [
                    "name",
                    "prompt_template",
                    "output_type",
                    "enum_options",
                    "source_scope",
                    "depth",
                    "depends_on",
                ],
                "additionalProperties": False,
            },
        },
        "question": {
            "type": "string",
            "description": "the single clarifying question; set only for needs_clarification",
        },
        "reason": {
            "type": "string",
            "description": "why the request is unsupported; set only for unsupported",
        },
    },
    "required": ["intent", "columns", "question", "reason"],
    "additionalProperties": False,
}


class RawPlannedColumn(BaseModel):
    """One column exactly as the planner returned it — claims, not truth.

    ``depends_on`` in particular is recomputed from ``prompt_template``
    server-side; the planner's claim is never trusted.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    output_type: OutputType
    enum_options: list[str] = []
    source_scope: SourceScope
    depth: Depth = "standard"
    depends_on: list[str] = []


class RawPlanResponse(BaseModel):
    """The planner's JSON envelope — shape- and intent-validated only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: OutputIntent
    columns: list[RawPlannedColumn] = Field(default_factory=list, max_length=MAX_PLAN_COLUMNS)
    question: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _intent_consistency(self) -> "RawPlanResponse":
        if self.intent == "add_columns":
            if not self.columns:
                raise ValueError("add_columns needs at least one column")
            return self
        if self.columns:
            raise ValueError(f"{self.intent} cannot carry columns")
        if self.intent == "needs_clarification" and not self.question:
            raise ValueError("needs_clarification needs exactly one question")
        if self.intent == "unsupported" and not self.reason:
            raise ValueError("unsupported needs a reason")
        return self


class PlanColumn(BaseModel):
    """A validated planned column in the preview spec.

    ``depends_on`` is the server-recomputed value and ``strategy`` is
    always ``"research"`` (D4) — the planner does not choose it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    prompt_template: str
    output_type: OutputType
    enum_options: list[str] = []
    source_scope: SourceScope
    depth: Depth = "standard"
    strategy: Strategy = "research"
    depends_on: list[str] = []


class PlanResult(BaseModel):
    """The planner's answer — a stateless preview the user can edit.

    ``intent == "failed"`` is our structured failure: the planner's output
    was unparseable or domain-invalid even after the one repair retry.
    ``estimated_cost_usd`` is a labelled ESTIMATE computed from documented
    per-request pricing — never a quote. ``mock`` carries the LLM result's
    provenance.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Literal["add_columns", "needs_clarification", "unsupported", "failed"]
    columns: list[PlanColumn] = []
    question: str | None = None
    reason: str | None = None
    estimated_cost_usd: float | None = None
    mock: bool = False


class SheetColumn(BaseModel):
    """What the planner may see of one existing column: name, output type
    and up to a few sample values — passed to the model as DATA, never as
    instructions (plan §4.4). Samples are truncated at serialization time
    in :mod:`app.planner.prompts`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    output_type: OutputType
    samples: list[str] = []
