"""API contracts for the sheets endpoints (plan §4.5).

Request bodies are validated here; response bodies reuse the persistence
models in :mod:`app.sheets.models` unchanged — ``Field[...]``,
``Source`` and ``CompanyIdentity`` inside them come from the existing
report contract, imported never copied. The planner preview reuses
:class:`app.planner.spec.PlanResult` as its wire shape.

This API has NO authentication — run it locally or behind a trusted
boundary only (plan §4.5).
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.sheets.models import (
    Cell,
    CellRevision,
    Column,
    Depth,
    OutputType,
    Row,
    Run,
    RunScope,
    Sheet,
    SourceScope,
)


class CreateSheetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class SheetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheets: list[Sheet]


class ImportRow(BaseModel):
    """One pasted row. At least one of ``name``/``domain`` is required —
    identity resolution has nothing to work with otherwise."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=300)
    domain: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _needs_a_hint(self) -> "ImportRow":
        if not (self.name or self.domain):
            raise ValueError("a row needs a name, a domain, or both")
        return self


class ImportRowsRequest(BaseModel):
    """Paste import — at most 100 rows per call (plan §4.5)."""

    model_config = ConfigDict(extra="forbid")

    rows: list[ImportRow] = Field(min_length=1, max_length=100)


class ImportRowsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[Row]
    created: int
    skipped: int


class GenerateRowsRequest(BaseModel):
    """List-building request (P9a): the company-finding query drives ONE
    Exa ``category="company"`` search. ``max_results`` stays inside the
    shared ≤5-per-call ``ExaSearchRequest`` cap — finding more companies
    means repeated calls. ``cap`` refuses the call outright when even the
    labelled estimate would exceed it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=5, ge=1, le=5)
    cap: float | None = Field(default=None, gt=0)


class GenerateRowsResponse(BaseModel):
    """Honest accounting: ``skipped`` counts hits that did not become
    rows (missing/invalid URL, duplicate domain, sheet cap);
    ``estimated_cost_usd`` is the labelled estimate; ``cost_usd`` is the
    provider-reported spend only — ``None`` when the result carried no
    usage; ``mock`` is provenance."""

    model_config = ConfigDict(extra="forbid")

    rows: list[Row]
    created: int
    skipped: int
    estimated_cost_usd: float
    cost_usd: float | None
    mock: bool


class PlanRequest(BaseModel):
    """One natural-language column request for the stateless planner."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=4000)


class ColumnSpec(BaseModel):
    """The editable column spec accepted by create/update — the same
    fields the planner preview carries. ``strategy`` and ``depends_on``
    are server-owned (always ``"research"``, always recomputed from
    ``prompt_template``) and are never accepted from a client."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    prompt_template: str = Field(min_length=1, max_length=4000)
    output_type: OutputType
    enum_options: list[str] = Field(default_factory=list, max_length=50)
    source_scope: SourceScope
    depth: Depth = "standard"


class CreateColumnsRequest(BaseModel):
    """Atomic column batch: every spec is validated against the sheet and
    its siblings before any column is written. ``run`` optionally starts
    a run of the newly idle cells in the same call."""

    model_config = ConfigDict(extra="forbid")

    columns: list[ColumnSpec] = Field(min_length=1, max_length=30)
    run: bool = False


class CreateColumnsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[Column]
    run: Run | None = None


class UpdateColumnRequest(ColumnSpec):
    """Full-column update (PATCH): the whole spec is rewritten and the
    column version bumps — a prompt edit marks cells stale, never wipes."""


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: RunScope = "empty"
    confirm_overwrite: bool = False
    cap: float | None = Field(default=None, gt=0)


class CellDetail(BaseModel):
    """One cell plus its append-only revision history. ``cell.result`` is
    the ``Field`` — value, status and sources live inside it."""

    model_config = ConfigDict(extra="forbid")

    cell: Cell
    revisions: list[CellRevision]
