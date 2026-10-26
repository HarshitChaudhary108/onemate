"""Repository functions for the sheets store.

Every function opens its own connection through :func:`connect` — one
connection per unit of work, never shared across tasks. Writes run under
``BEGIN IMMEDIATE`` so the database write lock is held for the whole unit:
``claim_cell``'s compare-and-set and ``finish_cell``'s cell update +
history append are atomic, and two workers can never claim the same cell.

``depends_on`` is stored verbatim — it is derived from the prompt template
by the caller, never accepted from a client.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import aiosqlite

from app.schemas.report import CompanyIdentity, Field
from app.sheets.db import connect
from app.sheets.models import (
    Cell,
    CellRevision,
    CellStatus,
    CellValue,
    Column,
    Depth,
    IdentityStatus,
    OutputType,
    Row,
    RowInput,
    Run,
    RunScope,
    Sheet,
    SheetSnapshot,
    SourceScope,
)


class CellNotFoundError(LookupError):
    """No cell exists at the requested (row_id, column_id)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@asynccontextmanager
async def _read(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    conn = await connect(db_path)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def _write(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """One connection holding the write lock for a whole unit of work."""
    conn = await connect(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        yield conn
    except BaseException:
        await conn.rollback()
        raise
    else:
        await conn.commit()
    finally:
        await conn.close()


async def _one(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()
) -> aiosqlite.Row | None:
    cursor = await conn.execute(sql, params)
    return await cursor.fetchone()


async def _all(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()
) -> list[aiosqlite.Row]:
    cursor = await conn.execute(sql, params)
    return list(await cursor.fetchall())


# --- row → model mapping -------------------------------------------------


def _data(r: aiosqlite.Row) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def _to_sheet(r: aiosqlite.Row) -> Sheet:
    return Sheet.model_validate(_data(r))


def _to_row(r: aiosqlite.Row) -> Row:
    data = _data(r)
    raw = data.pop("identity_json")
    data["identity"] = CompanyIdentity.model_validate_json(raw) if raw is not None else None
    return Row.model_validate(data)


def _to_column(r: aiosqlite.Row) -> Column:
    data = _data(r)
    data["enum_options"] = (
        json.loads(data["enum_options"]) if data["enum_options"] is not None else None
    )
    data["depends_on"] = json.loads(data["depends_on"])
    return Column.model_validate(data)


def _to_cell(r: aiosqlite.Row) -> Cell:
    data = _data(r)
    raw = data.pop("result_json")
    data["result"] = Field[CellValue].model_validate_json(raw) if raw is not None else None
    return Cell.model_validate(data)


def _to_cell_revision(r: aiosqlite.Row) -> CellRevision:
    data = _data(r)
    raw = data.pop("result_json")
    data["result"] = Field[CellValue].model_validate_json(raw) if raw is not None else None
    return CellRevision.model_validate(data)


def _to_run(r: aiosqlite.Row) -> Run:
    return Run.model_validate(_data(r))


# --- sheets ---------------------------------------------------------------


async def create_sheet(db_path: str, name: str) -> Sheet:
    now = _now()
    sheet = Sheet(id=uuid4().hex, name=name, created_at=now, updated_at=now)
    async with _write(db_path) as conn:
        await conn.execute(
            "INSERT INTO sheets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sheet.id, sheet.name, now, now),
        )
    return sheet


async def get_sheet(db_path: str, sheet_id: str) -> Sheet | None:
    async with _read(db_path) as conn:
        row = await _one(conn, "SELECT * FROM sheets WHERE id = ?", (sheet_id,))
    return _to_sheet(row) if row is not None else None


async def list_sheets(db_path: str) -> list[Sheet]:
    async with _read(db_path) as conn:
        rows = await _all(conn, "SELECT * FROM sheets ORDER BY created_at, id")
    return [_to_sheet(r) for r in rows]


async def delete_sheet(db_path: str, sheet_id: str) -> bool:
    """Delete a sheet; rows, columns, cells, revisions and runs cascade."""
    async with _write(db_path) as conn:
        cursor = await conn.execute("DELETE FROM sheets WHERE id = ?", (sheet_id,))
        return cursor.rowcount == 1


# --- rows -----------------------------------------------------------------


async def create_rows(db_path: str, sheet_id: str, inputs: Sequence[RowInput]) -> list[Row]:
    """Bulk-insert rows, assigning positions after the current maximum."""
    async with _write(db_path) as conn:
        row = await _one(
            conn,
            'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM "rows" WHERE sheet_id = ?',
            (sheet_id,),
        )
        assert row is not None
        position = int(row["next_pos"])
        now = _now()
        created: list[Row] = []
        for inp in inputs:
            item = Row(
                id=uuid4().hex,
                sheet_id=sheet_id,
                input_name=inp.input_name,
                input_domain=inp.input_domain,
                position=position,
                created_at=now,
                updated_at=now,
            )
            await conn.execute(
                'INSERT INTO "rows" (id, sheet_id, input_name, input_domain, position,'
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    item.sheet_id,
                    item.input_name,
                    item.input_domain,
                    item.position,
                    now,
                    now,
                ),
            )
            created.append(item)
            position += 1
    return created


async def list_rows(db_path: str, sheet_id: str) -> list[Row]:
    async with _read(db_path) as conn:
        rows = await _all(
            conn,
            'SELECT * FROM "rows" WHERE sheet_id = ? AND deleted = 0 ORDER BY position, id',
            (sheet_id,),
        )
    return [_to_row(r) for r in rows]


async def update_row_identity(
    db_path: str,
    row_id: str,
    *,
    identity_status: IdentityStatus,
    resolved_name: str | None = None,
    resolved_domain: str | None = None,
    identity: CompanyIdentity | None = None,
) -> Row | None:
    identity_json = identity.model_dump_json() if identity is not None else None
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            'UPDATE "rows" SET identity_status = ?, resolved_name = ?,'
            " resolved_domain = ?, identity_json = ?, updated_at = ? WHERE id = ?",
            (identity_status, resolved_name, resolved_domain, identity_json, _now(), row_id),
        )
        if cursor.rowcount != 1:
            return None
        row = await _one(conn, 'SELECT * FROM "rows" WHERE id = ?', (row_id,))
    return _to_row(row) if row is not None else None


async def soft_delete_row(db_path: str, row_id: str) -> bool:
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            'UPDATE "rows" SET deleted = 1, updated_at = ? WHERE id = ?',
            (_now(), row_id),
        )
        return cursor.rowcount == 1


