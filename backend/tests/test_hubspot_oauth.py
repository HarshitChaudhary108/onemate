"""OAuth helper layer tests (I2).

The token endpoint is respx-mocked; the authorization endpoint produces a
local URL only. Fernet uses a fixed test-only key. No network is touched.
"""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import Settings
from app.errors.integration_error import IntegrationError
from app.integrations.hubspot import store
from app.integrations.hubspot.models import ConnectionRecord
from app.integrations.hubspot.oauth import (
    HUBSPOT_AUTHORIZE_URL,
    HUBSPOT_TOKEN_URL,
    TokenBundle,
    authorize_url,
    ensure_fresh_token,
    exchange_code,
    refresh,
)

# Fixed test-only Fernet key — never a real secret.
TEST_FERNET = Fernet(base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef"))

CLIENT_ID = "synthetic-client-id"
CLIENT_SECRET = "synthetic-client-secret"
REDIRECT_URI = "http://localhost:8000/api/integrations/hubspot/callback"
SCOPES = "crm.objects.companies.read crm.objects.contacts.read"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        hubspot_client_id=SecretStr(CLIENT_ID),
        hubspot_client_secret=SecretStr(CLIENT_SECRET),
        hubspot_token_key=SecretStr("synthetic-key"),
        hubspot_redirect_uri=REDIRECT_URI,
        hubspot_scopes=SCOPES,
    )


def make_connection(**overrides: Any) -> ConnectionRecord:
    base: dict[str, Any] = {
        "provider": "hubspot",
        "portal_id": "portal-synth",
        "access_token": SecretStr("synthetic-access-old"),
        "refresh_token": SecretStr("synthetic-refresh-old"),
        "token_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "scopes": ["crm.objects.companies.read"],
        "mock": False,
        "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_sync_at": None,
    }
    base.update(overrides)
    return ConnectionRecord(**base)


def token_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "access_token": "synthetic-access-new",
        "refresh_token": "synthetic-refresh-new",
        "expires_in": 1800,
    }
    base.update(overrides)
    return base


def form_body(route: respx.Route, call: int = 0) -> dict[str, list[str]]:
    return parse_qs(route.calls[call].request.content.decode("utf-8"))


# --- authorize_url -------------------------------------------------------------


def test_authorize_url_exact_params(settings: Settings) -> None:
    url = authorize_url(settings, "state-abc-123")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == HUBSPOT_AUTHORIZE_URL
    params = parse_qs(parts.query)
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["scope"] == [SCOPES]
    assert params["state"] == ["state-abc-123"]
    assert params["response_type"] == ["code"]
    # client secret must never appear in the consent URL
    assert CLIENT_SECRET not in url


def test_authorize_url_unconfigured_raises() -> None:
    # Explicit blanks — Settings() alone would read the developer's .env.
    unconfigured = Settings(
        hubspot_client_id=SecretStr(""),
        hubspot_client_secret=SecretStr(""),
        hubspot_token_key=SecretStr(""),
    )
    with pytest.raises(IntegrationError) as excinfo:
        authorize_url(unconfigured, "state-abc")
    assert excinfo.value.code == "hubspot_not_configured"


# --- exchange_code -------------------------------------------------------------


async def test_exchange_code_posts_grant_and_secret(settings: Settings) -> None:
    with respx.mock:
        route = respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=token_payload())
        )
        bundle = await exchange_code(settings, "synthetic-code")

    assert isinstance(bundle, TokenBundle)
    assert bundle.access_token.get_secret_value() == "synthetic-access-new"
    assert bundle.refresh_token.get_secret_value() == "synthetic-refresh-new"
    assert (
        timedelta(seconds=1700) < (bundle.expires_at - datetime.now(UTC)) <= timedelta(seconds=1800)
    )

    body = form_body(route)
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["synthetic-code"]
    assert body["client_id"] == [CLIENT_ID]
    assert body["client_secret"] == [CLIENT_SECRET]  # client_secret_post
    assert body["redirect_uri"] == [REDIRECT_URI]


async def test_exchange_code_failure_maps_code(settings: Settings) -> None:
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(IntegrationError) as excinfo:
            await exchange_code(settings, "bad-code")
    err = excinfo.value
    assert err.code == "oauth_exchange_failed"
    assert "bad-code" not in err.message
    assert CLIENT_SECRET not in err.message


