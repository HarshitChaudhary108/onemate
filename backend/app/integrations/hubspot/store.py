"""Repository functions for the integration tables (plan §5.5).

Mirrors :mod:`app.sheets.repo`: every function opens its own connection
through :func:`app.sheets.db.connect` — one connection per unit of work —
and writes run under ``BEGIN IMMEDIATE``.

OAuth tokens are encrypted/decrypted at this boundary only: callers pass a
cipher with the ``cryptography.fernet.Fernet`` shape (structural
:class:`TokenCipher` — the dependency lands with the OAuth milestone) and
raw ciphertext never leaves this module. ``scopes`` is stored as the
canonical OAuth space-delimited string.
"""

import json
import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import aiosqlite

from app.integrations.hubspot.models import (
    ConnectionRecord,
    IntegrationLink,
    PushLogEntry,
    RemoteObjectType,
)
from app.sheets.db import connect

_COUNTS_KEY: dict[str, str] = {"company": "companies", "contact": "contacts"}
"""Map link ``object_type`` values onto the status endpoint's count keys."""


class TokenCipher(Protocol):
    """Structural shape of ``cryptography.fernet.Fernet``.

    ``cryptography`` is not a dependency yet (it lands with the OAuth
    milestone); typing the two methods used here keeps this module
    honest until then.
    """

    def encrypt(self, data: bytes) -> bytes: ...

    def decrypt(self, token: bytes) -> bytes: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


@asynccontextmanager
async def _read(db: str) -> AsyncIterator[aiosqlite.Connection]:
    conn = await connect(db)
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def _write(db: str) -> AsyncIterator[aiosqlite.Connection]:
    """One connection holding the write lock for a whole unit of work."""
    conn = await connect(db)
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


def _to_connection(r: aiosqlite.Row, fernet: TokenCipher) -> ConnectionRecord:
    data = _data(r)
    data["access_token"] = fernet.decrypt(str(data.pop("access_token_enc")).encode("ascii")).decode(
        "utf-8"
    )
    data["refresh_token"] = fernet.decrypt(
        str(data.pop("refresh_token_enc")).encode("ascii")
    ).decode("utf-8")
    data["scopes"] = str(data["scopes"]).split()
    return ConnectionRecord.model_validate(data)


def _to_link(r: aiosqlite.Row) -> IntegrationLink:
    data = _data(r)
    data["properties"] = json.loads(str(data.pop("properties_json")))
    return IntegrationLink.model_validate(data)


def _to_push_log(r: aiosqlite.Row) -> PushLogEntry:
    return PushLogEntry.model_validate(_data(r))


# --- integration_connections ----------------------------------------------


async def save_connection(db: str, fernet: TokenCipher, record: ConnectionRecord) -> None:
    """Insert or replace a provider connection; tokens are encrypted here."""
    access_enc = fernet.encrypt(record.access_token.get_secret_value().encode("utf-8"))
    refresh_enc = fernet.encrypt(record.refresh_token.get_secret_value().encode("utf-8"))
    async with _write(db) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO integration_connections"
            " (provider, portal_id, access_token_enc, refresh_token_enc,"
            " token_expires_at, scopes, mock, connected_at, last_sync_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.provider,
                record.portal_id,
                access_enc.decode("ascii"),
                refresh_enc.decode("ascii"),
                record.token_expires_at.isoformat(),
                " ".join(record.scopes),
                int(record.mock),
                record.connected_at.isoformat(),
                record.last_sync_at.isoformat() if record.last_sync_at is not None else None,
            ),
        )


async def get_connection(db: str, fernet: TokenCipher, provider: str) -> ConnectionRecord | None:
    """Load and decrypt a connection, or ``None`` when not connected."""
    async with _read(db) as conn:
        row = await _one(
            conn,
            "SELECT * FROM integration_connections WHERE provider = ?",
            (provider,),
        )
    return _to_connection(row, fernet) if row is not None else None


async def delete_connection(db: str, provider: str) -> bool:
    """Disconnect a provider; links are kept so a reconnect can resume."""
    async with _write(db) as conn:
        cursor = await conn.execute(
            "DELETE FROM integration_connections WHERE provider = ?",
            (provider,),
        )
        return cursor.rowcount == 1


async def touch_last_sync(db: str, provider: str, at: datetime) -> bool:
    """Stamp ``last_sync_at`` after a successful import/refresh."""
    async with _write(db) as conn:
        cursor = await conn.execute(
            "UPDATE integration_connections SET last_sync_at = ? WHERE provider = ?",
            (at.isoformat(), provider),
        )
        return cursor.rowcount == 1


# --- oauth_states -----------------------------------------------------------


async def create_state(db: str, provider: str, ttl_minutes: int = 10) -> str:
    """Mint a single-use OAuth state token valid for ``ttl_minutes``."""
    state = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    async with _write(db) as conn:
        await conn.execute(
            "INSERT INTO oauth_states (state, provider, expires_at, created_at)"
            " VALUES (?, ?, ?, ?)",
            (state, provider, (now + timedelta(minutes=ttl_minutes)).isoformat(), now.isoformat()),
        )
    return state


