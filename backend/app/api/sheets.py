"""Sheets HTTP API (plan §4.5) — every route under ``/api``.

Thin layer over :class:`app.sheets.service.SheetService` (business rules),
:class:`app.engine.runner.SheetRunner` (background runs) and
:class:`app.engine.events.HubRegistry` (SSE fan-out). Domain errors arrive
as ``SheetError`` and map to the shared ``ErrorResponse`` shape in
``main.py``; ``not_found`` → 404, name/reference/run conflicts → 409,
limit and validation errors → 422.

The events stream is SSE (``sse-starlette``): the client subscribes
FIRST, then fetches the snapshot, then merges per cell by revision (last
write wins) — cell events are batched into one frame about every 50 ms
and there is no replay. This API has no authentication — run it locally
or behind a trusted boundary only.
"""

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sse_starlette.sse import EventSourceResponse

from app.api.research import Providers
from app.config import Settings
from app.engine.events import EventHub, HubRegistry
from app.engine.runner import SheetRunner
from app.errors.llm_error import LlmError
from app.errors.provider_error import ResearchUnavailable
from app.errors.sheet_error import SheetError
from app.planner.planner import Planner
from app.planner.prompts import MAX_SAMPLE_VALUES
from app.planner.spec import PlanResult
from app.schemas.sheet import (
    CellDetail,
    ColumnSpec,
    CreateColumnsRequest,
    CreateColumnsResponse,
    CreateRunRequest,
    CreateSheetRequest,
    ImportRowsRequest,
    ImportRowsResponse,
    PlanRequest,
    SheetListResponse,
    UpdateColumnRequest,
)
from app.sheets.models import Column, RowInput, Run, Sheet, SheetSnapshot
from app.sheets.service import ColumnSpec as ServiceColumnSpec
from app.sheets.service import SheetService

_SSE_BATCH_SECONDS = 0.05

_FORMULA_PREFIXES = ("=", "+", "-", "@")
"""CSV cell values starting with these are single-quote-prefixed — the
formula-injection guard (plan §4.5)."""


@dataclass
class SheetDeps:
    """The sheets wiring created in the app lifespan, reached via
    ``app.state`` — tests substitute pieces in place."""

    service: SheetService
    runner: SheetRunner | None
    hubs: HubRegistry
    providers: Providers
    settings: Settings


def get_sheet_deps(request: Request) -> SheetDeps:
    app = request.app
    return SheetDeps(
        service=app.state.sheet_service,
        runner=app.state.runner,
        hubs=app.state.hubs,
        providers=app.state.providers,
        settings=app.state.settings,
    )


Deps = Annotated[SheetDeps, Depends(get_sheet_deps)]

router = APIRouter()


def _require_runner(deps: SheetDeps) -> SheetRunner:
    """Cell runs need Exa; an unconfigured key is a 503, never a mock."""
    if deps.runner is None:
        raise ResearchUnavailable(
            code="exa_not_configured",
            message="the sheet engine is unavailable: Exa is not configured",
        )
    return deps.runner


def _require_planner(deps: SheetDeps) -> Planner:
    """The plan preview needs Groq; missing key is a 503, never a mock."""
    llm = deps.providers.groq
    if llm is None:
        raise LlmError(
            provider="Groq",
            code="groq_not_configured",
            message="the column planner is unavailable: Groq is not configured",
            retryable=False,
        )
    return Planner(
        llm,
        model=deps.settings.planner_model,
        max_columns=deps.settings.sheet_max_columns,
    )


def _service_spec(spec: ColumnSpec) -> ServiceColumnSpec:
    return ServiceColumnSpec(
        name=spec.name,
        prompt_template=spec.prompt_template,
        output_type=spec.output_type,
        source_scope=spec.source_scope,
        depth=spec.depth,
        enum_options=tuple(spec.enum_options),
    )


# --- sheets ------------------------------------------------------------------


@router.post("/api/sheets", status_code=201)
async def create_sheet(body: CreateSheetRequest, deps: Deps) -> Sheet:
    return await deps.service.create_sheet(body.name)


@router.get("/api/sheets")
async def list_sheets(deps: Deps) -> SheetListResponse:
    return SheetListResponse(sheets=await deps.service.list_sheets())