# --- columns ---------------------------------------------------------------


async def create_column(
    db_path: str,
    sheet_id: str,
    *,
    name: str,
    prompt_template: str,
    output_type: OutputType,
    source_scope: SourceScope,
    depth: Depth = "standard",
    enum_options: Sequence[str] | None = None,
    depends_on: Sequence[str] = (),
    position: int | None = None,
) -> Column:
    async with _write(db_path) as conn:
        if position is None:
            row = await _one(
                conn,
                'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM "columns" '
                "WHERE sheet_id = ?",
                (sheet_id,),
            )
            assert row is not None
            position = int(row["next_pos"])
        now = _now()
        column = Column(
            id=uuid4().hex,
            sheet_id=sheet_id,
            name=name,
            prompt_template=prompt_template,
            output_type=output_type,
            enum_options=list(enum_options) if enum_options is not None else None,
            source_scope=source_scope,
            depth=depth,
            depends_on=list(depends_on),
            position=position,
            created_at=now,
            updated_at=now,
        )
        await conn.execute(
            'INSERT INTO "columns" (id, sheet_id, name, prompt_template, output_type,'
            " enum_options, source_scope, depth, depends_on, version, position,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                column.id,
                column.sheet_id,
                column.name,
                column.prompt_template,
                column.output_type,
                json.dumps(column.enum_options) if column.enum_options is not None else None,
                column.source_scope,
                column.depth,
                json.dumps(column.depends_on),
                column.version,
                column.position,
                now,
                now,
            ),
        )
    return column


async def get_column(db_path: str, column_id: str) -> Column | None:
    async with _read(db_path) as conn:
        row = await _one(conn, 'SELECT * FROM "columns" WHERE id = ?', (column_id,))
    return _to_column(row) if row is not None else None


