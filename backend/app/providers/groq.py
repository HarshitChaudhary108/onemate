"""Live Groq adapter (``GroqProvider``).

Docs verified 2026-10-17 — plan §1.2 (Groq structured outputs). Groq's
OpenAI-compatible Chat Completions endpoint is called directly via httpx;
structured output uses native ``response_format`` with
``type: "json_schema"`` and ``strict: true``. ``tools``/``tool_choice``
are never sent — Groq documents that they cannot combine with Structured
Outputs.

This module is the single retry boundary for Groq calls, mirroring the
Exa adapter's boundary exactly: transient failures are retried once
inside the adapter; callers must not retry again.

Security: the API key is never logged, never embedded in error messages,
and request/response bodies are never logged.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

from app.errors.llm_error import LlmError
from app.providers.types import LlmProvider, LlmRequest, LlmResult

# VERIFIED 2026-10-17 (console.groq.com/docs): OpenAI-compatible endpoint.
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
# VERIFIED: Bearer token in the Authorization header (OpenAI-compatible).
GROQ_AUTH_HEADER = "Authorization"

# Implementation choice (not a provider limit): if a required wait exceeds
# this budget we do not retry and report the error instead.
RETRY_WAIT_BUDGET_SECONDS = 10.0
# Bounded fallback backoff when no Retry-After metadata is present (<= 2 s cap).
BOUNDED_BACKOFF_SECONDS = 1.0

_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_ATTEMPTS = 2  # one retry at most

# The json_schema envelope requires a name; one fixed name is enough.
_SCHEMA_NAME = "structured_output"

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


def _groq_error(code: str, message: str, **kwargs: Any) -> LlmError:
    return LlmError(provider="Groq", code=code, message=message, **kwargs)


_MESSAGES = {
    "groq_not_configured": "Groq is not configured: set GROQ_API_KEY on the backend.",
    "groq_bad_request": "Groq rejected the request; nothing was generated.",
    "groq_invalid_key": "The Groq API key was rejected; check GROQ_API_KEY.",
    "groq_model_not_found": "The configured planner model is not available on Groq.",
    "groq_invalid_request": "The request is invalid for Groq; it was not sent again.",
    "groq_rate_limited": "Groq rate limit reached; try again shortly.",
    "groq_timeout": "The Groq request timed out; try again.",
    "groq_network": "Could not reach Groq; check connectivity and try again.",
    "groq_server_error": "Groq returned a server error; try again shortly.",
    "groq_invalid_response": "Groq returned a response that could not be understood.",
}

_STATUS_CODES = {
    400: "groq_bad_request",
    401: "groq_invalid_key",
    404: "groq_model_not_found",
    422: "groq_invalid_request",
}


class GroqProvider:
    """Groq Chat Completions client with a shared AsyncClient and one retry."""

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

    async def __aenter__(self) -> "GroqProvider":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def complete(self, request: LlmRequest) -> LlmResult:
        key = self._api_key.get_secret_value().strip()
        if not key:
            raise _groq_error(
                "groq_not_configured", _MESSAGES["groq_not_configured"], retryable=False
            )

        body = self._build_body(request)
        headers = {GROQ_AUTH_HEADER: f"Bearer {key}"}
        calls = 0
        attempt = 0

        while True:
            calls += 1
            try:
                response = await self._client.post(GROQ_CHAT_URL, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _groq_error(
                    "groq_timeout", _MESSAGES["groq_timeout"], retryable=True, calls=calls
                ) from exc
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await self._sleep(BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _groq_error(
                    "groq_network", _MESSAGES["groq_network"], retryable=True, calls=calls
                ) from exc

            status = response.status_code
            if status == 200:
                return self._parse_success(response, calls)

            if status == 429:
                wait = _retry_after_seconds(response)
                if attempt == 0 and (wait is None or wait <= RETRY_WAIT_BUDGET_SECONDS):
                    await self._sleep(wait if wait is not None else BOUNDED_BACKOFF_SECONDS)
                    attempt += 1
                    continue
                raise _groq_error(
                    "groq_rate_limited",
                    _MESSAGES["groq_rate_limited"],
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
                raise _groq_error(
                    "groq_server_error",
                    _MESSAGES["groq_server_error"],
                    retryable=True,
                    http_status=status,
                    calls=calls,
                )

            code = _STATUS_CODES.get(status, "groq_bad_request")
            raise _groq_error(
                code, _MESSAGES[code], retryable=False, http_status=status, calls=calls
            )

    @staticmethod
    def _build_body(request: LlmRequest) -> dict[str, Any]:
        # Structured Outputs via response_format only — never tools or
        # tool_choice in the same request (Groq documents the combination
        # as unsupported).
        return {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": _SCHEMA_NAME,
                    "schema": request.json_schema,
                    "strict": True,
                },
            },
        }

    @staticmethod
    def _parse_success(response: httpx.Response, calls: int) -> LlmResult:
        invalid = _groq_error(
            "groq_invalid_response",
            _MESSAGES["groq_invalid_response"],
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
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise invalid
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise invalid
        try:
            parsed = json.loads(content)
        except ValueError:
            raise invalid from None
        usage = payload.get("usage")
        return LlmResult(
            content=parsed,
            usage=usage if isinstance(usage, dict) else None,
            calls=calls,
            mock=False,
        )


if TYPE_CHECKING:
    # Static check that the adapter satisfies the Protocol.
    _conformance_check: LlmProvider = GroqProvider(SecretStr("unused"))
