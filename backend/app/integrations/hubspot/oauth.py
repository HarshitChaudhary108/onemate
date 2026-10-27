"""OAuth helper layer over Authlib's ``AsyncOAuth2Client`` (plan §5.1).

HubSpot expects the client secret in the token POST body, hence
``token_endpoint_auth_method="client_secret_post"``. Tokens are never
logged and never embedded in error messages — failures surface only the
stable ``oauth_exchange_failed`` / ``hubspot_not_connected`` codes.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import SecretStr

from app.config import Settings
from app.errors.integration_error import IntegrationError
from app.integrations.hubspot import store
from app.integrations.hubspot.models import ConnectionRecord
from app.integrations.hubspot.store import TokenCipher

# VERIFIED 2026-10-26 (developers.hubspot.com OAuth docs).
HUBSPOT_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"

_PROVIDER = "hubspot"
# Refresh a token expiring within this window, matching the client's skew.
_EXPIRY_SKEW = timedelta(seconds=60)

_MESSAGES = {
    "hubspot_not_configured": (
        "HubSpot is not configured: set the client id, client secret, and token key on the backend."
    ),
    "hubspot_not_connected": "HubSpot is not connected; connect HubSpot first.",
    "oauth_exchange_failed": "HubSpot authorization failed; try connecting again.",
}


@dataclass(frozen=True)
class TokenBundle:
    """Fresh tokens from the token endpoint.

    ``expires_at`` is an absolute UTC datetime computed as
    ``now + expires_in`` at exchange time.
    """

    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: datetime


def _oauth_client(settings: Settings) -> AsyncOAuth2Client:
    """Build the Authlib client, or raise when the app is unconfigured."""
    if not (
        settings.hubspot_configured
        and settings.hubspot_client_id is not None
        and settings.hubspot_client_secret is not None
    ):
        raise IntegrationError(
            code="hubspot_not_configured", message=_MESSAGES["hubspot_not_configured"]
        )
    return AsyncOAuth2Client(
        settings.hubspot_client_id.get_secret_value(),
        settings.hubspot_client_secret.get_secret_value(),
        redirect_uri=settings.hubspot_redirect_uri,
        scope=settings.hubspot_scopes,
        token_endpoint_auth_method="client_secret_post",
    )


def _to_bundle(token: Any, *, fallback_refresh_token: str | None = None) -> TokenBundle:
    """Normalize an Authlib ``OAuth2Token`` mapping; any missing piece is an
    exchange failure — token values never leak into the message."""
    access = token.get("access_token") if isinstance(token, dict) else None
    refresh = token.get("refresh_token") if isinstance(token, dict) else None
    expires_in = token.get("expires_in") if isinstance(token, dict) else None
    if refresh is None:
        refresh = fallback_refresh_token
    if not access or not refresh or expires_in is None:
        raise IntegrationError(
            code="oauth_exchange_failed", message=_MESSAGES["oauth_exchange_failed"]
        )
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        raise IntegrationError(
            code="oauth_exchange_failed", message=_MESSAGES["oauth_exchange_failed"]
        ) from None
    return TokenBundle(
        access_token=SecretStr(str(access)),
        refresh_token=SecretStr(str(refresh)),
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
    )


def authorize_url(settings: Settings, state: str) -> str:
    """Build the HubSpot consent URL for one minted ``state`` value."""
    client = _oauth_client(settings)
    url, _ = client.create_authorization_url(HUBSPOT_AUTHORIZE_URL, state=state)
    return str(url)


async def exchange_code(settings: Settings, code: str) -> TokenBundle:
    """Exchange an authorization ``code`` for tokens."""
    client = _oauth_client(settings)
    try:
        async with client:
            token = await client.fetch_token(
                HUBSPOT_TOKEN_URL, grant_type="authorization_code", code=code
            )
    except Exception as exc:
        raise IntegrationError(
            code="oauth_exchange_failed", message=_MESSAGES["oauth_exchange_failed"]
        ) from exc
    return _to_bundle(token)


async def refresh(settings: Settings, refresh_token: str) -> TokenBundle:
    """Exchange a ``refresh_token`` for a fresh token bundle.

    HubSpot rotates refresh tokens; if a response ever omits one, the
    existing refresh token is kept.
    """
    client = _oauth_client(settings)
    try:
        async with client:
            token = await client.fetch_token(
                HUBSPOT_TOKEN_URL, grant_type="refresh_token", refresh_token=refresh_token
            )
    except Exception as exc:
        raise IntegrationError(
            code="oauth_exchange_failed", message=_MESSAGES["oauth_exchange_failed"]
        ) from exc
    return _to_bundle(token, fallback_refresh_token=refresh_token)


async def ensure_fresh_token(db: str, fernet: TokenCipher, settings: Settings) -> ConnectionRecord:
    """Load the stored connection and return a record with a live token.

    When ``token_expires_at`` is within the 60 s skew the token is
    refreshed and the rotated tokens are persisted before returning. A
    missing connection — or a rejected refresh (plan §3.2) — surfaces as
    ``hubspot_not_connected``.
    """
    record = await store.get_connection(db, fernet, _PROVIDER)
    if record is None:
        raise IntegrationError(
            code="hubspot_not_connected", message=_MESSAGES["hubspot_not_connected"]
        )
    if record.token_expires_at - _EXPIRY_SKEW > datetime.now(UTC):
        return record
    try:
        bundle = await refresh(settings, record.refresh_token.get_secret_value())
    except IntegrationError as exc:
        raise IntegrationError(
            code="hubspot_not_connected", message=_MESSAGES["hubspot_not_connected"]
        ) from exc
    fresh = record.model_copy(
        update={
            "access_token": bundle.access_token,
            "refresh_token": bundle.refresh_token,
            "token_expires_at": bundle.expires_at,
        }
    )
    await store.save_connection(db, fernet, fresh)
    return fresh