async def list_columns(db_path: str, sheet_id: str) -> list[Column]:
    async with _read(db_path) as conn:
        rows = await _all(
            conn,
            'SELECT * FROM "columns" WHERE sheet_id = ? AND deleted = 0 ORDER BY position, id',
            (sheet_id,),
        )
    return [_to_column(r) for r in rows]


async def update_column(
    db_path: str,
    column_id: str,
    *,
    name: str,
    prompt_template: str,
    output_type: OutputType,
    enum_options: Sequence[str] | None = None,
    source_scope: SourceScope,
    depth: Depth = "standard",
    depends_on: Sequence[str] = (),
) -> Column | None:
    """Write a full new column state, bump ``version`` and mark cells stale.

    A prompt edit sets ``stale`` on cells holding an outcome recorded under
    an older version — it never wipes their value. ``depends_on`` must be
    derived from the template by the caller.
    """
    enum_json = json.dumps(list(enum_options)) if enum_options is not None else None
    depends_json = json.dumps(list(depends_on))
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            'UPDATE "columns" SET name = ?, prompt_template = ?, output_type = ?,'
            " enum_options = ?, source_scope = ?, depth = ?, depends_on = ?,"
            " version = version + 1, updated_at = ? WHERE id = ? AND deleted = 0",
            (
                name,
                prompt_template,
                output_type,
                enum_json,
                source_scope,
                depth,
                depends_json,
                _now(),
                column_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = await _one(conn, 'SELECT * FROM "columns" WHERE id = ?', (column_id,))
        assert row is not None
        new_version = int(row["version"])
        await conn.execute(
            "UPDATE cells SET stale = 1, updated_at = ?"
            " WHERE column_id = ? AND column_version < ? AND status IN ('done', 'error')",
            (_now(), column_id, new_version),
        )
    return _to_column(row)


async def soft_delete_column(db_path: str, column_id: str) -> bool:
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            'UPDATE "columns" SET deleted = 1, updated_at = ? WHERE id = ?',
            (_now(), column_id),
        )
        return cursor.rowcount == 1


# --- cells (the job queue) --------------------------------------------------


