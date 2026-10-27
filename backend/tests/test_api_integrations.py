"""Integrations API end-to-end tests (I3, plan §5.3).

``httpx.AsyncClient`` over the ASGI app inside its lifespan. HubSpot is
an in-memory ``FakeHubSpotClient`` swapped onto the service for the
data-plane tests; OAuth token endpoints are ``respx``-mocked. No live
calls anywhere.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import respx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.integrations.hubspot import store
from app.integrations.hubspot.mock import FakeHubSpotClient
from app.integrations.hubspot.models import ConnectionRecord
from app.integrations.hubspot.oauth import HUBSPOT_AUTHORIZE_URL, HUBSPOT_TOKEN_URL
from app.main import create_app
from app.schemas.report import Field
from app.sheets import repo
from app.sheets.db import connect as db_connect

CLIENT_ID = "synthetic-client-id"
CLIENT_SECRET = "synthetic-client-secret"
FERNET_KEY = Fernet.generate_key().decode("ascii")
FERNET = Fernet(FERNET_KEY.encode("ascii"))
POST_CONNECT = "http://localhost:3000/integrations"


def _settings(tmp_path: Path) -> Settings:
    # Keys are forced/ pinned so the developer's real .env can never
    # leak in; the Fernet key is a synthetic test key.
    return Settings(
        exa_api_key=None,
        exa_mode="live",
        groq_api_key=None,
        groq_mode="live",
        moltsets_api_key=None,
        moltsets_mode="off",
        sheets_db_path=str(tmp_path / "sheets.db"),
        hubspot_client_id=SecretStr(CLIENT_ID),
        hubspot_client_secret=SecretStr(CLIENT_SECRET),
        hubspot_token_key=SecretStr(FERNET_KEY),
        hubspot_mode="live",
        hubspot_post_connect_url=POST_CONNECT,
    )


@asynccontextmanager
async def _app_client(
    tmp_path: Path, settings: Settings | None = None
) -> AsyncIterator[tuple[FastAPI, AsyncClient, Settings]]:
    settings = settings if settings is not None else _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, settings


async def _use_fake(app: FastAPI, fake: FakeHubSpotClient) -> None:
    """Swap the service's client for a fake, closing the real transport."""
    service = app.state.integration_service
    await service.aclose()
    service.client = fake


async def _connect(db_path: str) -> None:
    """Store a live (encrypted) connection row — ``ensure_fresh_token``
    then resolves without a refresh call."""
    await store.save_connection(
        db_path,
        FERNET,
        ConnectionRecord(
            provider="hubspot",
            portal_id="portal-synth",
            access_token=SecretStr("synthetic-access"),
            refresh_token=SecretStr("synthetic-refresh"),
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=["crm.objects.companies.read"],
            mock=False,
            connected_at=datetime.now(UTC),
            last_sync_at=None,
        ),
    )


async def _mint_state(client: AsyncClient) -> str:
    resp = await client.get("/api/integrations/hubspot/connect")
    assert resp.status_code == 302
    return parse_qs(urlsplit(resp.headers["location"]).query)["state"][0]


# --- OAuth connect / callback ----------------------------------------------------


