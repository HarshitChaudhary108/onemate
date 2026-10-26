"""Live Exa adapter (``HttpExaProvider``).

Docs verified 2026-10-16 — see ``docs/provider-notes.md``. This module is the
single retry boundary for Exa calls: transient failures are retried once
inside the adapter; the orchestrator must not retry again.

Security: the API key is never logged, never embedded in error messages, and
request/response bodies are never logged.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr, ValidationError

from app.errors.provider_error import ProviderError
from app.providers.types import (
    ExaHit,
    ExaOutput,
    ExaProvider,
    ExaSearchRequest,
    ExaSearchResult,
)

# VERIFIED 2026-10-16 (docs.exa.ai/reference/search, OpenAPI spec):
EXA_SEARCH_URL = "https://api.exa.ai/search"
# VERIFIED: both `x-api-key` and `Authorization: Bearer` are documented for
# /search. We standardize on `x-api-key` (the reference curl sample).
EXA_AUTH_HEADER = "x-api-key"

# Implementation choice (not a provider limit): if a required wait exceeds
# this budget we do not retry and report the error instead.
RETRY_WAIT_BUDGET_SECONDS = 10.0
# Bounded fallback backoff when no Retry-After metadata is present (<= 2 s cap).
BOUNDED_BACKOFF_SECONDS = 1.0

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_ATTEMPTS = 2  # one retry at most

Sleep = Callable[[float], Awaitable[None]]


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


def _exa_error(code: str, message: str, **kwargs: Any) -> ProviderError:
    return ProviderError(provider="Exa", code=code, message=message, **kwargs)


_MESSAGES = {
    "exa_not_configured": "Exa search is not configured: set EXA_API_KEY on the backend.",
    "exa_bad_request": "Exa rejected the search request; nothing was searched.",
    "exa_invalid_key": "The Exa API key was rejected; check EXA_API_KEY.",
    "exa_credits_exhausted": "The Exa account is out of credits; top up or retry later.",
    "exa_invalid_request": "The search request is invalid for Exa; it was not sent again.",
    "exa_rate_limited": "Exa rate limit reached; try again shortly.",
    "exa_timeout": "The Exa request timed out; try again.",
    "exa_network": "Could not reach Exa; check connectivity and try again.",
    "exa_server_error": "Exa returned a server error; try again shortly.",
    "exa_invalid_response": "Exa returned a response that could not be understood.",
}

_STATUS_CODES = {
    400: "exa_bad_request",
    401: "exa_invalid_key",
    402: "exa_credits_exhausted",
    422: "exa_invalid_request",
}


class HttpExaProvider:
    """Exa ``/search`` client with a shared AsyncClient and one retry."""

    def __init__(
        self,
        api_key: SecretStr,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=_TIMEOUT)
        self._owns_client = client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpExaProvider":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def search(self, request: ExaSearchRequest) -> ExaSearchResult:
        key = self._api_key.get_secret_value().strip()
        if not key:
            raise _exa_error("exa_not_configured", _MESSAGES["exa_not_configured"], retryable=False)

        body = self._build_body(request)
        headers = {EXA_AUTH_HEADER: key}
        calls = 0
        attempt = 0

        while True:
            calls += 1
            try:
                response = await self._client.post(EXA_SEARCH_URL, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _exa_error(
                    "exa_timeout", _MESSAGES["exa_timeout"], retryable=True, calls=calls
                ) from exc
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _exa_error(
                    "exa_network", _MESSAGES["exa_network"], retryable=True, calls=calls
                ) from exc

            status = response.status_code
            if status == 200:
                return self._parse_success(response, request, calls)

            if status == 429:
                wait = _retry_after_seconds(response)
                if attempt == 0 and (wait is None or wait <= RETRY_WAIT_BUDGET_SECONDS):
                    await self._sleep(wait if wait is not None else BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _exa_error(
                    "exa_rate_limited",
                    _MESSAGES["exa_rate_limited"],
                    retryable=True,
                    retry_after=wait,
                    http_status=429,
                    calls=calls,
                )

            if status >= 500:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _exa_error(
                    "exa_server_error",
                    _MESSAGES["exa_server_error"],
                    retryable=True,
                    http_status=status,
                    calls=calls,
                )

            code = _STATUS_CODES.get(status, "exa_bad_request")
            raise _exa_error(
                code, _MESSAGES[code], retryable=False, http_status=status, calls=calls
            )

    @staticmethod
    def _build_body(request: ExaSearchRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": request.query,
            "type": request.search_type,
            "numResults": request.num_results,
        }
        if request.include_contents:
            body["contents"] = {"highlights": True}
        if request.category:
            body["category"] = request.category
        if request.include_domains:
            body["includeDomains"] = request.include_domains
        if request.start_published_date:
            body["startPublishedDate"] = request.start_published_date
        if request.end_published_date:
            body["endPublishedDate"] = request.end_published_date
        # Structured output (plan §1.2): only sent when requested, so a
        # legacy request produces a byte-identical body.
        if request.system_prompt:
            body["systemPrompt"] = request.system_prompt
        if request.output_schema is not None:
            body["outputSchema"] = request.output_schema
        return body

    @staticmethod
    def _parse_success(
        response: httpx.Response, request: ExaSearchRequest, calls: int
    ) -> ExaSearchResult:
        invalid = _exa_error(
            "exa_invalid_response",
            _MESSAGES["exa_invalid_response"],
            retryable=False,
            http_status=response.status_code,
            calls=calls,
        )
        try:
            payload = response.json()
        except ValueError:
            raise invalid from None
        if not isinstance(payload, dict):
            raise invalid
        results = payload.get("results")
        if not isinstance(results, list):
            raise invalid
        try:
            hits = [ExaHit.model_validate(item) for item in results]
        except ValidationError:
            raise invalid from None
        usage = payload.get("costDollars")
        # `output` is only present when outputSchema was sent; a missing or
        # malformed value is a normal outcome (None), never an error.
        # Optional `warnings`/`notices` fields are tolerated and ignored.
        output: ExaOutput | None = None
        raw_output = payload.get("output")
        if raw_output is not None:
            try:
                output = ExaOutput.model_validate(raw_output)
            except ValidationError:
                output = None
        return ExaSearchResult(
            hits=hits,
            query=request.query,
            usage=usage if isinstance(usage, dict) else None,
            output=output,
            calls=calls,
            mock=False,
        )


if TYPE_CHECKING:
    # Static check that the adapter satisfies the Protocol.
    _conformance_check: ExaProvider = HttpExaProvider(SecretStr("unused"))
