"""Run dispatch loop (plan §4.3).

One asyncio loop per run: up to ``sheet_max_concurrency`` workers behind a
token-bucket limiter at ``exa_max_qps`` and a per-run cost cap.

- IDENTITY FIRST: newly imported rows (``identity_status == "pending"``)
  with queued cells are resolved through ``resolve_identity`` — one
  primary ``identity_query`` plus at most one
  ``identity_fallback_query`` — before any cell on the row runs.
  Unresolved rows keep their identity status and EVERY cell on the row
  becomes blocked with the reason; no cell on such a row reaches Exa.
- Claiming is atomic through ``repo.claim_cell``. ``queued -> blocked``
  when an upstream cell errored or is unavailable; dependencies are
  evaluated per row against the column DAG.
- ``exa_invalid_key`` / ``exa_credits_exhausted`` (401/402) stop the whole
  run and return its queued cells to idle — never N identical errors.
- Graceful shutdown (:meth:`SheetRunner.aclose`) cancels workers and
  requeues their running cells, pairing with ``repo.requeue_orphans`` at
  startup.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.engine.budget import (
    ESTIMATED_CELL_COST_USD,
    ESTIMATED_SEARCH_COST_USD,
    Budget,
)
from app.engine.cell_exec import execute_cell, result_cost
from app.engine.events import HubRegistry
from app.engine.limiter import RateLimiter
from app.errors.provider_error import ProviderError
from app.normalize.domain import DomainError, NormalizedDomain, normalize_domain
from app.normalize.report import hit_source
from app.providers.types import ExaProvider, ExaSearchRequest, ExaSearchResult
from app.research.orchestrator import IdentityResolution, resolve_identity
from app.research.queries import identity_fallback_query, identity_query
from app.schemas.report import CompanyIdentity, Field
from app.sheets import repo
from app.sheets.models import Cell, CellValue, Column, Row, Run, RunScope

logger = logging.getLogger("outmate.engine")

STOP_CODES = frozenset({"exa_invalid_key", "exa_credits_exhausted"})

_AMBIGUOUS_NOTES = frozenset(
    {
        "supplied name and domain do not agree",
        "top name-matching hits point to more than one host",
    }
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class _RunContext:
    """In-memory view of one run's grid state.

    The DB stays the source of truth for claims and finished writes; this
    map mirrors cell status/result for dependency and identity checks.
    """

    run: Run
    sheet_id: str
    budget: Budget
    rows: dict[str, Row]
    columns: dict[str, Column]
    columns_by_name: dict[str, Column]
    cells: dict[tuple[str, str], Cell]
    pending: set[tuple[str, str]] = field(default_factory=set)
    running: dict[asyncio.Task[None], tuple[str, str]] = field(default_factory=dict)
    stop_reason: str | None = None
    cancel_requested: bool = False


class SheetRunner:
    """Dispatches queued cells for one sheet run at a time."""

    def __init__(
        self,
        db_path: str,
        exa: ExaProvider,
        *,
        hubs: HubRegistry | None = None,
        max_concurrency: int = 4,
        qps: float = 5.0,
        default_cap: float | None = 1.00,
        limiter: RateLimiter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db_path
        self._exa = exa
        self._hubs = hubs
        self._max_concurrency = max(1, max_concurrency)
        self._default_cap = default_cap
        self._limiter = limiter or RateLimiter(qps)
        self._clock = clock or _utcnow
        self._active: dict[str, _RunContext] = {}
        self._closing = False

    async def run(
        self,
        sheet_id: str,
        scope: RunScope = "empty",
        *,
        cap: float | None = None,
    ) -> Run:
        """Queue the scope's cells and dispatch them to completion.

        Returns the final run row. Early exits (cancel, cost cap,
        provider 401/402) flag the run cancelled with the reason and
        return its still-queued cells to idle.
        """
        cap_value = cap if cap is not None else self._default_cap
        run = await repo.create_run(self._db, sheet_id, cap=cap_value, reason=f"scope={scope}")
        await repo.queue_cells(self._db, sheet_id, scope, run_id=run.id)
        snap = await repo.snapshot(self._db, sheet_id)
        if snap is None:
            raise LookupError(f"no sheet {sheet_id!r}")
        ctx = _RunContext(
            run=run,
            sheet_id=sheet_id,
            budget=Budget(cap=cap_value),
            rows={row.id: row for row in snap.rows},
            columns={column.id: column for column in snap.columns},
            columns_by_name={column.name: column for column in snap.columns},
            cells={(cell.row_id, cell.column_id): cell for cell in snap.cells},
        )
        ctx.pending = {
            key
            for key, cell in ctx.cells.items()
            if cell.status == "queued" and cell.run_id == run.id
        }
        self._publish_run(ctx, "started")
        self._active[run.id] = ctx
        try:
            await self._resolve_pending_rows(ctx)
            await self._dispatch(ctx)
        finally:
            self._active.pop(run.id, None)
            if ctx.stop_reason or ctx.cancel_requested:
                await repo.stop_run(
                    self._db,
                    run.id,
                    ctx.stop_reason or "cancelled",
                )
                self._publish_run(ctx, "stopped")
            else:
                self._publish_run(ctx, "finished")
        final = await repo.get_run(self._db, run.id)
        return final if final is not None else run

    async def cancel(self, run_id: str) -> bool:
        """Ask a live run to stop: queued cells return to idle once the
        in-flight workers settle."""
        ctx = self._active.get(run_id)
        if ctx is None:
            return False
        ctx.cancel_requested = True
        return True

    async def aclose(self) -> None:
        """Graceful shutdown: cancel in-flight workers and requeue their
        cells (``running -> queued``), pairing with ``requeue_orphans`` at
        startup. Queued cells stay queued for the next start."""
        self._closing = True
        contexts = list(self._active.values())
        tasks = [task for ctx in contexts for task in ctx.running]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for ctx in contexts:
            keys = [key for key, cell in ctx.cells.items() if cell.status == "running"]
            if keys:
                await repo.requeue_cells(self._db, keys)

    # -- identity phase ----------------------------------------------------

    async def _resolve_pending_rows(self, ctx: _RunContext) -> None:
        """Resolve identity for pending rows that have queued cells —
        before any cell dispatch."""
        queued_rows = {row_id for row_id, _ in ctx.pending}
        pending = [
            row
            for row in sorted(ctx.rows.values(), key=lambda r: r.position)
            if row.identity_status == "pending" and row.id in queued_rows
        ]
        for row in pending:
            if self._closing or ctx.stop_reason or ctx.cancel_requested:
                return
            await self._resolve_one_row(ctx, row)

    async def _search(self, ctx: _RunContext, request: ExaSearchRequest) -> ExaSearchResult | None:
        """Rate-limited, budget-checked Exa call; ``None`` when the cap
        stops the run. ``ProviderError`` propagates to the caller."""
        if not ctx.budget.affords(ESTIMATED_SEARCH_COST_USD):
            ctx.stop_reason = "cost_cap"
            return None
        await self._limiter.acquire()
        result = await self._exa.search(request)
        cost = result_cost(result)
        ctx.budget.record(cost)
        if cost:
            await repo.add_run_spend(self._db, ctx.run.id, cost)
        return result

    async def _resolve_one_row(self, ctx: _RunContext, row: Row) -> None:
        domain: NormalizedDomain | None = None
        if row.input_domain:
            try:
                domain = normalize_domain(row.input_domain)
            except DomainError:
                domain = None  # import validates domains; defensive only
        try:
            result = await self._search(ctx, identity_query(row.input_name, domain))
            if result is None:
                return
            resolution = resolve_identity(row.input_name, domain, result, self._clock)
            if (
                not resolution.resolved
                and domain is not None
                and resolution.note == "no evidence for this domain"
            ):
                result = await self._search(ctx, identity_fallback_query(row.input_name, domain))
                if result is None:
                    return
                resolution = resolve_identity(row.input_name, domain, result, self._clock)
        except ProviderError as exc:
            if exc.code in STOP_CODES:
                ctx.stop_reason = exc.code
                return
            await repo.update_row_identity(self._db, row.id, identity_status="error")
            row.identity_status = "error"
            await self._block_row_cells(ctx, row, f"identity check failed: {exc.message}")
            return

        if resolution.resolved and resolution.domain_key is not None:
            identity = _identity_model(resolution, result, self._clock)
            await repo.update_row_identity(
                self._db,
                row.id,
                identity_status="resolved",
                resolved_name=resolution.name,
                resolved_domain=resolution.domain_key,
                identity=identity,
            )
            row.identity_status = "resolved"
            row.resolved_name = resolution.name
            row.resolved_domain = resolution.domain_key
            row.identity = identity
            self._publish_row(ctx, row)
            return

        status: Literal["unresolved", "ambiguous"] = (
            "ambiguous" if resolution.note in _AMBIGUOUS_NOTES else "unresolved"
        )
        identity = _identity_model(resolution, result, self._clock)
        await repo.update_row_identity(self._db, row.id, identity_status=status, identity=identity)
        row.identity_status = status
        row.identity = identity
        self._publish_row(ctx, row)
        await self._block_row_cells(ctx, row, resolution.note or "identity unresolved")

    async def _block_row_cells(self, ctx: _RunContext, row: Row, reason: str) -> None:
        for key, cell in list(ctx.cells.items()):
            if key[0] != row.id or cell.status == "blocked":
                continue
            blocked = await repo.mark_blocked(self._db, key[0], key[1], reason)
            if blocked is not None:
                ctx.cells[key] = blocked
                ctx.pending.discard(key)
                self._publish_cell(ctx, blocked)

    # -- dispatch phase -----------------------------------------------------

    async def _dispatch(self, ctx: _RunContext) -> None:
        while ctx.pending or ctx.running:
            if self._closing or ctx.stop_reason or ctx.cancel_requested:
                break
            progress = False
            for key in sorted(ctx.pending, key=lambda k: self._cell_order(ctx, k)):
                if len(ctx.running) >= self._max_concurrency:
                    break
                decision, reason = self._dep_state(ctx, key)
                if decision == "wait":
                    continue
                ctx.pending.discard(key)
                if decision == "blocked":
                    await self._block_cell(ctx, key, reason or "upstream failed")
                    progress = True
                    continue
                row = ctx.rows[key[0]]
                if row.identity_status != "resolved":
                    await self._block_cell(ctx, key, "identity not resolved")
                    progress = True
                    continue
                if not ctx.budget.affords(ESTIMATED_CELL_COST_USD):
                    ctx.pending.add(key)  # stays queued for a later run
                    ctx.stop_reason = "cost_cap"
                    break
                task = asyncio.create_task(self._cell_worker(ctx, key))
                ctx.running[task] = key
                progress = True
            if not ctx.running:
                if (
                    ctx.pending
                    and not progress
                    and not (ctx.stop_reason or ctx.cancel_requested or self._closing)
                ):
                    # No worker can ever satisfy these dependencies —
                    # block rather than hang.
                    for key in list(ctx.pending):
                        ctx.pending.discard(key)
                        await self._block_cell(ctx, key, "upstream never completed")
                continue
            done, _ = await asyncio.wait(set(ctx.running), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                ctx.running.pop(task, None)
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.error("cell worker failed: %s", exc)
        # Let in-flight workers record their outcomes before the run's
        # early exit returns its queued cells to idle.
        if ctx.running:
            await asyncio.gather(*ctx.running, return_exceptions=True)
            ctx.running.clear()

    @staticmethod
    def _cell_order(ctx: _RunContext, key: tuple[str, str]) -> tuple[int, int]:
        """Deterministic dispatch order: row position, then column position."""
        row = ctx.rows.get(key[0])
        column = ctx.columns.get(key[1])
        return (row.position if row else 0, column.position if column else 0)

    async def _block_cell(self, ctx: _RunContext, key: tuple[str, str], reason: str) -> None:
        blocked = await repo.mark_blocked(self._db, key[0], key[1], reason)
        if blocked is not None:
            ctx.cells[key] = blocked
            self._publish_cell(ctx, blocked)

    def _dep_state(
        self, ctx: _RunContext, key: tuple[str, str]
    ) -> tuple[Literal["ready", "wait", "blocked"], str | None]:
        """A queued cell is ready only when every upstream cell holds a
        usable value; a failed or unavailable upstream blocks it."""
        row_id, column_id = key
        column = ctx.columns.get(column_id)
        if column is None:
            return "blocked", "column no longer exists"
        for dep_name in column.depends_on:
            dep_column = ctx.columns_by_name.get(dep_name)
            if dep_column is None:
                return "blocked", f"upstream column {dep_name!r} no longer exists"
            dep_cell = ctx.cells.get((row_id, dep_column.id))
            if dep_cell is None:
                return "blocked", f"upstream column {dep_name!r} has no cell"
            if dep_cell.status in ("queued", "running"):
                return "wait", None
            if dep_cell.status in ("error", "blocked"):
                return "blocked", f"upstream column {dep_name!r} {dep_cell.status}"
            if dep_cell.status == "idle":
                return "blocked", f"upstream column {dep_name!r} did not run"
            dep_field = dep_cell.result
            if dep_field is None or dep_field.status == "unavailable" or dep_field.value is None:
                return "blocked", f"upstream column {dep_name!r} is unavailable"
        return "ready", None

    async def _cell_worker(self, ctx: _RunContext, key: tuple[str, str]) -> None:
        row_id, column_id = key
        row = ctx.rows[row_id]
        column = ctx.columns[column_id]
        try:
            await self._limiter.acquire()
            claimed = await repo.claim_cell(self._db, row_id, column_id, ctx.run.id)
            if claimed is None:
                return  # another claim won or the cell was requeued
            ctx.cells[key] = claimed
            self._publish_cell(ctx, claimed)
            upstream: dict[str, Field[CellValue] | None] = {}
            for dep_name in column.depends_on:
                dep_column = ctx.columns_by_name.get(dep_name)
                dep_cell = (
                    ctx.cells.get((row_id, dep_column.id)) if dep_column is not None else None
                )
                upstream[dep_name] = dep_cell.result if dep_cell is not None else None
            outcome = await execute_cell(
                exa=self._exa,
                row=row,
                column=column,
                upstream=upstream,
                clock=self._clock,
            )
            cell = await repo.finish_cell(
                self._db,
                row_id,
                column_id,
                status=outcome.status,
                result=outcome.field,
                cost=outcome.cost,
                rendered_prompt=outcome.rendered_prompt,
                error=outcome.error,
                run_id=ctx.run.id,
                column_version=column.version,
            )
            ctx.cells[key] = cell
            ctx.budget.record(outcome.cost)
            if outcome.cost:
                await repo.add_run_spend(self._db, ctx.run.id, outcome.cost)
            self._publish_cell(ctx, cell)
            if outcome.error_code in STOP_CODES:
                ctx.stop_reason = outcome.error_code
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cell worker failed at %s", key)
            try:
                cell = await repo.finish_cell(
                    self._db,
                    row_id,
                    column_id,
                    status="error",
                    error="internal engine error",
                    run_id=ctx.run.id,
                )
                ctx.cells[key] = cell
                self._publish_cell(ctx, cell)
            except Exception:
                logger.exception("could not mark cell error at %s", key)

    # -- events --------------------------------------------------------------

    def _publish_cell(self, ctx: _RunContext, cell: Cell) -> None:
        if self._hubs is None:
            return
        self._hubs.for_sheet(ctx.sheet_id).publish(
            {
                "type": "cell",
                "row_id": cell.row_id,
                "column_id": cell.column_id,
                "status": cell.status,
                "revision": cell.revision,
                "stale": cell.stale,
            }
        )

    def _publish_row(self, ctx: _RunContext, row: Row) -> None:
        if self._hubs is None:
            return
        self._hubs.for_sheet(ctx.sheet_id).publish(
            {
                "type": "row",
                "row_id": row.id,
                "identity_status": row.identity_status,
                "resolved_name": row.resolved_name,
                "resolved_domain": row.resolved_domain,
            }
        )

    def _publish_run(self, ctx: _RunContext, status: str) -> None:
        if self._hubs is None:
            return
        self._hubs.for_sheet(ctx.sheet_id).publish(
            {
                "type": "run",
                "run_id": ctx.run.id,
                "status": status,
                "reason": ctx.stop_reason,
            }
        )


def _identity_model(
    resolution: IdentityResolution,
    result: ExaSearchResult,
    clock: Callable[[], datetime],
) -> CompanyIdentity:
    """The ``CompanyIdentity`` stored on the row — mirrors the
    orchestrator's identity fields (verified needs a URL-bearing source;
    the user's input alone is never a source)."""
    if resolution.resolved:
        sources = [hit_source(hit, result, clock) for hit in resolution.supporting]
        verified = any(source.url is not None for source in sources)
        status: Literal["verified", "unavailable"] = "verified" if verified else "unavailable"
        return CompanyIdentity(
            resolved=True,
            company_name=Field[str](
                value=resolution.name,
                status=status,
                sources=sources,
                note=resolution.name_note,
            ),
            domain=Field[str](value=resolution.domain_key, status=status, sources=sources),
            note=resolution.note,
        )
    candidates = [hit_source(hit, result, clock) for hit in resolution.candidates]
    return CompanyIdentity(
        resolved=False,
        company_name=Field[str](value=None, status="unavailable", sources=candidates),
        domain=Field[str](value=None, status="unavailable", sources=candidates),
        note=resolution.note,
    )
