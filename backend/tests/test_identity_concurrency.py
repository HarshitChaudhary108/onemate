"""Bounded-concurrent identity resolution + contents-free identity
requests (sheets-upgrade performance patch).

Real file DB under ``tmp_path``; Exa is an in-memory fake — no network.
"""

import asyncio

from conftest import FakeExa, empty_result, identity_result, structured_result, utcnow

from app.engine.limiter import RateLimiter
from app.engine.runner import SheetRunner
from app.errors.provider_error import ProviderError
from app.providers.types import ExaSearchRequest, ExaSearchResult
from app.sheets import repo
from app.sheets.models import Row, RowInput
from app.sheets.service import SheetService


async def _sheet(db: str, rows: int) -> tuple[str, list[Row]]:
    """A sheet with ``rows`` pending-identity rows and one column."""
    svc = SheetService(db)
    sheet = await svc.create_sheet("s")
    created = await svc.import_rows(
        sheet.id,
        [RowInput(input_name=f"Co{i}", input_domain=f"co{i}.test") for i in range(rows)],
    )
    await svc.add_column(
        sheet.id,
        name="A",
        prompt_template="about {{Company}}",
        output_type="text",
        source_scope="web",
    )
    return sheet.id, created


async def _ok(request: ExaSearchRequest) -> ExaSearchResult:
    return structured_result(value=f"answer for {request.query[:20]}")


def _identity_hit_for(request: ExaSearchRequest) -> ExaSearchResult:
    """A resolving hit on the exact domain the request is scoped to."""
    host = (request.include_domains or ["unknown.test"])[0]
    return identity_result(f"https://{host}/", "Co Inc")


class _CountingLimiter(RateLimiter):
    """Shared limiter spy: counts every acquisition."""

    def __init__(self, rate: float) -> None:
        super().__init__(rate)
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1
        await super().acquire()


# -- contents-free identity requests --------------------------------------------


async def test_identity_requests_omit_contents_cell_requests_keep_them(db: str) -> None:
    sheet_id, _ = await _sheet(db, 2)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return _identity_hit_for(request)
        return await _ok(request)

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, limiter=RateLimiter(1000), clock=utcnow)
    await runner.run(sheet_id, "empty")

    identity = [r for r in exa.requests if r.output_schema is None]
    cells = [r for r in exa.requests if r.output_schema is not None]
    assert identity, "expected at least one identity request"
    assert cells, "expected at least one cell request"
    assert all(r.include_contents is False for r in identity)
    assert all(r.include_contents is True for r in cells)
    # Identity lookups run the low-latency fast type; cells keep auto.
    assert all(r.search_type == "fast" for r in identity)
    assert all(r.search_type == "auto" for r in cells)


# -- bounded concurrency ---------------------------------------------------------


async def test_identities_resolve_concurrently_within_configured_bound(db: str) -> None:
    sheet_id, rows = await _sheet(db, 6)
    in_flight = 0
    peak = 0

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        nonlocal in_flight, peak
        if request.output_schema is None:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)  # hold the slot so overlap is visible
            in_flight -= 1
            return _identity_hit_for(request)
        return await _ok(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=3, limiter=RateLimiter(1000), clock=utcnow
    )
    await runner.run(sheet_id, "empty")

    assert peak == 3  # concurrent AND bounded at max_concurrency
    input_domains = {r.input_domain for r in rows}
    for row in await repo.list_rows(db, sheet_id):
        assert row.identity_status == "resolved", row.id
        assert row.resolved_domain in input_domains


async def test_shared_rate_limiter_serves_every_identity_search(db: str) -> None:
    sheet_id, _ = await _sheet(db, 4)
    limiter = _CountingLimiter(1000)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return _identity_hit_for(request)
        return await _ok(request)

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, max_concurrency=4, limiter=limiter, clock=utcnow)
    await runner.run(sheet_id, "empty")

    # One limiter acquisition per actual provider call — identity and cell.
    assert limiter.acquired == len(exa.requests)


# -- correctness under concurrency -------------------------------------------------