async def test_exchange_code_missing_fields_maps_code(settings: Settings) -> None:
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "only-token"})
        )
        with pytest.raises(IntegrationError) as excinfo:
            await exchange_code(settings, "synthetic-code")
    assert excinfo.value.code == "oauth_exchange_failed"
    assert "only-token" not in excinfo.value.message


# --- refresh -----------------------------------------------------------------


async def test_refresh_posts_refresh_grant(settings: Settings) -> None:
    with respx.mock:
        route = respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json=token_payload(access_token="rotated-access", refresh_token="rotated-refresh"),
            )
        )
        bundle = await refresh(settings, "synthetic-refresh-old")

    assert bundle.access_token.get_secret_value() == "rotated-access"
    assert bundle.refresh_token.get_secret_value() == "rotated-refresh"
    body = form_body(route)
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["synthetic-refresh-old"]
    assert body["client_secret"] == [CLIENT_SECRET]


async def test_refresh_keeps_old_refresh_token_when_omitted(settings: Settings) -> None:
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "rotated-access", "expires_in": 1800}
            )
        )
        bundle = await refresh(settings, "synthetic-refresh-old")
    assert bundle.refresh_token.get_secret_value() == "synthetic-refresh-old"


# --- ensure_fresh_token --------------------------------------------------------


async def test_ensure_fresh_token_returns_record_when_valid(db: str, settings: Settings) -> None:
    record = make_connection(token_expires_at=datetime.now(UTC) + timedelta(hours=1))
    await store.save_connection(db, TEST_FERNET, record)

    with respx.mock:
        route = respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=token_payload())
        )
        fresh = await ensure_fresh_token(db, TEST_FERNET, settings)

    assert fresh.access_token.get_secret_value() == "synthetic-access-old"
    assert route.call_count == 0  # no refresh needed


async def test_ensure_fresh_token_refreshes_within_skew_and_persists(
    db: str, settings: Settings
) -> None:
    record = make_connection(token_expires_at=datetime.now(UTC) + timedelta(seconds=30))
    await store.save_connection(db, TEST_FERNET, record)

    with respx.mock:
        route = respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=token_payload(refresh_token="rotated-refresh"))
        )
        fresh = await ensure_fresh_token(db, TEST_FERNET, settings)

    assert fresh.access_token.get_secret_value() == "synthetic-access-new"
    assert fresh.refresh_token.get_secret_value() == "rotated-refresh"
    assert fresh.token_expires_at > datetime.now(UTC) + timedelta(seconds=1700)

    # rotated tokens were persisted — reload and decrypt through the store
    stored = await store.get_connection(db, TEST_FERNET, "hubspot")
    assert stored is not None
    assert stored.access_token.get_secret_value() == "synthetic-access-new"
    assert stored.refresh_token.get_secret_value() == "rotated-refresh"
    assert stored.token_expires_at > datetime.now(UTC) + timedelta(seconds=1700)

    body = form_body(route)
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["synthetic-refresh-old"]


async def test_ensure_fresh_token_expired_boundary_refreshes(db: str, settings: Settings) -> None:
    record = make_connection(token_expires_at=datetime.now(UTC) - timedelta(seconds=5))
    await store.save_connection(db, TEST_FERNET, record)
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(return_value=httpx.Response(200, json=token_payload()))
        fresh = await ensure_fresh_token(db, TEST_FERNET, settings)
    assert fresh.access_token.get_secret_value() == "synthetic-access-new"


async def test_ensure_fresh_token_missing_connection(db: str, settings: Settings) -> None:
    with pytest.raises(IntegrationError) as excinfo:
        await ensure_fresh_token(db, TEST_FERNET, settings)
    assert excinfo.value.code == "hubspot_not_connected"


async def test_ensure_fresh_token_refresh_failure_is_not_connected(
    db: str, settings: Settings
) -> None:
    record = make_connection(token_expires_at=datetime.now(UTC) - timedelta(seconds=5))
    await store.save_connection(db, TEST_FERNET, record)
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(IntegrationError) as excinfo:
            await ensure_fresh_token(db, TEST_FERNET, settings)
    err = excinfo.value
    assert err.code == "hubspot_not_connected"
    assert "synthetic-refresh-old" not in err.message


async def test_oauth_module_has_no_token_in_error_paths(settings: Settings) -> None:
    with respx.mock:
        respx.post(HUBSPOT_TOKEN_URL).mock(side_effect=httpx.ConnectError("synthetic"))
        with pytest.raises(IntegrationError) as excinfo:
            await refresh(settings, "refresh-secret-value")
    assert "refresh-secret-value" not in str(excinfo.value)