async def test_connect_redirects_to_hubspot_with_state(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (_, client, settings):
        resp = await client.get("/api/integrations/hubspot/connect")
        assert resp.status_code == 302
        url = urlsplit(resp.headers["location"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == HUBSPOT_AUTHORIZE_URL
        params = parse_qs(url.query)
        assert params["client_id"] == [CLIENT_ID]
        assert params["redirect_uri"] == [settings.hubspot_redirect_uri]
        assert params["response_type"] == ["code"]
        assert len(params["state"][0]) >= 32
        assert CLIENT_SECRET not in resp.headers["location"]


async def test_callback_bad_state_redirects_oauth_state_mismatch(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (_, client, _loaded):
        resp = await client.get("/api/integrations/hubspot/callback?code=x&state=bogus")
        assert resp.status_code == 302
        assert resp.headers["location"] == f"{POST_CONNECT}?hubspot_error=oauth_state_mismatch"


async def test_callback_happy_path_stores_encrypted_tokens(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (_, client, settings):
        state = await _mint_state(client)
        with respx.mock:
            respx.post(HUBSPOT_TOKEN_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "synthetic-access-new",
                        "refresh_token": "synthetic-refresh-new",
                        "expires_in": 1800,
                    },
                )
            )
            respx.get("https://api.hubapi.com/oauth/v1/access-tokens/synthetic-access-new").mock(
                return_value=httpx.Response(
                    200, json={"hub_id": 12345678, "scopes": ["crm.objects.companies.read"]}
                )
            )
            resp = await client.get(f"/api/integrations/hubspot/callback?code=abc&state={state}")
        assert resp.status_code == 302
        assert resp.headers["location"] == f"{POST_CONNECT}?hubspot=connected"

        # Tokens are stored as ciphertext — never the plaintext values.
        conn = await db_connect(settings.sheets_db_path)
        try:
            cursor = await conn.execute(
                "SELECT access_token_enc, refresh_token_enc, portal_id, scopes"
                " FROM integration_connections WHERE provider = 'hubspot'"
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()
        assert row is not None
        assert "synthetic-access-new" not in str(row["access_token_enc"])
        assert "synthetic-refresh-new" not in str(row["refresh_token_enc"])
        assert row["portal_id"] == "12345678"

        stored = await store.get_connection(settings.sheets_db_path, FERNET, "hubspot")
        assert stored is not None
        assert stored.access_token.get_secret_value() == "synthetic-access-new"
        assert stored.refresh_token.get_secret_value() == "synthetic-refresh-new"


async def test_callback_exchange_failure_redirects(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (_, client, settings):
        state = await _mint_state(client)
        with respx.mock:
            respx.post(HUBSPOT_TOKEN_URL).mock(
                return_value=httpx.Response(400, json={"error": "invalid_grant"})
            )
            resp = await client.get(f"/api/integrations/hubspot/callback?code=bad&state={state}")
        assert resp.status_code == 302
        assert resp.headers["location"] == f"{POST_CONNECT}?hubspot_error=oauth_exchange_failed"
        assert await store.get_connection(settings.sheets_db_path, FERNET, "hubspot") is None


# --- status / connection -----------------------------------------------------------


async def test_status_disconnect_reconnect_semantics(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (_, client, settings):
        resp = await client.get("/api/integrations/hubspot/status")
        body = resp.json()
        assert resp.status_code == 200
        assert body["connected"] is False and body["mock"] is False
        assert body["companies_imported"] == 0

        await _connect(settings.sheets_db_path)
        resp = await client.get("/api/integrations/hubspot/status")
        body = resp.json()
        assert body["connected"] is True
        assert body["portal_id"] == "portal-synth"
        assert body["scopes"] == ["crm.objects.companies.read"]
        assert "synthetic-access" not in resp.text  # tokens never leave this layer
        assert "synthetic-refresh" not in resp.text

        deleted = await client.delete("/api/integrations/hubspot/connection")
        assert deleted.status_code == 204
        resp = await client.get("/api/integrations/hubspot/status")
        assert resp.json()["connected"] is False


# --- objects / links / data-plane ----------------------------------------------------


async def test_objects_preview_requires_connection(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (app, client, settings):
        resp = await client.get("/api/integrations/hubspot/objects?object_type=companies")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "hubspot_not_connected"

        await _connect(settings.sheets_db_path)
        await _use_fake(app, FakeHubSpotClient())
        resp = await client.get("/api/integrations/hubspot/objects?object_type=companies")
        assert resp.status_code == 200
        assert {obj["remote_id"] for obj in resp.json()} == {"1001", "1002"}
        assert all(obj["object_type"] == "company" for obj in resp.json())

        resp = await client.get("/api/integrations/hubspot/objects?object_type=bogus")
        assert resp.status_code == 422


async def test_import_links_push_refresh_happy_path(tmp_path: Path) -> None:
    async with _app_client(tmp_path) as (app, client, settings):
        fake = FakeHubSpotClient()
        await _use_fake(app, fake)
        await _connect(settings.sheets_db_path)
        db_path = settings.sheets_db_path

        sheet_id = (await client.post("/api/sheets", json={"name": "targets"})).json()["id"]

        resp = await client.post("/api/integrations/hubspot/import", json={"sheet_id": sheet_id})
        assert resp.status_code == 200
        assert resp.json() == {
            "created": 2,
            "skipped": 0,
            "linked_companies": 2,
            "linked_contacts": 1,
        }

        resp = await client.get(f"/api/integrations/hubspot/links?sheet_id={sheet_id}")
        assert resp.status_code == 200
        links = resp.json()["links"]
        assert len(links) == 3
        companies = {link["remote_id"]: link for link in links if link["object_type"] == "company"}
        contacts = [link for link in links if link["object_type"] == "contact"]
        assert set(companies) == {"1001", "1002"}
        assert contacts[0]["row_id"] is None
        row_id = companies["1001"]["row_id"]

        # A done cell is required before anything can be pushed.
        created = await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={
                "columns": [
                    {
                        "name": "Tech stack",
                        "prompt_template": "stack of {{Company}}",
                        "output_type": "text",
                        "enum_options": [],
                        "source_scope": "web",
                        "depth": "standard",
                    }
                ]
            },
        )
        column_id = created.json()["columns"][0]["id"]
        await repo.finish_cell(
            db_path,
            row_id,
            column_id,
            status="done",
            result=Field(value="Python", status="provider-reported"),
            column_version=1,
        )

        resp = await client.post(
            "/api/integrations/hubspot/push",
            json={
                "sheet_id": sheet_id,
                "approvals": [
                    {
                        "row_id": row_id,
                        "column_id": column_id,
                        "property": "outmate_tech_stack",
                    }
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == [
            {
                "row_id": row_id,
                "column_id": column_id,
                "property": "outmate_tech_stack",
                "status": "pushed",
                "detail": None,
            }
        ]
        assert fake.patch_calls == [
            {
                "object_type": "company",
                "remote_id": "1001",
                "properties": {"outmate_tech_stack": "Python"},
            }
        ]

        resp = await client.post("/api/integrations/hubspot/refresh", json={"sheet_id": sheet_id})
        assert resp.status_code == 200
        assert resp.json() == {"changes": []}

        await fake.patch_object("", "company", "1001", {"industry": "Automation"})
        resp = await client.post("/api/integrations/hubspot/refresh", json={"sheet_id": sheet_id})
        assert resp.json()["changes"] == [
            {
                "row_id": row_id,
                "remote_id": "1001",
                "property": "industry",
                "remote_value": "Automation",
                "previous": "Robotics",
            }
        ]


# --- not configured ------------------------------------------------------------------


async def test_unconfigured_returns_503_and_status_still_loads(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"hubspot_client_id": None})
    async with _app_client(tmp_path, settings) as (app, client, _loaded):
        # client is None (live + no client id) -> data routes are a
        # 503 hubspot_not_configured, never a silent mock.
        resp = await client.get("/api/integrations/hubspot/objects?object_type=companies")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "hubspot_not_configured"

        resp = await client.post("/api/integrations/hubspot/import", json={"sheet_id": "any"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "hubspot_not_configured"

        # /status still loads — disconnected is not an error.
        resp = await client.get("/api/integrations/hubspot/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False

        # Service absent entirely -> same 503 on data routes, 200 on status.
        app.state.integration_service = None
        resp = await client.post("/api/integrations/hubspot/refresh", json={"sheet_id": "any"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "hubspot_not_configured"
        resp = await client.get("/api/integrations/hubspot/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False


async def test_mock_mode_discloses_and_works_without_oauth(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(
        update={
            "hubspot_mode": "mock",
            "hubspot_client_id": None,
            "hubspot_client_secret": None,
            "hubspot_token_key": None,
        }
    )
    async with _app_client(tmp_path, settings) as (_, client, _loaded):
        resp = await client.get("/api/integrations/hubspot/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True and body["mock"] is True

        sheet_id = (await client.post("/api/sheets", json={"name": "S"})).json()["id"]
        resp = await client.post("/api/integrations/hubspot/import", json={"sheet_id": sheet_id})
        assert resp.status_code == 200
        assert resp.json()["created"] == 2
