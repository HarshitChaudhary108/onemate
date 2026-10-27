"""Integration schema v2: fresh create, v1 → v2 migration, version guard.

The v1 DDL below is a verbatim snapshot of the pre-migration schema —
building a real v1 database is the only honest way to test the migration
branch.
"""

from pathlib import Path

import aiosqlite
import pytest

from app.sheets.db import SCHEMA_VERSION, SchemaVersionError, connect, init_db

_V1_TABLES = {"sheets", "rows", "columns", "cells", "cell_revisions", "runs"}
_V2_TABLES = {
    "integration_connections",
    "oauth_states",
    "integration_links",
    "integration_field_map",
    "integration_push_log",
}

# Pre-v2 schema, verbatim from the v1 _DDL.
_DDL_V1 = """
CREATE TABLE IF NOT EXISTS sheets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "rows" (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    input_name TEXT,
    input_domain TEXT,
    position INTEGER NOT NULL,
    identity_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (identity_status IN ('pending', 'resolved', 'unresolved', 'ambiguous', 'error')),
    resolved_name TEXT,
    resolved_domain TEXT,
    identity_json TEXT,
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "columns" (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    output_type TEXT NOT NULL
        CHECK (output_type IN ('text', 'number', 'boolean', 'enum', 'url', 'date')),
    enum_options TEXT,
    strategy TEXT NOT NULL DEFAULT 'research' CHECK (strategy = 'research'),
    source_scope TEXT NOT NULL
        CHECK (source_scope IN ('company_site', 'web', 'news', 'people')),
    depth TEXT NOT NULL DEFAULT 'standard' CHECK (depth IN ('standard', 'deep')),
    depends_on TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    cap REAL,
    spent REAL NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0, 1)),
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cells (
    row_id TEXT NOT NULL REFERENCES "rows"(id) ON DELETE CASCADE,
    column_id TEXT NOT NULL REFERENCES "columns"(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK (status IN ('idle', 'queued', 'running', 'done', 'error', 'blocked')),
    result_json TEXT,
    column_version INTEGER NOT NULL DEFAULT 1,
    stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 0,
    cost REAL,
    rendered_prompt TEXT,
    error TEXT,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (row_id, column_id)
);

CREATE TABLE IF NOT EXISTS cell_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id TEXT NOT NULL,
    column_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (row_id, column_id, revision),
    FOREIGN KEY (row_id, column_id) REFERENCES cells(row_id, column_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cells_status ON cells(status);
"""


async def _table_names(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    return {r[0] for r in await cursor.fetchall()}


async def _make_v1_db(path: str) -> None:
    """Build a real schema-v1 database with one sheet and one row."""
    conn = await aiosqlite.connect(path, isolation_level=None)
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.executescript(_DDL_V1)
        await conn.execute("PRAGMA user_version = 1")
        await conn.execute(
            "INSERT INTO sheets (id, name, created_at, updated_at)"
            " VALUES ('sheet1', 'Migrated Sheet', '2026-01-01T00:00:00+00:00',"
            " '2026-01-01T00:00:00+00:00')"
        )
        await conn.execute(
            'INSERT INTO "rows" (id, sheet_id, input_name, position, created_at,'
            " updated_at) VALUES ('row1', 'sheet1', 'Example Robotics Ltd', 0,"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
    finally:
        await conn.close()


async def test_fresh_init_creates_v2_schema(tmp_path: Path) -> None:
    path = str(tmp_path / "sheets.db")
    await init_db(path)
    conn = await connect(path)
    try:
        assert _V1_TABLES | _V2_TABLES <= await _table_names(conn)
        cursor = await conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == SCHEMA_VERSION
        assert SCHEMA_VERSION == 2
    finally:
        await conn.close()


async def test_v1_database_migrates_to_v2(tmp_path: Path) -> None:
    path = str(tmp_path / "sheets.db")
    await _make_v1_db(path)

    await init_db(path)

    conn = await connect(path)
    try:
        cursor = await conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 2
        assert _V2_TABLES <= await _table_names(conn)
        # Existing sheets/rows data survives the migration.
        cursor = await conn.execute('SELECT input_name FROM "rows" WHERE id = ?', ("row1",))
        row = await cursor.fetchone()
        assert row is not None and row[0] == "Example Robotics Ltd"
    finally:
        await conn.close()

    # The migrated DB keeps working through the normal connection guard.
    await init_db(path)  # idempotent — already at SCHEMA_VERSION


async def test_unknown_version_still_raises(tmp_path: Path) -> None:
    path = str(tmp_path / "sheets.db")
    await _make_v1_db(path)
    conn = await aiosqlite.connect(path, isolation_level=None)
    try:
        await conn.execute("PRAGMA user_version = 42")
    finally:
        await conn.close()

    with pytest.raises(SchemaVersionError, match="user_version"):
        await init_db(path)
    with pytest.raises(SchemaVersionError, match="user_version"):
        await connect(path)