async def consume_state(db: str, provider: str, state: str) -> bool:
    """Consume a state token: True only if it existed and was unexpired.

    A matching row is deleted on lookup — tokens are single-use. Expired
    stragglers are cleaned up opportunistically before the check.
    """
    async with _write(db) as conn:
        await conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (_now(),))
        cursor = await conn.execute(
            "DELETE FROM oauth_states WHERE state = ? AND provider = ?",
            (state, provider),
        )
        return cursor.rowcount == 1


# --- integration_links --------------------------------------------------------


async def upsert_link(db: str, link: IntegrationLink) -> None:
    """Insert or refresh a remote-object link, keyed by
    ``(provider, object_type, remote_id)`` — re-importing the same object
    updates its snapshot instead of duplicating it."""
    async with _write(db) as conn:
        await conn.execute(
            "INSERT INTO integration_links"
            " (provider, object_type, remote_id, sheet_id, row_id,"
            " properties_json, remote_updated_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (provider, object_type, remote_id) DO UPDATE SET"
            " sheet_id = excluded.sheet_id, row_id = excluded.row_id,"
            " properties_json = excluded.properties_json,"
            " remote_updated_at = excluded.remote_updated_at",
            (
                link.provider,
                link.object_type,
                link.remote_id,
                link.sheet_id,
                link.row_id,
                json.dumps(link.properties),
                link.remote_updated_at.isoformat() if link.remote_updated_at is not None else None,
                link.created_at.isoformat(),
            ),
        )


async def get_link(
    db: str, provider: str, object_type: RemoteObjectType, remote_id: str
) -> IntegrationLink | None:
    async with _read(db) as conn:
        row = await _one(
            conn,
            "SELECT * FROM integration_links"
            " WHERE provider = ? AND object_type = ? AND remote_id = ?",
            (provider, object_type, remote_id),
        )
    return _to_link(row) if row is not None else None


async def links_for_sheet(db: str, provider: str, sheet_id: str) -> list[IntegrationLink]:
    async with _read(db) as conn:
        rows = await _all(
            conn,
            "SELECT * FROM integration_links WHERE provider = ? AND sheet_id = ?"
            " ORDER BY created_at, remote_id",
            (provider, sheet_id),
        )
    return [_to_link(r) for r in rows]


async def link_for_row(
    db: str, provider: str, object_type: RemoteObjectType, row_id: str
) -> IntegrationLink | None:
    async with _read(db) as conn:
        row = await _one(
            conn,
            "SELECT * FROM integration_links WHERE provider = ? AND object_type = ? AND row_id = ?",
            (provider, object_type, row_id),
        )
    return _to_link(row) if row is not None else None


async def update_link_snapshot(
    db: str,
    provider: str,
    object_type: RemoteObjectType,
    remote_id: str,
    properties: dict[str, str | None],
    remote_updated_at: datetime | None,
) -> bool:
    """Replace a link's remote snapshot after a refresh diff."""
    async with _write(db) as conn:
        cursor = await conn.execute(
            "UPDATE integration_links SET properties_json = ?, remote_updated_at = ?"
            " WHERE provider = ? AND object_type = ? AND remote_id = ?",
            (
                json.dumps(properties),
                remote_updated_at.isoformat() if remote_updated_at is not None else None,
                provider,
                object_type,
                remote_id,
            ),
        )
        return cursor.rowcount == 1


# --- integration_push_log -----------------------------------------------------


async def append_push_log(db: str, entry: PushLogEntry) -> PushLogEntry:
    """Append one push outcome; ``entry.id`` is ignored — SQLite assigns it."""
    async with _write(db) as conn:
        cursor = await conn.execute(
            "INSERT INTO integration_push_log"
            " (provider, row_id, remote_id, property, value, status, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.provider,
                entry.row_id,
                entry.remote_id,
                entry.property,
                entry.value,
                entry.status,
                entry.error,
                entry.created_at.isoformat(),
            ),
        )
        row = await _one(
            conn,
            "SELECT * FROM integration_push_log WHERE id = ?",
            (cursor.lastrowid,),
        )
    assert row is not None
    return _to_push_log(row)


async def push_log_for_sheet(
    db: str, provider: str, sheet_id: str, limit: int = 50
) -> list[PushLogEntry]:
    """Most recent push outcomes for a sheet, newest first."""
    async with _read(db) as conn:
        rows = await _all(
            conn,
            "SELECT p.* FROM integration_push_log p"
            ' JOIN "rows" r ON r.id = p.row_id'
            " WHERE p.provider = ? AND r.sheet_id = ?"
            " ORDER BY p.id DESC LIMIT ?",
            (provider, sheet_id, limit),
        )
    return [_to_push_log(r) for r in rows]


# --- status aggregates ---------------------------------------------------------


async def import_counts(db: str, provider: str) -> dict[str, int]:
    """Linked-object totals for the status endpoint:
    ``{"companies": n, "contacts": m}``."""
    counts = {"companies": 0, "contacts": 0}
    async with _read(db) as conn:
        rows = await _all(
            conn,
            "SELECT object_type, COUNT(*) AS n FROM integration_links"
            " WHERE provider = ? GROUP BY object_type",
            (provider,),
        )
    for r in rows:
        key = _COUNTS_KEY.get(str(r["object_type"]))
        if key is not None:
            counts[key] = int(r["n"])
    return counts