async def test_fallback_query_still_runs_and_resolves(db: str) -> None:
    sheet_id, _ = await _sheet(db, 1)
    seen: list[ExaSearchRequest] = []

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            seen.append(request)
            # Primary (category="company") finds nothing on the domain;
            # the fallback (no category) does.
            if request.category == "company":
                return empty_result()
            return _identity_hit_for(request)
        return await _ok(request)

    runner = SheetRunner(db, FakeExa(handler), limiter=RateLimiter(1000), clock=utcnow)
    await runner.run(sheet_id, "empty")

    assert [r.category for r in seen] == ["company", None]
    assert all(r.include_contents is False for r in seen)
    row = (await repo.list_rows(db, sheet_id))[0]
    assert row.identity_status == "resolved"
    assert row.resolved_domain == "co0.test"


async def test_mixed_outcomes_persist_correct_statuses(db: str) -> None:
    sheet_id, rows = await _sheet(db, 3)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            if request.include_domains == ["co1.test"]:
                return empty_result()  # primary AND fallback both miss
            return _identity_hit_for(request)
        return await _ok(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=3, limiter=RateLimiter(1000), clock=utcnow
    )
    await runner.run(sheet_id, "empty")

    by_domain = {row.input_domain: row for row in await repo.list_rows(db, sheet_id)}
    assert by_domain["co0.test"].identity_status == "resolved"
    assert by_domain["co0.test"].resolved_name == "Co Inc"
    assert by_domain["co2.test"].identity_status == "resolved"
    assert by_domain["co1.test"].identity_status == "unresolved"
    # The unresolved row's cell is blocked with the resolution note.
    columns = await repo.list_columns(db, sheet_id)
    cell = await repo.get_cell(db, rows[1].id, columns[0].id)
    assert cell is not None and cell.status == "blocked"


# -- stop conditions under concurrency ---------------------------------------------


async def test_fatal_provider_error_stops_scheduling_safely(db: str) -> None:
    sheet_id, _ = await _sheet(db, 5)
    gate = asyncio.Event()
    started = 0

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        nonlocal started
        if request.output_schema is None:
            started += 1
            if started >= 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            raise ProviderError(
                provider="Exa",
                code="exa_invalid_key",
                message="synthetic 401",
                retryable=False,
            )
        return await _ok(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=2, limiter=RateLimiter(1000), clock=utcnow
    )
    run = await runner.run(sheet_id, "empty")

    # Only the two in-flight requests ran; nothing new was scheduled
    # after the 401 surfaced.
    assert started == 2
    final = await repo.get_run(db, run.id)
    assert final is not None and final.cancelled is True
    assert final.reason == "exa_invalid_key"
    # Fatal errors leave rows pending — never marked resolved or errored.
    for row in await repo.list_rows(db, sheet_id):
        assert row.identity_status == "pending"


async def test_unexpected_identity_error_leaves_row_unresolved(db: str) -> None:
    sheet_id, rows = await _sheet(db, 1)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            raise ValueError("synthetic unexpected failure")
        return await _ok(request)

    runner = SheetRunner(db, FakeExa(handler), limiter=RateLimiter(1000), clock=utcnow)
    run = await runner.run(sheet_id, "empty")

    row = (await repo.list_rows(db, sheet_id))[0]
    assert row.identity_status == "pending"  # never wrongly resolved
    assert row.resolved_domain is None
    columns = await repo.list_columns(db, sheet_id)
    cell = await repo.get_cell(db, rows[0].id, columns[0].id)
    assert cell is not None and cell.status == "blocked"
    assert cell.error is not None and "identity not resolved" in cell.error
    assert run.cancelled is False


async def test_shutdown_during_identity_phase_cancels_safely(db: str) -> None:
    sheet_id, _ = await _sheet(db, 4)
    gate = asyncio.Event()

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        await gate.wait()  # every identity call hangs mid-flight
        return _identity_hit_for(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=2, limiter=RateLimiter(1000), clock=utcnow
    )
    task = asyncio.create_task(runner.run(sheet_id, "empty"))
    for _ in range(200):  # wait until the identity workers are in flight
        await asyncio.sleep(0.005)
        if runner._active:
            break
    await runner.aclose()
    await asyncio.wait_for(task, timeout=5)

    for row in await repo.list_rows(db, sheet_id):
        assert row.identity_status == "pending"  # cancelled, not resolved


# -- identity-only background pass --------------------------------------------------
# resolve_pending_identities verifies rows added via import/generate so the
# Company column does not stay "pending" until the first column run.


