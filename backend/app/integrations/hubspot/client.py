"""Live HubSpot adapter (``HttpHubSpotClient``) and the ``HubSpotClient``
protocol (plan §5.1).

Docs verified 2026-10-26 — developers.hubspot.com object/tokens references.
This module is the single retry boundary for HubSpot calls: transient
failures are retried once inside the adapter; callers must not retry again.

Auth: the caller passes a valid OAuth ``access_token`` per call (``Bearer``
header) — the client never stores tokens. Security: tokens are never
logged, never embedded in error messages, and request/response bodies are
never logged.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

from app.errors.integration_error import IntegrationError
from app.integrations.hubspot.models import RemoteObject, RemoteObjectType

# VERIFIED 2026-10-26 (developers.hubspot.com/docs/api-reference):
# - GET    /crm/v3/objects/{objectType}            list + paging.next.after
# - POST   /crm/v3/objects/{objectType}/batch/read {inputs:[{id}], properties:[..]}
# - PATCH  /crm/v3/objects/{objectType}/{recordId} {properties:{..}}
# - GET    /oauth/v1/access-tokens/{token}         token metadata (hub_id, scopes)
HUBSPOT_API_BASE = "https://api.hubapi.com"

_OBJECT_PATH: dict[RemoteObjectType, str] = {"company": "companies", "contact": "contacts"}
"""Model vocabulary (``company``/``contact``) → HubSpot API path segment."""

# Implementation choice (not a provider limit): if a required wait exceeds
# this budget we do not retry and report the error instead.
RETRY_WAIT_BUDGET_SECONDS = 10.0
# Bounded fallback backoff when no Retry-After metadata is present (<= 2 s cap).
BOUNDED_BACKOFF_SECONDS = 1.0

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_ATTEMPTS = 2  # one retry at most

Sleep = Callable[[float], Awaitable[None]]


@runtime_checkable
class HubSpotClient(Protocol):
    """HubSpot object/token surface used by the integration service.

    ``object_type`` uses the model vocabulary (``"company"``/``"contact"``);
    adapters translate it onto the API path segments (``companies`` etc.).
    Every method takes the caller's ``access_token`` explicitly — token
    storage and refresh live in :mod:`app.integrations.hubspot.oauth`.
    """

    async def list_objects(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        properties: Sequence[str],
        limit: int = 100,
        after: str | None = None,
    ) -> tuple[list[RemoteObject], str | None]:
        """One page of objects; second tuple element is the next ``after``
        cursor (``None`` on the last page)."""
        ...

    async def batch_read(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        ids: Sequence[str],
        properties: Sequence[str],
    ) -> list[RemoteObject]: ...

    async def patch_object(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        remote_id: str,
        properties: dict[str, str],
    ) -> None: ...

    async def token_info(self, access_token: str) -> dict[str, Any]:
        """Token metadata (``hub_id``, ``scopes``, …) — used to capture the
        portal id at connect time."""
        ...


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a numeric-seconds ``Retry-After`` header; anything else → None."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _hs_error(code: str) -> IntegrationError:
    return IntegrationError(code=code, message=_MESSAGES[code])


_MESSAGES = {
    "hubspot_auth": "HubSpot rejected the access token; reconnect HubSpot.",
    "hubspot_forbidden": "HubSpot denied permission for this request; check the granted scopes.",
    "hubspot_not_found": "The requested HubSpot object was not found.",
    "hubspot_property_missing": "The property does not exist in the HubSpot portal; check the outmate_* setup.",
    "hubspot_rate_limited": "HubSpot rate limit reached; try again shortly.",
    "hubspot_server_error": "HubSpot returned a server error; try again shortly.",
    "hubspot_timeout": "The HubSpot request timed out; try again.",
    "hubspot_network": "Could not reach HubSpot; check connectivity and try again.",
    "hubspot_api_error": "HubSpot returned an unexpected error; try again.",
    "hubspot_invalid_response": "HubSpot returned a response that could not be understood.",
}

_STATUS_CODES = {
    401: "hubspot_auth",
    403: "hubspot_forbidden",
    404: "hubspot_not_found",
}

_VALIDATION_CODES = {
    "PROPERTY_DOESNT_EXIST": "hubspot_property_missing",
}
"""HubSpot ``errors[].code`` subcodes that map to a distinct error — they
surface as the push item ``detail``, so a portal-setup gap reads as itself
instead of an unexplained ``hubspot_api_error``."""


def _first_error_code(response: httpx.Response) -> str | None:
    """First ``errors[].code`` in a HubSpot error body; ``None`` when the
    body has none. Never raises — a failed peek still maps to
    ``hubspot_api_error``."""
    try:
        payload = response.json()
    except ValueError:
        return None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        return code if isinstance(code, str) else None
    return None


class HttpHubSpotClient:
    """HubSpot REST client with a shared AsyncClient and one retry.

    ``calls`` counts real HTTP attempts across the client's lifetime —
    a retried call counts twice. ``aclose()`` only closes the transport
    this instance created; an injected client's lifecycle stays with the
    caller.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client if client is not None else httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None
        self._sleep = sleep
        self.calls = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpHubSpotClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def list_objects(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        properties: Sequence[str],
        limit: int = 100,
        after: str | None = None,
    ) -> tuple[list[RemoteObject], str | None]:
        params: dict[str, str | int] = {"limit": limit}
        if properties:
            params["properties"] = ",".join(properties)
        if after is not None:
            params["after"] = after
        response = await self._send(
            "GET",
            f"{HUBSPOT_API_BASE}/crm/v3/objects/{_OBJECT_PATH[object_type]}",
            access_token=access_token,
            params=params,
        )
        payload = _json_object(response)
        results = payload.get("results")
        if not isinstance(results, list):
            raise _hs_error("hubspot_invalid_response")
        objects = [_to_remote_object(item, object_type) for item in results]
        next_after: str | None = None
        paging = payload.get("paging")
        if isinstance(paging, dict):
            nxt = paging.get("next")
            if isinstance(nxt, dict) and isinstance(nxt.get("after"), str):
                next_after = nxt["after"]
        return objects, next_after

    async def batch_read(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        ids: Sequence[str],
        properties: Sequence[str],
    ) -> list[RemoteObject]:
        body = {
            "inputs": [{"id": remote_id} for remote_id in ids],
            "properties": list(properties),
        }
        response = await self._send(
            "POST",
            f"{HUBSPOT_API_BASE}/crm/v3/objects/{_OBJECT_PATH[object_type]}/batch/read",
            access_token=access_token,
            json=body,
        )
        payload = _json_object(response)
        results = payload.get("results")
        if not isinstance(results, list):
            raise _hs_error("hubspot_invalid_response")
        return [_to_remote_object(item, object_type) for item in results]

    async def patch_object(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        remote_id: str,
        properties: dict[str, str],
    ) -> None:
        await self._send(
            "PATCH",
            f"{HUBSPOT_API_BASE}/crm/v3/objects/{_OBJECT_PATH[object_type]}/{remote_id}",
            access_token=access_token,
            json={"properties": properties},
        )

    async def token_info(self, access_token: str) -> dict[str, Any]:
        response = await self._send(
            "GET",
            f"{HUBSPOT_API_BASE}/oauth/v1/access-tokens/{access_token}",
            access_token=access_token,
        )
        return _json_object(response)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        access_token: str,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send one request with the single-retry boundary; returns the 2xx
        response or raises :class:`IntegrationError`."""
        headers = {"Authorization": f"Bearer {access_token}"}
        attempt = 0
        while True:
            self.calls += 1
            try:
                response = await self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.TimeoutException as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _hs_error("hubspot_timeout") from exc
            except httpx.HTTPError as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _hs_error("hubspot_network") from exc

            status = response.status_code
            if 200 <= status < 300:
                return response

            if status == 429:
                wait = _retry_after_seconds(response)
                if attempt < _MAX_ATTEMPTS - 1 and (
                    wait is None or wait <= RETRY_WAIT_BUDGET_SECONDS
                ):
                    await self._sleep(wait if wait is not None else BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _hs_error("hubspot_rate_limited")

            if status >= 500:
                if attempt < _MAX_ATTEMPTS - 1:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _hs_error("hubspot_server_error")

            code = _STATUS_CODES.get(status)
            if code is None:
                code = _VALIDATION_CODES.get(
                    _first_error_code(response) or "", "hubspot_api_error"
                )
            raise _hs_error(code)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Parse a response body that must be a JSON object."""
    try:
        payload = response.json()
    except ValueError:
        raise _hs_error("hubspot_invalid_response") from None
    if not isinstance(payload, dict):
        raise _hs_error("hubspot_invalid_response")
    return payload


def _to_remote_object(item: Any, object_type: RemoteObjectType) -> RemoteObject:
    """Normalize one ``{id, properties}`` entry; anything else is invalid."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), (str, int)):
        raise _hs_error("hubspot_invalid_response")
    raw_props = item.get("properties")
    if raw_props is None:
        raw_props = {}
    if not isinstance(raw_props, dict):
        raise _hs_error("hubspot_invalid_response")
    properties = {str(k): (None if v is None else str(v)) for k, v in raw_props.items()}
    return RemoteObject(remote_id=str(item["id"]), object_type=object_type, properties=properties)


if TYPE_CHECKING:
    # Static check that the adapter satisfies the Protocol.
    _conformance_check: HubSpotClient = HttpHubSpotClient()
