"""Store functions for the integration tables (plan §5.5).

Connection/crypto tests gate on ``cryptography`` — it is not a dependency
until the OAuth milestone, so they skip cleanly here; states, links, push
log and counts run against the real tmp DB.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from app.integrations.hubspot import store
from app.integrations.hubspot.models import (
    ConnectionRecord,
    IntegrationLink,
    PushLogEntry,
)
from app.sheets import repo
from app.sheets.db import connect
from app.sheets.models import RowInput

PROVIDER = "hubspot"


@pytest.fixture
def fernet() -> Any:
    """A real Fernet on a fixed test-only key — skips until ``cryptography``
    is installed (OAuth milestone owns the dependency pin)."""
    fernet_mod = pytest.importorskip(
        "cryptography.fernet", reason="cryptography not installed until the OAuth milestone"
    )
    return fernet_mod.Fernet(fernet_mod.Fernet.generate_key())


def make_connection(**overrides: Any) -> ConnectionRecord:
    base: dict[str, Any] = {
        "provider": PROVIDER,
        "portal_id": "portal-123",
        "access_token": SecretStr("pat-na1-synthetic-access"),
        "refresh_token": SecretStr("synthetic-refresh"),
        "token_expires_at": datetime(2026, 1, 1, tzinfo=UTC),
        "scopes": ["crm.objects.companies.read", "crm.objects.contacts.read"],
        "mock": False,
        "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_sync_at": None,
    }
    base.update(overrides)
    return ConnectionRecord(**base)


def make_link(**overrides: Any) -> IntegrationLink:
    base: dict[str, Any] = {
        "provider": PROVIDER,
        "object_type": "company",
        "remote_id": "hs-company-1",
        "sheet_id": "unset",
        "row_id": "unset",
        "properties": {"name": "Example Robotics Ltd", "domain": "example-robotics.test"},
        "remote_updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return IntegrationLink(**base)


# --- connections (crypto-gated) -----------------------------------------------


async def test_connection_roundtrip_and_delete(db: str, fernet: Any) -> None:
    record = make_connection()
    await store.save_connection(db, fernet, record)

    got = await store.get_connection(db, fernet, PROVIDER)
    assert got is not None
    assert got.access_token.get_secret_value() == "pat-na1-synthetic-access"
    assert got.refresh_token.get_secret_value() == "synthetic-refresh"
    assert got.portal_id == "portal-123"
    assert got.scopes == ["crm.objects.companies.read", "crm.objects.contacts.read"]
    assert got.mock is False
    assert got.connected_at == record.connected_at
    assert got.token_expires_at == record.token_expires_at
    assert got.last_sync_at is None

    # save again on the same provider = replace
    await store.save_connection(db, fernet, make_connection(portal_id="portal-999"))
    got = await store.get_connection(db, fernet, PROVIDER)
    assert got is not None and got.portal_id == "portal-999"

    assert await store.delete_connection(db, PROVIDER) is True
    assert await store.get_connection(db, fernet, PROVIDER) is None
    assert await store.delete_connection(db, PROVIDER) is False


async def test_tokens_stored_as_ciphertext(db: str, fernet: Any) -> None:
    await store.save_connection(db, fernet, make_connection())
    conn = await connect(db)
    try:
        cursor = await conn.execute(
            "SELECT access_token_enc, refresh_token_enc FROM integration_connections"
            " WHERE provider = ?",
            (PROVIDER,),
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert row is not None
    # The stored column is ciphertext — never the plaintext token.
    assert row["access_token_enc"] != "pat-na1-synthetic-access"
    assert "pat-na1-synthetic-access" not in row["access_token_enc"]
    assert "synthetic-refresh" not in row["refresh_token_enc"]


async def test_get_connection_missing_returns_none(db: str, fernet: Any) -> None:
    assert await store.get_connection(db, fernet, PROVIDER) is None


async def test_touch_last_sync(db: str, fernet: Any) -> None:
    await store.save_connection(db, fernet, make_connection())
    at = datetime(2026, 2, 1, tzinfo=UTC)
    assert await store.touch_last_sync(db, PROVIDER, at) is True
    got = await store.get_connection(db, fernet, PROVIDER)
    assert got is not None and got.last_sync_at == at
    assert await store.touch_last_sync(db, "unknown-provider", at) is False


# --- oauth_states --------------------------------------------------------------


async def test_state_single_use_and_expiry(db: str) -> None:
    state = await store.create_state(db, PROVIDER)
    assert len(state) >= 32

    assert await store.consume_state(db, PROVIDER, state) is True
    # single-use: the row is gone after the first consume
    assert await store.consume_state(db, PROVIDER, state) is False
    # missing state
    assert await store.consume_state(db, PROVIDER, "nonsense") is False
    # wrong provider cannot consume
    other = await store.create_state(db, PROVIDER)
    assert await store.consume_state(db, "salesforce", other) is False
    # expired state fails
    expired = await store.create_state(db, PROVIDER, ttl_minutes=-1)
    assert await store.consume_state(db, PROVIDER, expired) is False


# --- integration_links ----------------------------------------------------------


async def test_link_upsert_get_and_sheet_listing(db: str) -> None:
    sheet = await repo.create_sheet(db, "Test Sheet")
    rows = await repo.create_rows(db, sheet.id, [RowInput(input_name="Example Robotics Ltd")])
    row_id = rows[0].id

    link = make_link(sheet_id=sheet.id, row_id=row_id)
    await store.upsert_link(db, link)

    got = await store.get_link(db, PROVIDER, "company", link.remote_id)
    assert got == link

    # re-importing the same remote object updates the snapshot, not a twin
    updated = make_link(
        sheet_id=sheet.id,
        row_id=row_id,
        properties={"name": "Example Robotics Ltd", "employee_count": "42"},
    )
    await store.upsert_link(db, updated)
    got = await store.get_link(db, PROVIDER, "company", link.remote_id)
    assert got is not None
    assert got.properties == {"name": "Example Robotics Ltd", "employee_count": "42"}
    assert len(await store.links_for_sheet(db, PROVIDER, sheet.id)) == 1

    links = await store.links_for_sheet(db, PROVIDER, sheet.id)
    assert [item.remote_id for item in links] == [link.remote_id]
    assert await store.links_for_sheet(db, PROVIDER, "no-such-sheet") == []

    by_row = await store.link_for_row(db, PROVIDER, "company", row_id)
    assert by_row is not None and by_row.remote_id == link.remote_id
    assert await store.link_for_row(db, PROVIDER, "contact", row_id) is None


async def test_contact_link_has_null_row(db: str) -> None:
    sheet = await repo.create_sheet(db, "Contacts")
    await store.upsert_link(
        db,
        make_link(
            object_type="contact",
            remote_id="hs-contact-7",
            sheet_id=sheet.id,
            row_id=None,
            properties={"email": "ada@example-robotics.test", "jobtitle": "CTO"},
        ),
    )
    got = await store.get_link(db, PROVIDER, "contact", "hs-contact-7")
    assert got is not None
    assert got.row_id is None
    assert got.properties["email"] == "ada@example-robotics.test"


async def test_links_cascade_on_sheet_delete(db: str) -> None:
    sheet = await repo.create_sheet(db, "Doomed")
    rows = await repo.create_rows(db, sheet.id, [RowInput(input_name="X")])
    await store.upsert_link(db, make_link(sheet_id=sheet.id, row_id=rows[0].id))
    await store.upsert_link(
        db,
        make_link(
            object_type="contact",
            remote_id="hs-contact-1",
            sheet_id=sheet.id,
            row_id=None,
        ),
    )
    assert len(await store.links_for_sheet(db, PROVIDER, sheet.id)) == 2

    assert await repo.delete_sheet(db, sheet.id) is True
    assert await store.links_for_sheet(db, PROVIDER, sheet.id) == []
    assert await store.get_link(db, PROVIDER, "company", "hs-company-1") is None
    assert await store.get_link(db, PROVIDER, "contact", "hs-contact-1") is None


async def test_update_link_snapshot(db: str) -> None:
    sheet = await repo.create_sheet(db, "S")
    rows = await repo.create_rows(db, sheet.id, [RowInput()])
    await store.upsert_link(db, make_link(sheet_id=sheet.id, row_id=rows[0].id))

    at = datetime(2026, 3, 1, tzinfo=UTC)
    assert (
        await store.update_link_snapshot(
            db, PROVIDER, "company", "hs-company-1", {"name": "Renamed Ltd"}, at
        )
        is True
    )
    got = await store.get_link(db, PROVIDER, "company", "hs-company-1")
    assert got is not None
    assert got.properties == {"name": "Renamed Ltd"}
    assert got.remote_updated_at == at

    assert await store.update_link_snapshot(db, PROVIDER, "company", "ghost", {}, at) is False


# --- integration_push_log --------------------------------------------------------


async def test_push_log_append_and_sheet_listing(db: str) -> None:
    sheet = await repo.create_sheet(db, "S")
    rows = await repo.create_rows(
        db, sheet.id, [RowInput(input_name="A"), RowInput(input_name="B")]
    )
    e1 = PushLogEntry(
        provider=PROVIDER,
        row_id=rows[0].id,
        remote_id="hs-1",
        property="outmate_headcount",
        value="42",
        status="ok",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    e2 = PushLogEntry(
        provider=PROVIDER,
        row_id=rows[1].id,
        remote_id="hs-2",
        property="outmate_headcount",
        value=None,
        status="error",
        error="hubspot_rate_limited",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    stored1 = await store.append_push_log(db, e1)
    stored2 = await store.append_push_log(db, e2)
    assert stored1.id != 0
    assert stored2.id > stored1.id

    entries = await store.push_log_for_sheet(db, PROVIDER, sheet.id)
    assert [e.id for e in entries] == [stored2.id, stored1.id]  # newest first
    assert entries[0].status == "error"
    assert entries[0].error == "hubspot_rate_limited"
    assert entries[1].value == "42"

    limited = await store.push_log_for_sheet(db, PROVIDER, sheet.id, limit=1)
    assert [e.id for e in limited] == [stored2.id]

    # a second sheet only sees its own rows' pushes
    other = await repo.create_sheet(db, "Other")
    assert await store.push_log_for_sheet(db, PROVIDER, other.id) == []


# --- import_counts ----------------------------------------------------------------


async def test_import_counts(db: str) -> None:
    assert await store.import_counts(db, PROVIDER) == {"companies": 0, "contacts": 0}

    sheet = await repo.create_sheet(db, "S")
    rows = await repo.create_rows(db, sheet.id, [RowInput(), RowInput()])
    await store.upsert_link(db, make_link(remote_id="hs-c1", sheet_id=sheet.id, row_id=rows[0].id))
    await store.upsert_link(db, make_link(remote_id="hs-c2", sheet_id=sheet.id, row_id=rows[1].id))
    await store.upsert_link(
        db,
        make_link(object_type="contact", remote_id="hs-p1", sheet_id=sheet.id, row_id=None),
    )
    # a different provider's links never leak into the counts
    await store.upsert_link(
        db,
        make_link(provider="salesforce", remote_id="sf-1", sheet_id=sheet.id, row_id=rows[0].id),
    )

    assert await store.import_counts(db, PROVIDER) == {"companies": 2, "contacts": 1}
