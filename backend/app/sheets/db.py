"""SQLite connections for the sheets store (plan §4.1, decision D2).

Raw ``aiosqlite``, no ORM and no migrations framework. One connection per
unit of work — a shared connection would let concurrent tasks interleave
one transaction. Every connection enables foreign keys, WAL journaling and
a busy timeout.

The schema is created idempotently by :func:`init_db`, which stamps
``PRAGMA user_version`` on first create. :func:`connect` refuses a database
whose version does not match :data:`SCHEMA_VERSION`, so a mismatched DB
fails loudly instead of silently misbehaving (migrations land before real
user data exists).
"""

import aiosqlite

SCHEMA_VERSION = 2

_BUSY_TIMEOUT_MS = 5000

# Schema v2 (integration plan §5.5): provider-keyed integration tables.
# Kept as a separate constant so the v1 → v2 migration branch applies
# exactly this DDL; fresh databases get it appended to ``_DDL`` below.
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS integration_connections (
    provider TEXT PRIMARY KEY,              -- 'hubspot'
    portal_id TEXT,
    access_token_enc TEXT NOT NULL,         -- Fernet-encrypted
    refresh_token_enc TEXT NOT NULL,
    token_expires_at TEXT NOT NULL,
    scopes TEXT NOT NULL,
    mock INTEGER NOT NULL DEFAULT 0,
    connected_at TEXT NOT NULL,
    last_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    expires_at TEXT NOT NULL,               -- now + 10 min; consumed = deleted
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_links (
    provider TEXT NOT NULL,
    object_type TEXT NOT NULL,              -- 'company' | 'contact'
    remote_id TEXT NOT NULL,
    sheet_id TEXT REFERENCES sheets(id) ON DELETE CASCADE,
    row_id TEXT REFERENCES "rows"(id) ON DELETE CASCADE,  -- NULL for contacts (snapshot-only)
    properties_json TEXT NOT NULL,          -- remote property snapshot for refresh diffs
    remote_updated_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider, object_type, remote_id)
);

CREATE TABLE IF NOT EXISTS integration_field_map (
    provider TEXT NOT NULL,
    object_type TEXT NOT NULL,
    column_name TEXT NOT NULL,              -- Outmate column name
    remote_property TEXT NOT NULL,          -- 'outmate_*' only (enforced in mapping.py)
    direction TEXT NOT NULL DEFAULT 'push', -- 'push' | 'pull' | 'both'
    owner TEXT NOT NULL DEFAULT 'outmate',  -- 'outmate' | 'hubspot'
    PRIMARY KEY (provider, object_type, column_name)
);

CREATE TABLE IF NOT EXISTS integration_push_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    row_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    property TEXT NOT NULL,
    value TEXT,
    status TEXT NOT NULL,                   -- 'ok' | 'error'
    error TEXT,
    created_at TEXT NOT NULL
);
"""

_DDL = (
    """
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
    + _DDL_V2
)


class SchemaVersionError(RuntimeError):
    """A sheets DB whose ``user_version`` is not :data:`SCHEMA_VERSION`."""


async def _user_version(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def _apply_pragmas(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")


async def init_db(db_path: str) -> None:
    """Create the schema if needed and stamp ``user_version``.

    Idempotent: a database already at :data:`SCHEMA_VERSION` is untouched,
    a fresh database (``user_version`` 0) receives the full DDL, a database
    at ``user_version`` 1 is migrated by applying only the v2 DDL and
    re-stamping, and any other version is refused with
    :class:`SchemaVersionError`.
    """
    conn = await aiosqlite.connect(db_path, isolation_level=None)
    try:
        await _apply_pragmas(conn)
        version = await _user_version(conn)
        if version == SCHEMA_VERSION:
            return
        if version == 1:
            await conn.executescript(_DDL_V2)
            await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        if version != 0:
            raise SchemaVersionError(
                f"sheets DB at {db_path!r} has user_version={version}, "
                f"expected 0 (new), 1 (migratable) or {SCHEMA_VERSION}; "
                "refusing to initialize"
            )
        await conn.executescript(_DDL)
        await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        await conn.close()


async def connect(db_path: str) -> aiosqlite.Connection:
    """Open one connection with the standard pragmas.

    Fails loudly with :class:`SchemaVersionError` when ``user_version`` is
    not :data:`SCHEMA_VERSION`. Callers must close the connection; it is
    never shared across tasks.
    """
    conn = await aiosqlite.connect(db_path, isolation_level=None)
    try:
        await _apply_pragmas(conn)
        version = await _user_version(conn)
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"sheets DB at {db_path!r} has user_version={version}, "
                f"expected {SCHEMA_VERSION}; refusing to open"
            )
        conn.row_factory = aiosqlite.Row
    except BaseException:
        await conn.close()
        raise
    return conn
