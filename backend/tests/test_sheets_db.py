"""Sheets DB plumbing: pragma application, idempotent init, version guard.

Tests use real files under ``tmp_path`` — ``:memory:`` is not shared across
connections, and every repo function opens its own connection.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from app.sheets.db import SCHEMA_VERSION, SchemaVersionError, connect, init_db


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "sheets.db")
    await init_db(path)
    return path


async def test_init_creates_schema_and_is_idempotent(db_path: str) -> None:
    await init_db(db_path)  # second init is a no-op, not an error
    conn = await connect(db_path)
    try:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {r[0] for r in await cursor.fetchall()}
    finally:
        await conn.close()
    assert {"sheets", "rows", "columns", "cells", "cell_revisions", "runs"} <= tables


async def test_connection_applies_pragmas(db_path: str) -> None:
    conn = await connect(db_path)
    try:
        cursor = await conn.execute("PRAGMA foreign_keys")
        assert (await cursor.fetchone())[0] == 1
        cursor = await conn.execute("PRAGMA journal_mode")
        assert (await cursor.fetchone())[0] == "wal"
        cursor = await conn.execute("PRAGMA busy_timeout")
        assert (await cursor.fetchone())[0] == 5000
        cursor = await conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == SCHEMA_VERSION
    finally:
        await conn.close()


async def test_user_version_guard_refuses_mismatched_db(db_path: str) -> None:
    conn = await connect(db_path)
    await conn.execute("PRAGMA user_version = 42")
    await conn.close()

    with pytest.raises(SchemaVersionError, match="user_version"):
        await connect(db_path)
    with pytest.raises(SchemaVersionError, match="user_version"):
        await init_db(db_path)


async def test_connect_refuses_uninitialized_db(tmp_path: Path) -> None:
    with pytest.raises(SchemaVersionError):
        await connect(str(tmp_path / "fresh.db"))
