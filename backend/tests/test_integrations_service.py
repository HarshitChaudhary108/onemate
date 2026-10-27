"""Integration service business rules (I3, plan §5.1 ``service.py``).

The client is an in-memory ``FakeHubSpotClient`` — no HTTP anywhere;
rows, links and push-log entries land in a real tmp SQLite DB via the
conftest ``db`` fixture. Settings are explicit so the developer's
``.env`` can never leak in.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import Settings
from app.errors.integration_error import IntegrationError
from app.errors.sheet_error import SheetError
from app.integrations.hubspot import store
from app.integrations.hubspot.client import HttpHubSpotClient
from app.integrations.hubspot.mapping import (
    ensure_push_property,
    serialize_value,
    slugify_column,
    suggest_property,
)
from app.integrations.hubspot.mock import FakeHubSpotClient
from app.integrations.hubspot.models import ConnectionRecord
from app.integrations.hubspot.service import IntegrationService, build_client
from app.schemas.integration import PushApproval
from app.schemas.report import Field
from app.sheets import repo
from app.sheets.models import RowInput
from app.sheets.service import SheetService

PROVIDER = "hubspot"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "hubspot_mode": "mock",
        "hubspot_client_id": None,
        "hubspot_client_secret": None,
        "hubspot_token_key": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake() -> FakeHubSpotClient:
    return FakeHubSpotClient()


@pytest.fixture
def sheet_service(db: str) -> SheetService:
    return SheetService(db)


@pytest.fixture
def service(db: str, sheet_service: SheetService, fake: FakeHubSpotClient) -> IntegrationService:
    return IntegrationService(db, _settings(), None, sheet_service, client=fake)


async def _imported_sheet(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> tuple[str, dict[str, str]]:
    """Create a sheet, import the fake's defaults; returns
    ``(sheet_id, {input_domain: row_id})``."""
    sheet = await sheet_service.create_sheet("S")
    await service.import_records(sheet.id, None, None)
    rows = await repo.list_rows(db, sheet.id)
    return sheet.id, {row.input_domain or "": row.id for row in rows}


# --- mapping helpers ------------------------------------------------------------


def test_slugify_and_suggest() -> None:
    assert slugify_column("Tech Stack!") == "tech_stack"
    assert slugify_column("  Multiple---Separators  ") == "multiple_separators"
    assert slugify_column("growth signals") == "growth_signals"
    assert len(slugify_column("x" * 100)) == 40
    assert suggest_property("Growth Signals") == "outmate_growth_signals"


def test_serialize_value() -> None:
    assert serialize_value(True) == "true"
    assert serialize_value(False) == "false"
    assert serialize_value(3.5) == "3.5"
    assert serialize_value(7) == "7"
    assert serialize_value("abc") == "abc"


def test_ensure_push_property_guard() -> None:
    assert ensure_push_property("outmate_tech_stack") == "outmate_tech_stack"
    with pytest.raises(IntegrationError) as excinfo:
        ensure_push_property("name")
    assert excinfo.value.code == "import_conflict"


def test_build_client_modes() -> None:
    assert build_client(_settings(hubspot_mode="off")) is None
    assert isinstance(build_client(_settings(hubspot_mode="mock")), FakeHubSpotClient)
    assert build_client(_settings(hubspot_mode="live")) is None  # no creds
    configured = _settings(
        hubspot_mode="live",
        hubspot_client_id=SecretStr("id"),
        hubspot_client_secret=SecretStr("secret"),
        hubspot_token_key=SecretStr("key"),
    )
    assert isinstance(build_client(configured), HttpHubSpotClient)


async def test_unconfigured_service_rejects_data_calls(
    db: str, sheet_service: SheetService
) -> None:
    service = IntegrationService(db, _settings(hubspot_mode="live"), None, sheet_service)
    assert service.client is None
    sheet = await sheet_service.create_sheet("S")
    with pytest.raises(IntegrationError) as excinfo:
        await service.import_records(sheet.id, None, None)
    assert excinfo.value.code == "hubspot_not_configured"


# --- import (plan §3.2) ------------------------------------------------------------


async def test_import_creates_rows_and_links(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> None:
    sheet = await sheet_service.create_sheet("S")
    result = await service.import_records(sheet.id, None, None)
    assert (result.created, result.skipped) == (2, 0)
    assert (result.linked_companies, result.linked_contacts) == (2, 1)

    rows = await repo.list_rows(db, sheet.id)
    assert {row.input_name for row in rows} == {
        "Example Robotics Ltd",
        "Synthetic Components Ltd",
    }
    assert {row.input_domain for row in rows} == {
        "example-robotics.test",
        "synthetic-components.test",
    }

    links = await store.links_for_sheet(db, PROVIDER, sheet.id)
    companies = {link.remote_id: link for link in links if link.object_type == "company"}
    contacts = [link for link in links if link.object_type == "contact"]
    assert set(companies) == {"1001", "1002"}
    row_ids = {row.id for row in rows}
    assert companies["1001"].row_id in row_ids
    assert companies["1001"].properties["name"] == "Example Robotics Ltd"
    assert len(contacts) == 1
    assert contacts[0].row_id is None  # contacts are snapshot-only
    assert contacts[0].properties["email"] == "ada@example-robotics.test"


async def test_import_dedupes_remote_id_on_reimport(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> None:
    sheet = await sheet_service.create_sheet("S")
    first = await service.import_records(sheet.id, None, None)
    assert (first.created, first.skipped) == (2, 0)

    second = await service.import_records(sheet.id, None, None)
    assert (second.created, second.skipped) == (0, 2)
    assert second.linked_companies == 2  # still linked to this sheet
    assert len(await repo.list_rows(db, sheet.id)) == 2
    assert len(await store.links_for_sheet(db, PROVIDER, sheet.id)) == 3


async def test_import_links_to_existing_same_domain_row(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> None:
    """Domain dedupe: a pasted row for the same domain stays single —
    the link attaches to the existing row (plan §6)."""
    sheet = await sheet_service.create_sheet("S")
    pasted = await sheet_service.import_rows(
        sheet.id, [RowInput(input_name="Pasted row", input_domain="www.example-robotics.test")]
    )
    existing = pasted[0]

    result = await service.import_records(sheet.id, None, None)
    assert (result.created, result.skipped) == (1, 1)
    assert result.linked_companies == 2

    link = await store.get_link(db, PROVIDER, "company", "1001")
    assert link is not None
    assert link.row_id == existing.id  # link on the pre-existing row
    assert len(await repo.list_rows(db, sheet.id)) == 2  # no twin created


async def test_import_name_only_company(db: str, sheet_service: SheetService) -> None:
    fake = FakeHubSpotClient(companies={"3001": {"name": "Nameless Domain Co"}}, contacts={})
    service = IntegrationService(db, _settings(), None, sheet_service, client=fake)
    sheet = await sheet_service.create_sheet("S")

    result = await service.import_records(sheet.id, None, None)
    assert (result.created, result.skipped) == (1, 0)
    rows = await repo.list_rows(db, sheet.id)
    assert rows[0].input_name == "Nameless Domain Co"
    assert rows[0].input_domain is None
    link = await store.get_link(db, PROVIDER, "company", "3001")
    assert link is not None and link.row_id == rows[0].id


async def test_import_skips_company_without_identity(db: str, sheet_service: SheetService) -> None:
    fake = FakeHubSpotClient(companies={"4001": {"industry": "Robotics"}}, contacts={})
    service = IntegrationService(db, _settings(), None, sheet_service, client=fake)
    sheet = await sheet_service.create_sheet("S")

    result = await service.import_records(sheet.id, None, None)
    assert (result.created, result.skipped, result.linked_companies) == (0, 1, 0)
    assert await repo.list_rows(db, sheet.id) == []
    assert await store.get_link(db, PROVIDER, "company", "4001") is None


async def test_import_filters_requested_ids(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> None:
    sheet = await sheet_service.create_sheet("S")
    result = await service.import_records(sheet.id, ["1002"], [])
    assert (result.created, result.skipped) == (1, 0)
    assert (result.linked_companies, result.linked_contacts) == (1, 0)
    assert await store.get_link(db, PROVIDER, "company", "1002") is not None
    assert await store.get_link(db, PROVIDER, "company", "1001") is None


async def test_import_missing_sheet_is_not_found(service: IntegrationService) -> None:
    with pytest.raises(SheetError) as excinfo:
        await service.import_records("no-such-sheet", None, None)
    assert excinfo.value.code == "not_found"


# --- push (plan §3.4) ------------------------------------------------------------


async def test_push_patches_only_allowed_properties(
    db: str, service: IntegrationService, sheet_service: SheetService, fake: FakeHubSpotClient
) -> None:
    sheet_id, rows = await _imported_sheet(db, service, sheet_service)
    row_id = rows["example-robotics.test"]
    column = await sheet_service.add_column(
        sheet_id,
        name="Tech stack",
        prompt_template="stack of {{Company}}",
        output_type="text",
        source_scope="web",
    )
    await repo.finish_cell(
        db,
        row_id,
        column.id,
        status="done",
        result=Field(value="Python, FastAPI", status="provider-reported"),
        column_version=column.version,
    )

    results = await service.push(
        sheet_id,
        [
            PushApproval(row_id=row_id, column_id=column.id, property="outmate_tech_stack"),
        ],
    )
    assert [(r.status, r.detail) for r in results] == [("pushed", None)]
    assert fake.patch_calls == [
        {
            "object_type": "company",
            "remote_id": "1001",
            "properties": {"outmate_tech_stack": "Python, FastAPI"},
        }
    ]
    logs = await store.push_log_for_sheet(db, PROVIDER, sheet_id)
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].value == "Python, FastAPI"
    assert logs[0].property == "outmate_tech_stack"


async def test_push_skips_and_errors_never_patch(
    db: str, service: IntegrationService, sheet_service: SheetService, fake: FakeHubSpotClient
) -> None:
    sheet_id, rows = await _imported_sheet(db, service, sheet_service)
    linked_done = rows["example-robotics.test"]
    linked_idle = rows["synthetic-components.test"]
    unlinked = (
        await sheet_service.import_rows(
            sheet_id, [RowInput(input_name="Unlinked", input_domain="unlinked.test")]
        )
    )[0]
    column = await sheet_service.add_column(
        sheet_id,
        name="Tech stack",
        prompt_template="stack of {{Company}}",
        output_type="text",
        source_scope="web",
    )
    await repo.finish_cell(
        db,
        linked_done,
        column.id,
        status="done",
        result=Field(value="Python", status="provider-reported"),
        column_version=column.version,
    )

    results = await service.push(
        sheet_id,
        [
            # happy path — the only PATCH that may happen
            PushApproval(row_id=linked_done, column_id=column.id, property="outmate_tech_stack"),
            # done cell, but a sales-owned property — overwrite guard
            PushApproval(row_id=linked_done, column_id=column.id, property="name"),
            # linked row but the cell is still idle — no_value
            PushApproval(row_id=linked_idle, column_id=column.id, property="outmate_tech_stack"),
            # row exists but has no company link
            PushApproval(row_id=unlinked.id, column_id=column.id, property="outmate_tech_stack"),
            # no such row in this sheet
            PushApproval(row_id="ghost-row", column_id=column.id, property="outmate_tech_stack"),
        ],
    )

    assert [(r.status, r.detail) for r in results] == [
        ("pushed", None),
        ("error", "property_not_allowed"),
        ("skipped", "no_value"),
        ("skipped", "not_linked"),
        ("error", "row_not_found"),
    ]
    # Exactly one PATCH went out — the allowed property only.
    assert fake.patch_calls == [
        {
            "object_type": "company",
            "remote_id": "1001",
            "properties": {"outmate_tech_stack": "Python"},
        }
    ]
    logs = await store.push_log_for_sheet(db, PROVIDER, sheet_id)
    assert len(logs) == 1 and logs[0].status == "ok"


async def test_push_done_cell_without_value_is_never_pushed(
    db: str, service: IntegrationService, sheet_service: SheetService, fake: FakeHubSpotClient
) -> None:
    """A ``done`` cell holding ``unavailable`` (value=None) is skipped —
    unavailable cells are NEVER pushed (plan §6)."""
    sheet_id, rows = await _imported_sheet(db, service, sheet_service)
    row_id = rows["example-robotics.test"]
    column = await sheet_service.add_column(
        sheet_id,
        name="Tech stack",
        prompt_template="stack of {{Company}}",
        output_type="text",
        source_scope="web",
    )
    await repo.finish_cell(
        db,
        row_id,
        column.id,
        status="done",
        result=Field(value=None, status="unavailable"),
        column_version=column.version,
    )

    results = await service.push(
        sheet_id,
        [PushApproval(row_id=row_id, column_id=column.id, property="outmate_tech_stack")],
    )
    assert [(r.status, r.detail) for r in results] == [("skipped", "no_value")]
    assert fake.patch_calls == []


# --- refresh (plan §3.5) ------------------------------------------------------------


async def test_refresh_reports_diffs_and_updates_snapshot(
    db: str, service: IntegrationService, sheet_service: SheetService, fake: FakeHubSpotClient
) -> None:
    sheet_id, rows = await _imported_sheet(db, service, sheet_service)
    row_id = rows["example-robotics.test"]
    column = await sheet_service.add_column(
        sheet_id,
        name="Tech stack",
        prompt_template="stack of {{Company}}",
        output_type="text",
        source_scope="web",
    )
    await repo.finish_cell(
        db,
        row_id,
        column.id,
        status="done",
        result=Field(value="Python", status="provider-reported"),
        column_version=column.version,
    )

    # A rep edits the company remotely.
    await fake.patch_object("", "company", "1001", {"name": "Renamed Robotics Ltd"})

    changes = await service.refresh(sheet_id)
    assert len(changes) == 1
    change = changes[0]
    assert change.row_id == row_id
    assert change.remote_id == "1001"
    assert change.property == "name"
    assert change.remote_value == "Renamed Robotics Ltd"
    assert change.previous == "Example Robotics Ltd"

    # The stored snapshot moved forward; the cell did not.
    link = await store.get_link(db, PROVIDER, "company", "1001")
    assert link is not None and link.properties["name"] == "Renamed Robotics Ltd"
    cell = await repo.get_cell(db, row_id, column.id)
    assert cell is not None
    assert cell.status == "done" and cell.result is not None and cell.result.value == "Python"

    # A second refresh sees nothing new.
    assert await service.refresh(sheet_id) == []


async def test_refresh_with_no_links_is_empty(
    db: str, service: IntegrationService, sheet_service: SheetService
) -> None:
    sheet = await sheet_service.create_sheet("Empty")
    assert await service.refresh(sheet.id) == []


# --- status / last_sync -------------------------------------------------------------


async def test_status_connected_counts_and_last_sync(
    db: str, sheet_service: SheetService, fake: FakeHubSpotClient
) -> None:
    fernet = Fernet(Fernet.generate_key())
    record = ConnectionRecord(
        provider=PROVIDER,
        portal_id="portal-synth",
        access_token=SecretStr("synthetic-access"),
        refresh_token=SecretStr("synthetic-refresh"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["crm.objects.companies.read"],
        mock=True,
        connected_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_sync_at=None,
    )
    await store.save_connection(db, fernet, record)
    service = IntegrationService(db, _settings(), fernet, sheet_service, client=fake)

    sheet = await sheet_service.create_sheet("S")
    await service.import_records(sheet.id, None, None)

    status = await service.status()
    assert status.connected is True
    assert status.portal_id == "portal-synth"
    assert status.scopes == ["crm.objects.companies.read"]
    assert status.companies_imported == 2
    assert status.contacts_imported == 1
    assert status.mock is True
    assert status.last_sync_at is not None  # stamped by the import

    await service.delete_connection()
    status = await service.status()
    assert status.connected is True  # mock mode is an implicit connection
    assert status.mock is True
    assert status.companies_imported == 2  # links survive reconnect