async def _pass_task(runner: SheetRunner, sheet_id: str) -> None:
    """Await the scheduled identity pass to completion."""
    task = runner._identity_passes.get(sheet_id)
    assert task is not None, "expected an identity pass to be scheduled"
    await task


async def test_import_time_pass_resolves_rows_without_queuing_cells(db: str) -> None:
    sheet_id, rows = await _sheet(db, 3)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return _identity_hit_for(request)
        return await _ok(request)

    exa = FakeExa(handler)
    runner = SheetRunner(db, exa, limiter=RateLimiter(1000), clock=utcnow)
    await runner.resolve_pending_identities(sheet_id)
    await _pass_task(runner, sheet_id)

    for row in await repo.list_rows(db, sheet_id):
        assert row.identity_status == "resolved"
    # No cell was queued or touched — only identity requests hit Exa.
    assert exa.cell_requests() == []
    columns = await repo.list_columns(db, sheet_id)
    for row in rows:
        cell = await repo.get_cell(db, row.id, columns[0].id)
        assert cell is not None and cell.status == "idle"


async def test_pass_leaves_unresolved_row_cells_idle_for_a_later_run(db: str) -> None:
    sheet_id, rows = await _sheet(db, 1)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            return empty_result()  # identity never resolves
        return await _ok(request)

    runner = SheetRunner(db, FakeExa(handler), limiter=RateLimiter(1000), clock=utcnow)
    await runner.resolve_pending_identities(sheet_id)
    await _pass_task(runner, sheet_id)

    row = (await repo.list_rows(db, sheet_id))[0]
    assert row.identity_status == "unresolved"
    columns = await repo.list_columns(db, sheet_id)
    cell = await repo.get_cell(db, rows[0].id, columns[0].id)
    assert cell is not None and cell.status == "idle"  # NOT blocked

    # The next real run queues it and blocks it with the identity reason.
    await runner.run(sheet_id, "empty")
    cell = await repo.get_cell(db, rows[0].id, columns[0].id)
    assert cell is not None and cell.status == "blocked"


async def test_pass_is_a_noop_when_a_run_is_active(db: str) -> None:
    sheet_id, _ = await _sheet(db, 1)
    gate = asyncio.Event()
    calls = 0

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        nonlocal calls
        calls += 1
        await gate.wait()
        return _identity_hit_for(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=1, limiter=RateLimiter(1000), clock=utcnow
    )
    run_task = asyncio.create_task(runner.run(sheet_id, "empty"))
    for _ in range(200):
        if calls > 0:
            break
        await asyncio.sleep(0.005)

    await runner.resolve_pending_identities(sheet_id)
    assert sheet_id not in runner._identity_passes  # yielded to the real run

    gate.set()
    await asyncio.wait_for(run_task, timeout=5)
    assert (await repo.list_rows(db, sheet_id))[0].identity_status == "resolved"


async def test_pass_fatal_error_leaves_rows_pending_and_cells_idle(db: str) -> None:
    sheet_id, rows = await _sheet(db, 2)

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        raise ProviderError(
            provider="Exa",
            code="exa_invalid_key",
            message="synthetic 401",
            retryable=False,
        )

    runner = SheetRunner(db, FakeExa(handler), limiter=RateLimiter(1000), clock=utcnow)
    await runner.resolve_pending_identities(sheet_id)
    await _pass_task(runner, sheet_id)

    columns = await repo.list_columns(db, sheet_id)
    for row in await repo.list_rows(db, sheet_id):
        assert row.identity_status == "pending"
        cell = await repo.get_cell(db, row.id, columns[0].id)
        assert cell is not None and cell.status == "idle"


# -- phase ordering -----------------------------------------------------------------


async def test_no_cell_dispatches_before_identity_phase_finishes(db: str) -> None:
    sheet_id, _ = await _sheet(db, 4)
    events: list[str] = []

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is None:
            await asyncio.sleep(0.02)  # slow identities must still finish first
            events.append("identity")
            return _identity_hit_for(request)
        events.append("cell")
        return await _ok(request)

    runner = SheetRunner(
        db, FakeExa(handler), max_concurrency=4, limiter=RateLimiter(1000), clock=utcnow
    )
    await runner.run(sheet_id, "empty")

    assert events.count("identity") == 4
    assert events.index("cell") > max(i for i, e in enumerate(events) if e == "identity")