@router.get("/api/sheets/{sheet_id}/snapshot")
async def snapshot(sheet_id: str, deps: Deps) -> SheetSnapshot:
    return await deps.service.snapshot(sheet_id)


@router.delete("/api/sheets/{sheet_id}", status_code=204)
async def delete_sheet(sheet_id: str, deps: Deps) -> Response:
    await deps.service.delete_sheet(sheet_id)
    deps.hubs.drop(sheet_id)
    return Response(status_code=204)


# --- rows ---------------------------------------------------------------------


@router.post("/api/sheets/{sheet_id}/rows", status_code=201)
async def import_rows(sheet_id: str, body: ImportRowsRequest, deps: Deps) -> ImportRowsResponse:
    created = await deps.service.import_rows(
        sheet_id,
        [RowInput(input_name=row.name, input_domain=row.domain) for row in body.rows],
    )
    return ImportRowsResponse(
        rows=created,
        created=len(created),
        skipped=len(body.rows) - len(created),
    )


# --- planner ------------------------------------------------------------------


@router.post("/api/sheets/{sheet_id}/plan")
async def plan(sheet_id: str, body: PlanRequest, deps: Deps) -> PlanResult:
    """Stateless planner preview — re-validate everything on apply."""
    snap = await deps.service.snapshot(sheet_id)
    planner = _require_planner(deps)
    return await planner.plan(
        body.prompt,
        columns=snap.columns,
        samples=_column_samples(snap),
        row_count=len(snap.rows),
        pending_identity_rows=sum(1 for row in snap.rows if row.identity_status == "pending"),
    )


def _column_samples(snap: SheetSnapshot) -> dict[str, list[str]]:
    """Up to ``MAX_SAMPLE_VALUES`` stringified cell values per column —
    shown to the planner as DATA, never instructions."""
    by_column: dict[str, list[str]] = {}
    for cell in snap.cells:
        if cell.result is None or cell.result.value is None:
            continue
        samples = by_column.setdefault(cell.column_id, [])
        if len(samples) < MAX_SAMPLE_VALUES:
            samples.append(str(cell.result.value))
    return {column.name: by_column.get(column.id, []) for column in snap.columns}


# --- columns --------------------------------------------------------------------


@router.post("/api/sheets/{sheet_id}/columns", status_code=201)
async def create_columns(
    sheet_id: str, body: CreateColumnsRequest, deps: Deps
) -> CreateColumnsResponse:
    runner = _require_runner(deps) if body.run else None
    if runner is not None and runner.active_run_for(sheet_id) is not None:
        raise SheetError(
            code="run_active",
            message="a run is already active for this sheet",
        )
    columns = await deps.service.add_columns(
        sheet_id, [_service_spec(spec) for spec in body.columns]
    )
    run = await runner.start(sheet_id, "empty") if runner is not None else None
    if runner is not None and run is None:
        raise SheetError(
            code="run_active",
            message="a run is already active for this sheet",
        )
    return CreateColumnsResponse(columns=columns, run=run)


@router.patch("/api/sheets/{sheet_id}/columns/{column_id}")
async def update_column(
    sheet_id: str, column_id: str, body: UpdateColumnRequest, deps: Deps
) -> Column:
    await deps.service.column_for_sheet(sheet_id, column_id)
    return await deps.service.edit_column(
        column_id,
        name=body.name,
        prompt_template=body.prompt_template,
        output_type=body.output_type,
        source_scope=body.source_scope,
        depth=body.depth,
        enum_options=body.enum_options,
    )


@router.delete("/api/sheets/{sheet_id}/columns/{column_id}", status_code=204)
async def delete_column(sheet_id: str, column_id: str, deps: Deps) -> Response:
    await deps.service.column_for_sheet(sheet_id, column_id)
    await deps.service.delete_column(column_id)
    return Response(status_code=204)


# --- runs ------------------------------------------------------------------------


