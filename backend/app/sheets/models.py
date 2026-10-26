"""Pydantic row models for the sheets store (plan §4.1).

Models mirror the tables in :mod:`app.sheets.db`: enum-like columns are
``Literal`` types, flags stored as INTEGER 0/1 are ``bool``, and the JSON
text columns are parsed into real structures at the model boundary —
``depends_on``/``enum_options`` become ``list[str]``, ``identity_json``
becomes :class:`CompanyIdentity` and ``result_json`` becomes the existing
``Field`` contract (imported, never redefined).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.report import CompanyIdentity, Field

CellValue = str | int | float | bool
"""Union of legal cell values; each cell stores one ``Field[CellValue]``."""

IdentityStatus = Literal["pending", "resolved", "unresolved", "ambiguous", "error"]
OutputType = Literal["text", "number", "boolean", "enum", "url", "date"]
Strategy = Literal["research"]
SourceScope = Literal["company_site", "web", "news", "people"]
Depth = Literal["standard", "deep"]
CellStatus = Literal["idle", "queued", "running", "done", "error", "blocked"]
RunScope = Literal["empty", "stale", "error", "all"]


class Sheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class RowInput(BaseModel):
    """One row to insert (paste import). Positions are assigned on write."""

    model_config = ConfigDict(extra="forbid")

    input_name: str | None = None
    input_domain: str | None = None


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    sheet_id: str
    input_name: str | None = None
    input_domain: str | None = None
    position: int
    identity_status: IdentityStatus = "pending"
    resolved_name: str | None = None
    resolved_domain: str | None = None
    identity: CompanyIdentity | None = None
    deleted: bool = False
    created_at: datetime
    updated_at: datetime


class Column(BaseModel):
    """A generated column. ``depends_on`` is always derived from
    ``prompt_template`` by the caller; the repo stores what it is given."""

    model_config = ConfigDict(extra="forbid")

    id: str
    sheet_id: str
    name: str
    prompt_template: str
    output_type: OutputType
    enum_options: list[str] | None = None
    strategy: Strategy = "research"
    source_scope: SourceScope
    depth: Depth = "standard"
    depends_on: list[str] = []
    version: int = 1
    position: int
    deleted: bool = False
    created_at: datetime
    updated_at: datetime


class Cell(BaseModel):
    """One (row, column) unit of work. The cells table is the job queue:
    ``idle → queued → running → done | error``, or ``blocked`` while an
    upstream cell cannot supply a value."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    column_id: str
    status: CellStatus = "idle"
    result: Field[CellValue] | None = None
    column_version: int = 1
    stale: bool = False
    revision: int = 0
    cost: float | None = None
    rendered_prompt: str | None = None
    error: str | None = None
    run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CellRevision(BaseModel):
    """Append-only history entry — one row per completed cell write."""

    model_config = ConfigDict(extra="forbid")

    id: int
    row_id: str
    column_id: str
    revision: int
    result: Field[CellValue] | None = None
    error: str | None = None
    created_at: datetime


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    sheet_id: str
    cap: float | None = None
    spent: float = 0.0
    cancelled: bool = False
    reason: str | None = None
    created_at: datetime


class SheetSnapshot(BaseModel):
    """Full sheet state for one grid load: sheet, visible rows and columns,
    every cell between them, and the latest run."""

    model_config = ConfigDict(extra="forbid")

    sheet: Sheet
    rows: list[Row]
    columns: list[Column]
    cells: list[Cell]
    latest_run: Run | None = None
