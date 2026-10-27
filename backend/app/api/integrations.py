"""Integrations HTTP API (plan §5.3) — every route under ``/api``.

Thin layer over :class:`app.integrations.hubspot.service.IntegrationService`
(business rules), resolved via ``app.state`` exactly like ``SheetDeps``.

OAuth flow: ``connect`` mints a single-use state and 302s to the HubSpot
consent URL; ``callback`` consumes the state, exchanges the code, reads
the portal id from token metadata, encrypts + stores the connection, and
redirects to the frontend — errors travel as ``?hubspot_error=<code>``
query params, never in a JSON body a browser redirect would swallow.

Domain errors arrive as ``IntegrationError`` and map onto the shared
``ErrorResponse`` shape in ``main.py``; ``SheetError`` from the sheets
layer passes through unchanged. This API has NO authentication — run it
locally or behind a trusted boundary only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Settings
from app.engine.runner import SheetRunner
from app.errors.integration_error import IntegrationError
from app.integrations.hubspot import oauth
from app.integrations.hubspot.models import ConnectionRecord
from app.integrations.hubspot.service import IntegrationService
from app.schemas.integration import (
    HubSpotStatus,
    ImportRequest,
    ImportResponse,
    LinksResponse,
    PushRequest,
    PushResponse,
    RefreshRequest,
    RefreshResponse,
    RemoteObjectPreview,
)
from app.sheets.service import SheetService

_NOT_CONFIGURED_MSG = (
    "HubSpot is not configured: set the client id, client secret, and token key on the backend."
)

_OBJECT_TYPES: dict[str, Literal["company", "contact"]] = {
    "companies": "company",
    "contacts": "contact",
}


@dataclass
class IntegrationDeps:
    """The integrations wiring created in the app lifespan, reached via
    ``app.state`` — tests substitute pieces in place.

    ``service`` is ``None`` only when a test overrides it — the real
    wiring always builds one (its ``client`` is what may be missing).
    """

    service: IntegrationService | None
    settings: Settings
    sheet_service: SheetService
    runner: SheetRunner | None


def get_integration_deps(request: Request) -> IntegrationDeps:
    app = request.app
    return IntegrationDeps(
        service=app.state.integration_service,
        settings=app.state.settings,
        sheet_service=app.state.sheet_service,
        runner=app.state.runner,
    )


Deps = Annotated[IntegrationDeps, Depends(get_integration_deps)]

router = APIRouter()


def _require_service(deps: IntegrationDeps) -> IntegrationService:
    """All data-plane routes need a configured client — missing one is
    a 503 ``hubspot_not_configured``, never a silent mock."""
    service = deps.service
    if service is None:
        raise IntegrationError(code="hubspot_not_configured", message=_NOT_CONFIGURED_MSG)
    service.require_client()
    return service


def _post_connect(deps: IntegrationDeps, suffix: str) -> RedirectResponse:
    return RedirectResponse(f"{deps.settings.hubspot_post_connect_url}{suffix}", status_code=302)


def _connection_record(
    settings: Settings, bundle: oauth.TokenBundle, info: dict[str, Any]
) -> ConnectionRecord:
    """The connection row to persist: tokens from the exchange, portal
    id + granted scopes from token metadata (settings scopes as
    fallback), ``mock`` stamped from the mode."""
    hub_id = info.get("hub_id")
    raw_scopes = info.get("scopes")
    scopes = (
        [str(scope) for scope in raw_scopes]
        if isinstance(raw_scopes, list) and raw_scopes
        else settings.hubspot_scopes.split()
    )
    return ConnectionRecord(
        provider="hubspot",
        portal_id=str(hub_id) if hub_id is not None else None,
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        token_expires_at=bundle.expires_at,
        scopes=scopes,
        mock=settings.hubspot_mode == "mock",
        connected_at=datetime.now(UTC),
        last_sync_at=None,
    )


# --- OAuth connect / callback (plan §3.1) -------------------------------------


@router.get("/api/integrations/hubspot/connect")
async def connect(deps: Deps) -> RedirectResponse:
    service = _require_service(deps)
    state = await service.create_state()
    return RedirectResponse(oauth.authorize_url(deps.settings, state), status_code=302)


@router.get("/api/integrations/hubspot/callback")
async def callback(code: str, state: str, deps: Deps) -> RedirectResponse:
    service = _require_service(deps)
    if not await service.consume_state(state):
        return _post_connect(deps, "?hubspot_error=oauth_state_mismatch")
    try:
        bundle = await oauth.exchange_code(deps.settings, code)
        client = service.require_client()
        info = await client.token_info(bundle.access_token.get_secret_value())
        await service.save_connection(_connection_record(deps.settings, bundle, info))
    except IntegrationError as exc:
        return _post_connect(deps, f"?hubspot_error={exc.code}")
    return _post_connect(deps, "?hubspot=connected")


# --- status / connection --------------------------------------------------------


@router.get("/api/integrations/hubspot/status")
async def status(deps: Deps) -> HubSpotStatus:
    """Always answers 200 — ``connected=False`` when there is no
    connection row or the service is absent; NOT an error."""
    service = deps.service
    if service is None:
        return HubSpotStatus(
            connected=False,
            portal_id=None,
            scopes=[],
            connected_at=None,
            last_sync_at=None,
            companies_imported=0,
            contacts_imported=0,
            mock=deps.settings.hubspot_mode == "mock",
        )
    return await service.status()


@router.delete("/api/integrations/hubspot/connection", status_code=204)
async def disconnect(deps: Deps) -> Response:
    service = _require_service(deps)
    await service.delete_connection()
    return Response(status_code=204)


# --- remote objects / links ------------------------------------------------------


@router.get("/api/integrations/hubspot/objects")
async def list_objects(
    object_type: Literal["companies", "contacts"], deps: Deps
) -> list[RemoteObjectPreview]:
    service = _require_service(deps)
    return await service.preview_objects(_OBJECT_TYPES[object_type])


@router.get("/api/integrations/hubspot/links")
async def list_links(sheet_id: str, deps: Deps) -> LinksResponse:
    service = _require_service(deps)
    return LinksResponse(links=await service.list_links(sheet_id))


# --- import / push / refresh ------------------------------------------------------


@router.post("/api/integrations/hubspot/import")
async def import_records(body: ImportRequest, deps: Deps) -> ImportResponse:
    service = _require_service(deps)
    response = await service.import_records(body.sheet_id, body.company_ids, body.contact_ids)
    if response.created and deps.runner is not None:
        # Verify the new rows' identities in the background — same
        # post-import pass as paste-imported rows.
        await deps.runner.resolve_pending_identities(body.sheet_id)
    return response


@router.post("/api/integrations/hubspot/push")
async def push(body: PushRequest, deps: Deps) -> PushResponse:
    service = _require_service(deps)
    return PushResponse(results=await service.push(body.sheet_id, body.approvals))


@router.post("/api/integrations/hubspot/refresh")
async def refresh(body: RefreshRequest, deps: Deps) -> RefreshResponse:
    service = _require_service(deps)
    return RefreshResponse(changes=await service.refresh(body.sheet_id))