async def upsert_cell(db_path: str, row_id: str, column_id: str) -> Cell:
    """Ensure a cell exists for the (row, column) pair, never wiping an
    existing one — idle cells are created when rows or columns arrive."""
    async with _write(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO cells (row_id, column_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (row_id, column_id, _now(), _now()),
        )
        row = await _one(
            conn,
            "SELECT * FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
    assert row is not None
    return _to_cell(row)


async def get_cell(db_path: str, row_id: str, column_id: str) -> Cell | None:
    async with _read(db_path) as conn:
        row = await _one(
            conn,
            "SELECT * FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
    return _to_cell(row) if row is not None else None


_QUEUEABLE: dict[RunScope, str] = {
    "empty": "status = 'idle'",
    "stale": "stale = 1 AND status IN ('done', 'error')",
    "error": "status = 'error'",
    "all": "status NOT IN ('queued', 'running')",
}


async def queue_cells(
    db_path: str,
    sheet_id: str,
    scope: RunScope,
    run_id: str | None = None,
) -> int:
    """Move the scope's eligible cells to ``queued``. Returns the count."""
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            f"UPDATE cells SET status = 'queued', run_id = ?, updated_at = ?"
            f" WHERE {_QUEUEABLE[scope]}"
            ' AND row_id IN (SELECT id FROM "rows" WHERE sheet_id = ? AND deleted = 0)'
            ' AND column_id IN (SELECT id FROM "columns" WHERE sheet_id = ? AND deleted = 0)',
            (run_id, _now(), sheet_id, sheet_id),
        )
        return cursor.rowcount


async def claim_cell(
    db_path: str,
    row_id: str,
    column_id: str,
    run_id: str | None = None,
) -> Cell | None:
    """Atomically claim one queued cell — exactly one concurrent caller wins."""
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE cells SET status = 'running', run_id = COALESCE(?, run_id),"
            " updated_at = ? WHERE row_id = ? AND column_id = ? AND status = 'queued'",
            (run_id, _now(), row_id, column_id),
        )
        if cursor.rowcount != 1:
            return None
        row = await _one(
            conn,
            "SELECT * FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
    assert row is not None
    return _to_cell(row)


async def finish_cell(
    db_path: str,
    row_id: str,
    column_id: str,
    *,
    status: CellStatus,
    result: Field[CellValue] | None = None,
    cost: float | None = None,
    rendered_prompt: str | None = None,
    error: str | None = None,
    run_id: str | None = None,
    column_version: int | None = None,
) -> Cell:
    """Record one completed write — cell update, ``revision + 1`` and the
    ``cell_revisions`` append happen in ONE transaction, all or nothing.

    ``column_version`` records which column version produced the result;
    the cell is marked ``stale`` when that is behind the column's current
    version, so a prompt edit mid-run can never masquerade as fresh data.
    """
    result_json = result.model_dump_json() if result is not None else None
    async with _write(db_path) as conn:
        current = await _one(
            conn,
            "SELECT revision, column_version FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
        if current is None:
            raise CellNotFoundError(f"no cell at ({row_id}, {column_id})")
        revision = int(current["revision"]) + 1
        col_ver = int(current["column_version"]) if column_version is None else column_version
        column = await _one(conn, 'SELECT version FROM "columns" WHERE id = ?', (column_id,))
        stale = 1 if column is not None and col_ver < int(column["version"]) else 0
        await conn.execute(
            "UPDATE cells SET status = ?, result_json = ?, column_version = ?,"
            " stale = ?, revision = ?, cost = ?, rendered_prompt = ?, error = ?,"
            " run_id = COALESCE(?, run_id), updated_at = ? WHERE row_id = ? AND column_id = ?",
            (
                status,
                result_json,
                col_ver,
                stale,
                revision,
                cost,
                rendered_prompt,
                error,
                run_id,
                _now(),
                row_id,
                column_id,
            ),
        )
        await conn.execute(
            "INSERT INTO cell_revisions"
            " (row_id, column_id, revision, result_json, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, column_id, revision, result_json, error, _now()),
        )
        row = await _one(
            conn,
            "SELECT * FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
    assert row is not None
    return _to_cell(row)


async def requeue_orphans(db_path: str) -> int:
    """Startup recovery: every ``running`` cell goes back to ``queued``."""
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE cells SET status = 'queued', updated_at = ? WHERE status = 'running'",
            (_now(),),
        )
        return cursor.rowcount


async def mark_blocked(db_path: str, row_id: str, column_id: str, reason: str) -> Cell | None:
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE cells SET status = 'blocked', error = ?, updated_at = ?"
            " WHERE row_id = ? AND column_id = ?",
            (reason, _now(), row_id, column_id),
        )
        if cursor.rowcount != 1:
            return None
        row = await _one(
            conn,
            "SELECT * FROM cells WHERE row_id = ? AND column_id = ?",
            (row_id, column_id),
        )
    return _to_cell(row) if row is not None else None


async def mark_cells_stale(db_path: str, column_ids: Sequence[str]) -> int:
    """Mark done/error cells on the given columns stale.

    Used when an upstream prompt edit invalidates downstream rendered
    prompts — stale is set, values are never wiped.
    """
    if not column_ids:
        return 0
    placeholders = ", ".join("?" for _ in column_ids)
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            f"UPDATE cells SET stale = 1, updated_at = ? WHERE column_id IN ({placeholders})"
            " AND status IN ('done', 'error')",
            (_now(), *column_ids),
        )
        return cursor.rowcount


async def requeue_cells(db_path: str, keys: Sequence[tuple[str, str]]) -> int:
    """Graceful shutdown: specific ``running`` cells back to ``queued`` —
    the scoped counterpart of :func:`requeue_orphans`."""
    count = 0
    async with _write(db_path) as conn:
        for row_id, column_id in keys:
            cursor = await conn.execute(
                "UPDATE cells SET status = 'queued', updated_at = ?"
                " WHERE row_id = ? AND column_id = ? AND status = 'running'",
                (_now(), row_id, column_id),
            )
            count += cursor.rowcount
    return count


async def list_cell_revisions(db_path: str, row_id: str, column_id: str) -> list[CellRevision]:
    async with _read(db_path) as conn:
        rows = await _all(
            conn,
            "SELECT * FROM cell_revisions WHERE row_id = ? AND column_id = ? ORDER BY revision",
            (row_id, column_id),
        )
    return [_to_cell_revision(r) for r in rows]


# --- runs -------------------------------------------------------------------


async def create_run(
    db_path: str,
    sheet_id: str,
    *,
    cap: float | None = None,
    reason: str | None = None,
) -> Run:
    now = _now()
    run = Run(id=uuid4().hex, sheet_id=sheet_id, cap=cap, reason=reason, created_at=now)
    async with _write(db_path) as conn:
        await conn.execute(
            "INSERT INTO runs (id, sheet_id, cap, spent, cancelled, reason, created_at)"
            " VALUES (?, ?, ?, 0, 0, ?, ?)",
            (run.id, run.sheet_id, run.cap, run.reason, now),
        )
    return run


async def get_run(db_path: str, run_id: str) -> Run | None:
    async with _read(db_path) as conn:
        row = await _one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
    return _to_run(row) if row is not None else None


async def cancel_run(db_path: str, run_id: str) -> Run | None:
    """Flag the run cancelled and return its still-queued cells to idle."""
    async with _write(db_path) as conn:
        cursor = await conn.execute("UPDATE runs SET cancelled = 1 WHERE id = ?", (run_id,))
        if cursor.rowcount != 1:
            return None
        await conn.execute(
            "UPDATE cells SET status = 'idle', run_id = NULL, updated_at = ?"
            " WHERE run_id = ? AND status = 'queued'",
            (_now(), run_id),
        )
        row = await _one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
    return _to_run(row) if row is not None else None


async def stop_run(db_path: str, run_id: str, reason: str) -> Run | None:
    """Stop a run early with a reason (cancel, cost cap, provider
    401/402): flag it cancelled and return its still-queued cells to
    idle — running cells are left alone."""
    async with _write(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE runs SET cancelled = 1, reason = ? WHERE id = ?",
            (reason, run_id),
        )
        if cursor.rowcount != 1:
            return None
        await conn.execute(
            "UPDATE cells SET status = 'idle', run_id = NULL, updated_at = ?"
            " WHERE run_id = ? AND status = 'queued'",
            (_now(), run_id),
        )
        row = await _one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
    return _to_run(row) if row is not None else None


async def add_run_spend(db_path: str, run_id: str, amount: float) -> None:
    """Add provider-reported spend to a run's ``spent`` total."""
    async with _write(db_path) as conn:
        await conn.execute(
            "UPDATE runs SET spent = spent + ? WHERE id = ?",
            (amount, run_id),
        )


# --- snapshot ---------------------------------------------------------------


async def snapshot(db_path: str, sheet_id: str) -> SheetSnapshot | None:
    """The full sheet state for one grid load — sheet, non-deleted rows and
    columns, every cell between them, and the latest run."""
    async with _read(db_path) as conn:
        sheet_row = await _one(conn, "SELECT * FROM sheets WHERE id = ?", (sheet_id,))
        if sheet_row is None:
            return None
        rows = await _all(
            conn,
            'SELECT * FROM "rows" WHERE sheet_id = ? AND deleted = 0 ORDER BY position, id',
            (sheet_id,),
        )
        columns = await _all(
            conn,
            'SELECT * FROM "columns" WHERE sheet_id = ? AND deleted = 0 ORDER BY position, id',
            (sheet_id,),
        )
        cells = await _all(
            conn,
            "SELECT cells.* FROM cells"
            ' JOIN "rows" r ON r.id = cells.row_id'
            ' JOIN "columns" c ON c.id = cells.column_id'
            " WHERE r.sheet_id = ? AND r.deleted = 0 AND c.deleted = 0",
            (sheet_id,),
        )
        latest_run = await _one(
            conn,
            "SELECT * FROM runs WHERE sheet_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (sheet_id,),
        )
    return SheetSnapshot(
        sheet=_to_sheet(sheet_row),
        rows=[_to_row(r) for r in rows],
        columns=[_to_column(c) for c in columns],
        cells=[_to_cell(c) for c in cells],
        latest_run=_to_run(latest_run) if latest_run is not None else None,
    )
