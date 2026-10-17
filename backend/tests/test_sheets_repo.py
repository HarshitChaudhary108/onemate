"""Sheets repo behavior: queue mechanics, atomicity, soft-delete, snapshot.

Real file DBs under ``tmp_path`` — ``:memory:`` is not shared across
connections, and every repo function opens its own connection.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from app.schemas.report import CompanyIdentity, Field
from app.sheets import repo
from app.sheets.db import connect, init_db
from app.sheets.models import Cell, CellValue, Column, Row, RowInput, Sheet


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> str:
    path = str(tmp_path / "sheets.db")
    await init_db(path)
    return path


async def _grid(
    db: str, row_count: int = 2, column_count: int = 1
) -> tuple[Sheet, list[Row], list[Column]]:
    """One sheet with rows, columns and an idle cell at every intersection."""
    sheet = await repo.create_sheet(db, "grid")
    rows = await repo.create_rows(
        db,
        sheet.id,
        [
            RowInput(input_name=f"Co{i}", input_domain=f"co{i}.example.com")
            for i in range(row_count)
        ],
    )
    columns = [
        await repo.create_column(
            db,
            sheet.id,
            name=f"Col{i}",
            prompt_template=f"prompt {i}",
            output_type="text",
            source_scope="web",
        )
        for i in range(column_count)
    ]
    for row in rows:
        for col in columns:
            await repo.upsert_cell(db, row.id, col.id)
    return sheet, rows, columns


async def _claimed_cell(db: str) -> tuple[str, str]:
    """One cell moved through idle → queued → running."""
    sheet, rows, columns = await _grid(db, row_count=1)
    await repo.queue_cells(db, sheet.id, "empty")
    cell = await repo.claim_cell(db, rows[0].id, columns[0].id)
    assert cell is not None
    return rows[0].id, columns[0].id


async def _cell(db: str, row_id: str, column_id: str) -> Cell:
    cell = await repo.get_cell(db, row_id, column_id)
    assert cell is not None
    return cell


async def test_claim_race_allows_exactly_one_winner(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=1)
    await repo.queue_cells(db, sheet.id, "empty")

    claims = await asyncio.gather(
        *[repo.claim_cell(db, rows[0].id, columns[0].id) for _ in range(8)]
    )
    winners = [c for c in claims if c is not None]
    assert len(winners) == 1
    assert winners[0].status == "running"

    cell = await _cell(db, rows[0].id, columns[0].id)
    assert cell.status == "running"


async def test_finish_cell_appends_monotonic_revisions(db: str) -> None:
    row_id, column_id = await _claimed_cell(db)

    first = await repo.finish_cell(
        db,
        row_id,
        column_id,
        status="done",
        result=Field[CellValue](value="acme", status="inferred"),
        cost=0.007,
        rendered_prompt="prompt 0",
    )
    assert first.revision == 1 and first.status == "done"
    assert first.result is not None and first.result.value == "acme"

    second = await repo.finish_cell(db, row_id, column_id, status="error", error="boom")
    assert second.revision == 2 and second.status == "error"

    revisions = await repo.list_cell_revisions(db, row_id, column_id)
    assert [r.revision for r in revisions] == [1, 2]
    assert revisions[0].result is not None and revisions[0].result.value == "acme"
    assert revisions[1].error == "boom"


async def test_finish_cell_rolls_back_on_mid_transaction_failure(db: str) -> None:
    row_id, column_id = await _claimed_cell(db)
    await repo.finish_cell(db, row_id, column_id, status="done", cost=0.007)

    # Simulate a corrupted/duplicated history row: the append of revision 2
    # conflicts AFTER the cell update already ran inside the transaction.
    conn = await connect(db)
    try:
        await conn.execute(
            "INSERT INTO cell_revisions (row_id, column_id, revision, created_at)"
            " VALUES (?, ?, ?, ?)",
            (row_id, column_id, 2, "2026-10-17T00:00:00+00:00"),
        )
    finally:
        await conn.close()

    with pytest.raises(sqlite3.IntegrityError):
        await repo.finish_cell(db, row_id, column_id, status="error", error="x")

    cell = await _cell(db, row_id, column_id)
    assert cell.status == "done"  # the UPDATE inside the tx was rolled back
    assert cell.revision == 1
    assert cell.error is None
    revisions = await repo.list_cell_revisions(db, row_id, column_id)
    assert [r.revision for r in revisions] == [1, 2]  # only the seeded rows


async def test_bool_and_int_results_do_not_collapse(db: str) -> None:
    # JSON-in-TEXT keeps True distinct from 1 — the classic bool-vs-int
    # SQLite hazard if a raw value column were used instead.
    sheet, rows, columns = await _grid(db, row_count=2)
    await repo.queue_cells(db, sheet.id, "empty")
    for row in rows:
        assert await repo.claim_cell(db, row.id, columns[0].id) is not None

    await repo.finish_cell(
        db,
        rows[0].id,
        columns[0].id,
        status="done",
        result=Field[CellValue](value=True, status="inferred"),
    )
    await repo.finish_cell(
        db,
        rows[1].id,
        columns[0].id,
        status="done",
        result=Field[CellValue](value=1, status="inferred"),
    )

    bool_cell = await _cell(db, rows[0].id, columns[0].id)
    int_cell = await _cell(db, rows[1].id, columns[0].id)
    assert bool_cell.result is not None and bool_cell.result.value is True
    assert int_cell.result is not None
    assert int_cell.result.value == 1 and type(int_cell.result.value) is int


async def test_soft_deleted_rows_and_columns_disappear(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=2, column_count=2)

    assert await repo.soft_delete_row(db, rows[0].id) is True
    assert [r.id for r in await repo.list_rows(db, sheet.id)] == [rows[1].id]

    snap = await repo.snapshot(db, sheet.id)
    assert snap is not None
    assert [r.id for r in snap.rows] == [rows[1].id]
    assert all(c.row_id == rows[1].id for c in snap.cells)

    assert await repo.soft_delete_column(db, columns[0].id) is True
    assert [c.id for c in await repo.list_columns(db, sheet.id)] == [columns[1].id]

    snap = await repo.snapshot(db, sheet.id)
    assert snap is not None
    assert [c.id for c in snap.columns] == [columns[1].id]
    assert all(c.column_id == columns[1].id for c in snap.cells)


async def test_requeue_orphans_only_moves_running(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=3)
    col = columns[0].id
    await repo.queue_cells(db, sheet.id, "empty")
    assert await repo.claim_cell(db, rows[0].id, col) is not None  # running
    assert await repo.claim_cell(db, rows[1].id, col) is not None
    await repo.finish_cell(db, rows[1].id, col, status="done")  # done
    # rows[2] stays queued

    assert await repo.requeue_orphans(db) == 1

    assert (await _cell(db, rows[0].id, col)).status == "queued"
    assert (await _cell(db, rows[1].id, col)).status == "done"
    assert (await _cell(db, rows[2].id, col)).status == "queued"


async def test_queue_cells_scopes(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=2)
    col = columns[0].id

    assert await repo.queue_cells(db, sheet.id, "empty") == 2

    assert await repo.claim_cell(db, rows[0].id, col) is not None
    await repo.finish_cell(db, rows[0].id, col, status="error", error="nope")

    # only the errored cell is eligible; the still-queued one is untouched
    run = await repo.create_run(db, sheet.id)
    assert await repo.queue_cells(db, sheet.id, "error", run_id=run.id) == 1
    errored = await _cell(db, rows[0].id, col)
    assert errored.status == "queued" and errored.run_id == run.id


async def test_cancel_run_returns_only_queued_cells_to_idle(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=2)
    col = columns[0].id
    run = await repo.create_run(db, sheet.id, cap=1.00)

    assert await repo.queue_cells(db, sheet.id, "all", run_id=run.id) == 2
    assert await repo.claim_cell(db, rows[0].id, col, run_id=run.id) is not None

    cancelled = await repo.cancel_run(db, run.id)
    assert cancelled is not None and cancelled.cancelled is True

    running = await _cell(db, rows[0].id, col)
    idled = await _cell(db, rows[1].id, col)
    assert running.status == "running"
    assert idled.status == "idle" and idled.run_id is None


async def test_update_column_bumps_version_and_marks_stale(db: str) -> None:
    row_id, column_id = await _claimed_cell(db)
    await repo.finish_cell(
        db,
        row_id,
        column_id,
        status="done",
        result=Field[CellValue](value="old", status="inferred"),
        column_version=1,
    )

    updated = await repo.update_column(
        db,
        column_id,
        name="Col0",
        prompt_template="new prompt",
        output_type="text",
        source_scope="web",
        depends_on=[],
    )
    assert updated is not None and updated.version == 2

    cell = await _cell(db, row_id, column_id)
    assert cell.stale is True  # a prompt edit marks stale, never wipes
    assert cell.status == "done"
    assert cell.result is not None and cell.result.value == "old"


async def test_update_row_identity_round_trip(db: str) -> None:
    _, rows, _ = await _grid(db, row_count=1)
    identity = CompanyIdentity(
        resolved=True,
        company_name=Field[str](value="Acme Corp", status="inferred"),
        domain=Field[str](value="acme.example.com", status="inferred"),
    )

    updated = await repo.update_row_identity(
        db,
        rows[0].id,
        identity_status="resolved",
        resolved_name="Acme Corp",
        resolved_domain="acme.example.com",
        identity=identity,
    )
    assert updated is not None
    assert updated.identity_status == "resolved"
    assert updated.identity is not None
    assert updated.identity.company_name.value == "Acme Corp"


async def test_snapshot_returns_full_state(db: str) -> None:
    sheet, _, _ = await _grid(db, row_count=3, column_count=2)
    run = await repo.create_run(db, sheet.id, cap=0.50, reason="preview ok")

    snap = await repo.snapshot(db, sheet.id)
    assert snap is not None
    assert snap.sheet.id == sheet.id
    assert len(snap.rows) == 3
    assert len(snap.columns) == 2
    assert len(snap.cells) == 6
    assert all(c.status == "idle" and c.revision == 0 for c in snap.cells)
    assert snap.latest_run is not None and snap.latest_run.id == run.id


async def test_delete_sheet_cascades_everything(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=2)
    await repo.queue_cells(db, sheet.id, "empty")
    assert await repo.claim_cell(db, rows[0].id, columns[0].id) is not None
    await repo.finish_cell(db, rows[0].id, columns[0].id, status="done")
    await repo.create_run(db, sheet.id)

    assert await repo.delete_sheet(db, sheet.id) is True
    assert await repo.get_sheet(db, sheet.id) is None
    assert await repo.snapshot(db, sheet.id) is None

    conn = await connect(db)
    try:
        for table in ("rows", "columns", "cells", "cell_revisions", "runs"):
            cursor = await conn.execute(f'SELECT COUNT(*) FROM "{table}"')
            row = await cursor.fetchone()
            assert row is not None and row[0] == 0, table
    finally:
        await conn.close()


async def test_mark_blocked_records_reason(db: str) -> None:
    sheet, rows, columns = await _grid(db, row_count=1)
    blocked = await repo.mark_blocked(db, rows[0].id, columns[0].id, "upstream failed")
    assert blocked is not None
    assert blocked.status == "blocked"
    assert blocked.error == "upstream failed"
