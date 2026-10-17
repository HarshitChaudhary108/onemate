"""Runner dispatch tests: ordering, blocked propagation, stop conditions,
restart recovery, caps and identity-first (plan §4.3, A2/A4/A5).

Real file DB under ``tmp_path``; Exa is an in-memory fake — no network.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import (
    FakeExa,
    empty_result,
    identity_result,
    make_hit,
    structured_result,
    utcnow,
)

from app.engine.budget import estimate_run_cost
from app.engine.events import HubRegistry
from app.engine.limiter import RateLimiter
from app.engine.runner import SheetRunner
from app.errors.provider_error import ProviderError
from app.providers.types import ExaSearchRequest, ExaSearchResult
from app.sheets import repo
from app.sheets.models import Cell, Column, Row, RowInput, Sheet
from app.sheets.service import SheetService

Handler = Callable[[ExaSearchRequest], Awaitable[ExaSearchResult]]


def _runner(db_path: str, handler: Handler, **kwargs: Any) -> SheetRunner:
    return SheetRunner(
        db_path,
        FakeExa(handler),
        limiter=RateLimiter(1000),
        clock=utcnow,
        **kwargs,
    )


async def _resolve(db: str, row: Row, name: str = "Acme Corp") -> None:
    await repo.update_row_identity(
        db,
        row.id,
        identity_status="resolved",
        resolved_name=name,
        resolved_domain=row.input_domain,
    )


async def _sheet(
    db: str,
    specs: list[tuple[str, str]],
    *,
    rows: int = 1,
    resolve: bool = True,
    service: SheetService | None = None,
) -> tuple[Sheet, list[Row], list[Column]]:
    """A sheet with rows and ``(name, template)`` columns via the service."""
    svc = service or SheetService(db)
    sheet = await svc.create_sheet("s")
    created_rows = await svc.import_rows(
        sheet.id,
        [RowInput(input_name=f"Co{i}", input_domain=f"co{i}.test") for i in range(rows)],
    )
    columns = []
    for name, template in specs:
        columns.append(
            await svc.add_column(
                sheet.id,
                name=name,
                prompt_template=template,
                output_type="text",
                source_scope="web",
            )
        )
    if resolve:
        for row in created_rows:
            await _resolve(db, row, name=f"{row.input_name} Inc")
    return sheet, created_rows, columns


async def _ok(request: ExaSearchRequest) -> ExaSearchResult:
    return structured_result(value=f"answer for {request.query[:20]}")


def _provider_error(code: str) -> ProviderError:
    return ProviderError(provider="Exa", code=code, message=f"synthetic {code}", retryable=False)


async def _cell(db: str, row_id: str, column_id: str) -> Cell:
    cell = await repo.get_cell(db, row_id, column_id)
    assert cell is not None
    return cell


# -- ordering and dependencies -------------------------------------------------


async def test_dependent_cell_never_starts_before_upstream(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "describe {{Company}}"), ("B", "sum {{A}}")])
    calls: list[str] = []

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return empty_result()
        calls.append("describe" if "describe" in request.query else "sum")
        return structured_result(value="A-answer")

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, limiter=RateLimiter(1000), clock=utcnow)
    run = await runner.run(sheet.id, "empty")

    assert calls == ["describe", "sum"]  # strict order, even with capacity
    cell_b = await _cell(db, rows[0].id, columns[1].id)
    assert cell_b.status == "done"
    assert cell_b.rendered_prompt is not None
    assert '<upstream column="A">' in cell_b.rendered_prompt
    assert "A-answer" in cell_b.rendered_prompt
    assert run.cancelled is False


async def test_blocked_propagation_on_upstream_error(db: str) -> None:
    sheet, rows, columns = await _sheet(
        db, [("A", "describe {{Company}}"), ("B", "sum {{A}}"), ("C", "independent")]
    )

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if "describe" in request.query:
            raise _provider_error("exa_server_error")
        return await _ok(request)

    runner = _runner(db, handler)
    await runner.run(sheet.id, "empty")

    cell_a = await _cell(db, rows[0].id, columns[0].id)
    cell_b = await _cell(db, rows[0].id, columns[1].id)
    cell_c = await _cell(db, rows[0].id, columns[2].id)
    assert cell_a.status == "error"
    assert cell_b.status == "blocked"
    assert cell_b.error is not None and "'A'" in cell_b.error
    assert cell_c.status == "done"  # independent work still completes


async def test_blocked_propagation_on_upstream_unavailable(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "describe"), ("B", "sum {{A}}")])

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if "describe" in request.query:
            return structured_result(found=False, value=None)
        return await _ok(request)

    runner = _runner(db, handler)
    await runner.run(sheet.id, "empty")

    cell_a = await _cell(db, rows[0].id, columns[0].id)
    cell_b = await _cell(db, rows[0].id, columns[1].id)
    assert cell_a.status == "done"
    assert cell_a.result is not None and cell_a.result.status == "unavailable"
    assert cell_b.status == "blocked"
    assert cell_b.error is not None and "unavailable" in cell_b.error


# -- stop conditions -------------------------------------------------------------


async def test_402_stops_run_and_returns_queued_cells_to_idle(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "b"), ("C", "c")], rows=1)
    calls = 0

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        nonlocal calls
        calls += 1
        raise _provider_error("exa_credits_exhausted")

    runner = SheetRunner(
        db,
        FakeExa(handler),
        max_concurrency=1,
        limiter=RateLimiter(1000),
        clock=utcnow,
    )
    run = await runner.run(sheet.id, "empty")

    assert calls == 1  # one failure, not N identical errors
    statuses = [await _cell(db, rows[0].id, c.id) for c in columns]
    # Exactly one cell consumed the 402; every other queued cell went idle.
    assert sorted(c.status for c in statuses) == ["error", "idle", "idle"]
    final = await repo.get_run(db, run.id)
    assert final is not None and final.cancelled is True
    assert final.reason == "exa_credits_exhausted"


async def test_cost_cap_stops_run_and_is_never_exceeded(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "b"), ("C", "c")])
    runner = SheetRunner(
        db,
        FakeExa(_ok),
        max_concurrency=1,
        limiter=RateLimiter(1000),
        clock=utcnow,
    )
    run = await runner.run(sheet.id, "empty", cap=0.02)

    statuses = [await _cell(db, rows[0].id, c.id) for c in columns]
    done = [c for c in statuses if c.status == "done"]
    others = [c for c in statuses if c.status != "done"]
    assert len(done) == 1  # 0.007 spent; a second 0.015 estimate would cap
    assert all(c.status == "idle" for c in others)
    final = await repo.get_run(db, run.id)
    assert final is not None and final.cancelled is True
    assert final.reason == "cost_cap"
    assert final.spent <= 0.02  # cap never exceeded


async def test_estimate_covers_actual_spend(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "b")], resolve=False)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return identity_result("https://co0.test/", "Co0 Inc")
        return await _ok(request)

    runner = _runner(db, handler)
    run = await runner.run(sheet.id, "empty")

    final = await repo.get_run(db, run.id)
    assert final is not None
    estimate = estimate_run_cost(pending_identity_rows=1, cell_count=len(columns))
    assert estimate >= final.spent  # estimate >= actual in fake runs


# -- restart recovery -------------------------------------------------------------


async def test_restart_requeues_running_and_keeps_done(db: str) -> None:
    # B depends on A so A always finishes before B is claimed.
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "sum {{A}}")])
    gate = asyncio.Event()

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if "sum" in request.query:
            await gate.wait()  # hangs mid-run like a killed process
        return await _ok(request)

    runner = SheetRunner(
        db,
        FakeExa(handler),
        max_concurrency=1,
        limiter=RateLimiter(1000),
        clock=utcnow,
    )
    task = asyncio.create_task(runner.run(sheet.id, "empty"))
    for _ in range(200):  # wait until B is claimed running
        cell_b = await repo.get_cell(db, rows[0].id, columns[1].id)
        if cell_b is not None and cell_b.status == "running":
            break
        await asyncio.sleep(0.005)
    task.cancel()  # simulate the process dying mid-run
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await _cell(db, rows[0].id, columns[1].id)).status == "running"
    assert await repo.requeue_orphans(db) == 1  # startup recovery
    assert (await _cell(db, rows[0].id, columns[1].id)).status == "queued"
    assert (await _cell(db, rows[0].id, columns[0].id)).status == "done"
    assert await repo.requeue_orphans(db) == 0  # exactly once
    gate.set()  # release the leaked worker so it can settle post-test
    await asyncio.sleep(0.05)


async def test_graceful_shutdown_requeues_running_cells(db: str) -> None:
    # B depends on A so A always finishes before B is claimed.
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "sum {{A}}")])
    gate = asyncio.Event()

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if "sum" in request.query:
            await gate.wait()
        return await _ok(request)

    runner = SheetRunner(
        db,
        FakeExa(handler),
        max_concurrency=1,
        limiter=RateLimiter(1000),
        clock=utcnow,
    )
    task = asyncio.create_task(runner.run(sheet.id, "empty"))
    for _ in range(200):
        cell_b = await repo.get_cell(db, rows[0].id, columns[1].id)
        if cell_b is not None and cell_b.status == "running":
            break
        await asyncio.sleep(0.005)
    await runner.aclose()  # graceful shutdown requeues running -> queued
    await asyncio.wait_for(task, timeout=5)

    assert (await _cell(db, rows[0].id, columns[1].id)).status == "queued"
    assert (await _cell(db, rows[0].id, columns[0].id)).status == "done"


# -- identity first ----------------------------------------------------------------


async def test_identity_first_resolves_then_runs_cells(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "about {{Company}}")], resolve=False)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return identity_result("https://co0.test/", "Co0 Inc")
        return await _ok(request)

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, limiter=RateLimiter(1000), clock=utcnow)
    await runner.run(sheet.id, "empty")

    row = (await repo.list_rows(db, sheet.id))[0]
    assert row.identity_status == "resolved"
    assert row.resolved_name == "Co0 Inc"
    cell = await _cell(db, rows[0].id, columns[0].id)
    assert cell.status == "done"
    identity_requests = [r for r in exa.requests if r.output_schema is None]
    assert len(identity_requests) >= 1


async def test_unresolved_row_blocks_all_cells_without_cell_calls(db: str) -> None:
    sheet, rows, columns = await _sheet(db, [("A", "a"), ("B", "b")], resolve=False)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        return empty_result()  # identity search finds nothing

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, limiter=RateLimiter(1000), clock=utcnow)
    await runner.run(sheet.id, "empty")

    row = (await repo.list_rows(db, sheet.id))[0]
    assert row.identity_status == "unresolved"
    for column in columns:
        cell = await _cell(db, rows[0].id, column.id)
        assert cell.status == "blocked"
        assert cell.error is not None
    assert exa.cell_requests() == []  # no cell reached Exa


async def test_ambiguous_row_blocks_cells(db: str) -> None:
    # Name-only input whose name-matching hits span two hosts -> ambiguous.
    svc = SheetService(db)
    sheet = await svc.create_sheet("s")
    rows = await svc.import_rows(sheet.id, [RowInput(input_name="Zebra Corp")])
    column = await svc.add_column(
        sheet.id,
        name="A",
        prompt_template="p",
        output_type="text",
        source_scope="web",
    )

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        return ExaSearchResult(
            hits=[
                make_hit("https://alpha.test/", "Zebra Corp"),
                make_hit("https://beta.test/", "Zebra Corp"),
            ],
            query="identity",
            calls=1,
        )

    runner = _runner(db, handler)
    await runner.run(sheet.id, "empty")
    row = (await repo.list_rows(db, sheet.id))[0]
    assert row.identity_status == "ambiguous"
    assert (await _cell(db, rows[0].id, column.id)).status == "blocked"


# -- events ------------------------------------------------------------------------


async def test_runner_publishes_cell_events(db: str) -> None:
    hubs = HubRegistry()
    sheet, rows, columns = await _sheet(db, [("A", "a")])
    runner = SheetRunner(
        db,
        FakeExa(_ok),
        hubs=hubs,
        limiter=RateLimiter(1000),
        clock=utcnow,
    )
    queue = hubs.for_sheet(sheet.id).subscribe()
    await runner.run(sheet.id, "empty")
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    cell_events = [e for e in events if e["type"] == "cell"]
    assert any(e["status"] == "running" for e in cell_events)
    assert any(e["status"] == "done" and e["revision"] == 1 for e in cell_events)
