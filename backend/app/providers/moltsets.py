"""Live MoltSets adapter (``HttpMoltSetsProvider``).

Docs verified 2026-10-16 against developer.moltsets.com — see
``docs/provider-notes.md``. Only the ``search_companies`` and
``search_people`` tools are ever called. Phone, personal-email and every
other tool are out of scope.

This module is the single retry boundary for MoltSets calls: transient
failures are retried once inside the adapter; the orchestrator must not
retry again. Company lookups are cached per-process by domain comparison
key; people results are never cached (tech section 10).

Security: the API key and business emails are never logged and never appear
in error messages. Provider response bodies are never logged.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr, ValidationError

from app.errors.provider_error import ProviderError
from app.normalize.domain import DomainError, normalize_domain
from app.providers.types import (
    MoltSetsCompany,
    MoltSetsCompanyResult,
    MoltSetsPeopleResult,
    MoltSetsPerson,
    MoltSetsProvider,
)

# VERIFIED 2026-10-16 (developer.moltsets.com): base URL and tool paths.
MOLTSETS_BASE_URL = "https://api.moltsets.com/api/v1/tools"
COMPANY_TOOL_PATH = "/search_companies"
PEOPLE_TOOL_PATH = "/search_people"
# VERIFIED: `Authorization: Bearer <key>`; `User-Agent` must be non-empty.
AUTH_HEADER = "Authorization"
USER_AGENT_HEADER = "User-Agent"

# Verified request field names: `domain` (exact match) for search_companies;
# `company_domain`, `seniority`, `limit` for search_people.
PEOPLE_LIMIT_MIN = 1
PEOPLE_LIMIT_MAX = 5  # prototype cap; API allows up to 25

# Implementation choice (same as the Exa adapter): if a required wait exceeds
# this budget we do not retry and report the error instead.
RETRY_WAIT_BUDGET_SECONDS = 10.0
BOUNDED_BACKOFF_SECONDS = 1.0

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

Sleep = Callable[[float], Awaitable[None]]

_MESSAGES = {
    "moltsets_not_configured": "MoltSets is not configured: set MOLTSETS_API_KEY on the backend.",
    "moltsets_missing_user_agent": (
        "MoltSets requires a non-empty User-Agent; set MOLTSETS_USER_AGENT."
    ),
    "moltsets_invalid_key": "The MoltSets API key was rejected; check MOLTSETS_API_KEY.",
    "moltsets_payment_required": "The MoltSets account is out of tokens; top up or upgrade.",
    "moltsets_plan_restricted": "This MoltSets tool is not part of the account's plan.",
    "moltsets_tool_not_found": "The MoltSets tool name was not recognized.",
    "moltsets_invalid_input": "MoltSets rejected the lookup parameters.",
    "moltsets_rate_limited": "MoltSets rate limit reached; try again shortly.",
    "moltsets_quota_exhausted": (
        "The MoltSets fair-use limit is exhausted with no reset time; "
        "enrichment is unavailable for this run."
    ),
    "moltsets_timeout": "The MoltSets request timed out; try again.",
    "moltsets_network": "Could not reach MoltSets; check connectivity and try again.",
    "moltsets_server_error": "MoltSets returned a server error; try again later.",
    "moltsets_invalid_response": "MoltSets returned a response that could not be understood.",
}

# Body error.code -> internal code. Decided from the body first, then the
# HTTP status (spec section 6: "Branch on the response body/error code").
_BODY_CODE_MAP = {
    "unauthorized": "moltsets_invalid_key",
    "insufficient_tokens": "moltsets_payment_required",
    "insufficient_phone_tokens": "moltsets_payment_required",
    "forbidden": "moltsets_missing_user_agent",
    "plan_restricted": "moltsets_plan_restricted",
    "account_inactive": "moltsets_plan_restricted",
    "tool_not_found": "moltsets_tool_not_found",
    "invalid_input": "moltsets_invalid_input",
    "rate_limited": "moltsets_rate_limited",
    "fair_use_requests_exceeded": "moltsets_rate_limited",
    "fair_use_limit_exceeded": "moltsets_quota_exhausted",
    "internal_error": "moltsets_server_error",
}

_STATUS_CODE_MAP = {
    401: "moltsets_invalid_key",
    402: "moltsets_payment_required",
    # 403 fallback: docs say plan_restricted when the body does not
    # disambiguate between missing User-Agent and plan restriction.
    403: "moltsets_plan_restricted",
    404: "moltsets_tool_not_found",
    422: "moltsets_invalid_input",
}


def _ms_error(code: str, **kwargs: Any) -> ProviderError:
    return ProviderError(provider="MoltSets", code=code, message=_MESSAGES[code], **kwargs)


def _body_error_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                return code
    return None


def _retry_after_seconds(response: httpx.Response, payload: Any) -> float | None:
    """Retry hint in seconds: ``metadata.retry_after`` (body) first, then the
    ``Retry-After`` header. ``None`` means no usable retry time."""
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("retry_after")
            if isinstance(value, int | float) and value >= 0:
                return float(value)
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


def _map_error(
    response: httpx.Response, payload: Any, calls: int
) -> tuple[ProviderError, float | None]:
    """Return ``(error, wait)``. ``wait`` is not ``None`` only when one
    bounded retry is permitted for this attempt."""
    status = response.status_code
    body_code = _body_error_code(payload)

    if status == 429:
        wait = _retry_after_seconds(response, payload)
        if body_code == "fair_use_limit_exceeded" and wait is None:
            return (
                _ms_error(
                    "moltsets_quota_exhausted",
                    retryable=False,
                    http_status=status,
                    calls=calls,
                ),
                None,
            )
        return (
            _ms_error(
                "moltsets_rate_limited",
                retryable=True,
                retry_after=wait,
                http_status=status,
                calls=calls,
            ),
            wait if wait is not None else BOUNDED_BACKOFF_SECONDS,
        )

    code = _BODY_CODE_MAP.get(body_code or "", "")
    if not code:
        code = _STATUS_CODE_MAP.get(
            status, "moltsets_server_error" if status >= 500 else "moltsets_invalid_input"
        )

    retryable = code == "moltsets_server_error"
    return (
        _ms_error(code, retryable=retryable, http_status=status, calls=calls),
        BOUNDED_BACKOFF_SECONDS if retryable else None,
    )


class HttpMoltSetsProvider:
    """MoltSets company + people client with one bounded retry.

    ``search_company`` results (found or not_found) are cached in process
    memory keyed by the normalized domain comparison key; errors are never
    cached. A cache hit reports ``calls=0``.
    """

    def __init__(
        self,
        api_key: SecretStr,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._user_agent = user_agent
        self._client = client if client is not None else httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None
        self._sleep = sleep
        self._company_cache: dict[str, MoltSetsCompanyResult] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpMoltSetsProvider":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- public API ---------------------------------------------------------

    async def search_company(self, domain: str) -> MoltSetsCompanyResult:
        compare_key = self._compare_key(domain)
        cached = self._company_cache.get(compare_key)
        if cached is not None:
            return cached.model_copy(update={"calls": 0})

        response, calls = await self._post(COMPANY_TOOL_PATH, {"domain": compare_key})
        payload = self._parse_envelope(response, calls)
        usage = self._usage(payload)

        if payload["status"] == "not_found":
            result = MoltSetsCompanyResult(found=False, calls=calls, usage=usage)
        elif payload["status"] == "ok":
            items = payload["results"].get("results")
            if not isinstance(items, list):
                raise self._invalid_response(response, calls)
            if not items:
                # Defensive: ok-with-empty is treated as a clean miss.
                result = MoltSetsCompanyResult(found=False, calls=calls, usage=usage)
            else:
                try:
                    company = MoltSetsCompany.model_validate(items[0])
                except ValidationError:
                    raise self._invalid_response(response, calls) from None
                result = MoltSetsCompanyResult(
                    found=True, company=company, calls=calls, usage=usage
                )
        else:
            raise self._invalid_response(response, calls)

        self._company_cache[compare_key] = result
        return result

    async def search_people(
        self,
        company_domain: str,
        seniority: list[str],
        limit: int = PEOPLE_LIMIT_MAX,
    ) -> MoltSetsPeopleResult:
        compare_key = self._compare_key(company_domain)
        capped = max(PEOPLE_LIMIT_MIN, min(limit, PEOPLE_LIMIT_MAX))
        body: dict[str, Any] = {"company_domain": compare_key, "limit": capped}
        if seniority:
            body["seniority"] = seniority

        response, calls = await self._post(PEOPLE_TOOL_PATH, body)
        payload = self._parse_envelope(response, calls)
        usage = self._usage(payload)

        if payload["status"] == "not_found":
            return MoltSetsPeopleResult(people=[], calls=calls, usage=usage)
        if payload["status"] != "ok":
            raise self._invalid_response(response, calls)
        items = payload["results"].get("results")
        if not isinstance(items, list):
            raise self._invalid_response(response, calls)
        try:
            people = [MoltSetsPerson.model_validate(item) for item in items]
        except ValidationError:
            raise self._invalid_response(response, calls) from None
        return MoltSetsPeopleResult(people=people, calls=calls, usage=usage)

    # -- internals ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        user_agent = self._user_agent.strip()
        if not user_agent:
            raise _ms_error("moltsets_missing_user_agent", retryable=False)
        key = self._api_key.get_secret_value().strip()
        if not key:
            raise _ms_error("moltsets_not_configured", retryable=False)
        return {
            AUTH_HEADER: f"Bearer {key}",
            USER_AGENT_HEADER: user_agent,
        }

    @staticmethod
    def _compare_key(domain: str) -> str:
        """Accept only a NormalizedDomain-style domain string (bare host or
        http(s) URL); the canonical comparison key is used for the request
        and the cache."""
        try:
            return normalize_domain(domain).compare_key
        except DomainError:
            raise _ms_error(
                "moltsets_invalid_input",
                retryable=False,
            ) from None

    async def _post(self, path: str, body: dict[str, Any]) -> tuple[httpx.Response, int]:
        headers = self._headers()  # local checks run before any HTTP attempt
        url = f"{MOLTSETS_BASE_URL}{path}"
        calls = 0
        attempt = 0
        while True:
            calls += 1
            try:
                response = await self._client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _ms_error("moltsets_timeout", retryable=True, calls=calls) from exc
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _ms_error("moltsets_network", retryable=True, calls=calls) from exc

            if response.status_code == 200:
                return response, calls

            payload: Any = None
            try:
                payload = response.json()
            except ValueError:
                payload = None

            error, wait = _map_error(response, payload, calls)
            if attempt == 0 and wait is not None and wait <= RETRY_WAIT_BUDGET_SECONDS:
                await self._sleep(wait)
                attempt += 1
                continue
            raise error

    @staticmethod
    def _parse_envelope(response: httpx.Response, calls: int) -> dict[str, Any]:
        invalid = HttpMoltSetsProvider._invalid_response(response, calls)
        try:
            payload = response.json()
        except ValueError:
            raise invalid from None
        if not isinstance(payload, dict):
            raise invalid
        status = payload.get("status")
        results = payload.get("results")
        if not isinstance(status, str) or not isinstance(results, dict):
            raise invalid
        return payload

    @staticmethod
    def _invalid_response(response: httpx.Response, calls: int) -> ProviderError:
        return _ms_error(
            "moltsets_invalid_response",
            retryable=False,
            http_status=response.status_code,
            calls=calls,
        )

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, Any] | None:
        metadata = payload.get("metadata")
        return metadata if isinstance(metadata, dict) else None


if TYPE_CHECKING:
    _conformance_check: MoltSetsProvider = HttpMoltSetsProvider(SecretStr("x"), "agent/1.0")