@router.post("/api/sheets/{sheet_id}/runs", status_code=202)
async def create_run(sheet_id: str, body: CreateRunRequest, deps: Deps) -> Run:
    if await deps.service.get_sheet(sheet_id) is None:
        raise SheetError(code="not_found", message="sheet not found")
    if body.scope == "all" and not body.confirm_overwrite:
        raise SheetError(
            code="confirm_required",
            message="scope 'all' overwrites completed cells; pass confirm_overwrite=true",
        )
    runner = _require_runner(deps)
    run = await runner.start(sheet_id, body.scope, cap=body.cap)
    if run is None:
        raise SheetError(
            code="run_active",
            message="a run is already active for this sheet",
        )
    return run


@router.post("/api/sheets/{sheet_id}/runs/{run_id}/cancel")
async def cancel_run(sheet_id: str, run_id: str, deps: Deps) -> Run:
    run = await deps.service.run_for_sheet(sheet_id, run_id)
    if deps.runner is not None and await deps.runner.cancel(run_id):
        return run  # queued cells return to idle as in-flight cells settle
    # Not active in this process (finished, or queued cells orphaned by a
    # restart): flag the row and unqueue its cells directly.
    return await deps.service.cancel_run(run_id)


# --- cells -----------------------------------------------------------------------


@router.get("/api/sheets/{sheet_id}/cells/{row_id}/{column_id}")
async def cell_detail(sheet_id: str, row_id: str, column_id: str, deps: Deps) -> CellDetail:
    cell, revisions = await deps.service.cell_detail(sheet_id, row_id, column_id)
    return CellDetail(cell=cell, revisions=revisions)


# --- export ----------------------------------------------------------------------


def _csv_safe(text: str) -> str:
    """Formula-injection guard: prefix values opening with =, +, -, @."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _cell_text(cell_value: object) -> str:
    if cell_value is None:
        return ""
    if isinstance(cell_value, bool):
        return "true" if cell_value else "false"
    return str(cell_value)


def _snapshot_csv(snap: SheetSnapshot) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Company", "Domain", *[column.name for column in snap.columns]])
    cells = {(cell.row_id, cell.column_id): cell for cell in snap.cells}
    for row in snap.rows:
        line = [
            _csv_safe(row.resolved_name or row.input_name or ""),
            _csv_safe(row.resolved_domain or row.input_domain or ""),
        ]
        for column in snap.columns:
            cell = cells.get((row.id, column.id))
            value = cell.result.value if cell is not None and cell.result else None
            line.append(_csv_safe(_cell_text(value)))
        writer.writerow(line)
    return buffer.getvalue()


@router.get("/api/sheets/{sheet_id}/export.csv")
async def export_csv(sheet_id: str, deps: Deps) -> Response:
    snap = await deps.service.snapshot(sheet_id)
    return Response(
        content=_snapshot_csv(snap),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="sheet-{sheet_id}.csv"'},
    )


# --- events (SSE) ----------------------------------------------------------------


async def _event_stream(hub: EventHub) -> AsyncIterator[dict[str, str]]:
    """Forward hub events to one SSE client, batched ~every 50 ms.

    Each frame's ``data`` is a JSON array of event objects
    (``{"type": "cell"|"row"|"run", ...}``). The client merges per cell by
    revision — last write wins — so no replay or ordering logic exists.
    Disconnect cleanup is driven by ``EventSourceResponse`` itself, which
    cancels this generator; the ``finally`` always unsubscribes.
    """
    queue = hub.subscribe()
    try:
        while True:
            batch = [await queue.get()]
            deadline = asyncio.get_running_loop().time() + _SSE_BATCH_SECONDS
            while True:
                wait = deadline - asyncio.get_running_loop().time()
                if wait <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=wait))
                except TimeoutError:
                    break
            yield {"data": json.dumps(batch)}
    finally:
        hub.unsubscribe(queue)


@router.get("/api/sheets/{sheet_id}/events")
async def events(sheet_id: str, deps: Deps) -> EventSourceResponse:
    """Subscribe to the sheet's event stream. Clients subscribe FIRST,
    then fetch ``/snapshot`` and merge — no replay (plan §4.5)."""
    if await deps.service.get_sheet(sheet_id) is None:
        raise SheetError(code="not_found", message="sheet not found")
    return EventSourceResponse(_event_stream(deps.hubs.for_sheet(sheet_id)))
