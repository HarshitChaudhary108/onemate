"""Groq adapter tests (P2).

All response payloads are SYNTHETIC, hand-written from Groq's documented
OpenAI-compatible Chat Completions shape (``choices[0].message.content``)
and Structured Outputs rules (plan §1.2: ``response_format`` with
``type: "json_schema"`` / ``strict: true``; never ``tools``/``tool_choice``).
No captured live fixtures exist; respx intercepts every call.
"""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.errors.llm_error import LlmError
from app.providers.groq import GROQ_CHAT_URL, GroqProvider
from app.providers.groq_mock import GroqMockProvider
from app.providers.types import LlmRequest, LlmResult

SYNTHETIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["add_columns", "needs_clarification", "unsupported"],
        },
        "columns": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["intent"],
    "additionalProperties": False,
}


def _request() -> LlmRequest:
    return LlmRequest(
        model="openai/gpt-oss-120b",
        system_prompt="Synthetic test system prompt.",
        user_prompt="Add a revenue column.",
        json_schema=SYNTHETIC_SCHEMA,
    )


def _chat_response(content: str, status: int = 200) -> httpx.Response:
    payload: dict[str, Any] = {
        "id": "chatcmpl-synthetic",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return httpx.Response(status, json=payload)


async def _no_sleep(_: float) -> None:
    return None


async def _run(request: LlmRequest | None = None, key: str = "synthetic-key") -> LlmResult:
    provider = GroqProvider(SecretStr(key), sleep=_no_sleep)
    try:
        return await provider.complete(request or _request())
    finally:
        await provider.aclose()


async def test_valid_strict_json_response_parses() -> None:
    with respx.mock:
        route = respx.post(GROQ_CHAT_URL).mock(
            return_value=_chat_response('{"intent": "add_columns", "columns": []}')
        )
        result = await _run()
    assert result.content == {"intent": "add_columns", "columns": []}
    assert result.mock is False
    assert result.calls == 1
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer synthetic-key"
    body = json.loads(request.content)
    assert "tools" not in body
    assert "tool_choice" not in body
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"] == [
        {"role": "system", "content": "Synthetic test system prompt."},
        {"role": "user", "content": "Add a revenue column."},
    ]
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == SYNTHETIC_SCHEMA
    assert response_format["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    "choices",
    [
        [],  # no choices at all
        [{"index": 0, "message": {"role": "assistant"}}],  # missing content
        [{"index": 0, "message": {"role": "assistant", "content": ""}}],
        [{"index": 0, "message": {"role": "assistant", "content": "   "}}],
        [{"index": 0, "message": {"role": "assistant", "content": "not json {"}}],
    ],
    ids=["no-choices", "missing-content", "empty-content", "blank-content", "bad-json"],
)
async def test_unusable_response_raises_llm_error(choices: list[Any]) -> None:
    with respx.mock:
        respx.post(GROQ_CHAT_URL).mock(
            return_value=httpx.Response(200, json={"id": "x", "choices": choices})
        )
        with pytest.raises(LlmError) as excinfo:
            await _run()
    assert excinfo.value.code == "groq_invalid_response"
    assert excinfo.value.provider == "Groq"
    assert excinfo.value.calls == 1


async def test_provider_error_body_raises_llm_error() -> None:
    with respx.mock:
        respx.post(GROQ_CHAT_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "synthetic provider error detail",
                        "type": "invalid_request_error",
                    }
                },
            )
        )
        with pytest.raises(LlmError) as excinfo:
            await _run()
    assert excinfo.value.code == "groq_bad_request"
    assert excinfo.value.http_status == 400
    assert excinfo.value.retryable is False
    assert excinfo.value.calls == 1
    # The provider body never leaks into the error's safe message.
    assert "synthetic provider error detail" not in str(excinfo.value)


async def test_429_retries_exactly_once_then_succeeds() -> None:
    with respx.mock:
        route = respx.post(GROQ_CHAT_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "0"}),
                _chat_response('{"intent": "needs_clarification"}'),
            ]
        )
        result = await _run()
    assert result.content == {"intent": "needs_clarification"}
    assert result.calls == 2
    assert len(route.calls) == 2


async def test_429_twice_raises_rate_limited() -> None:
    with respx.mock:
        route = respx.post(GROQ_CHAT_URL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "0"})
        )
        with pytest.raises(LlmError) as excinfo:
            await _run()
    assert excinfo.value.code == "groq_rate_limited"
    assert excinfo.value.retryable is True
    assert excinfo.value.http_status == 429
    assert excinfo.value.calls == 2
    assert len(route.calls) == 2


async def test_5xx_retries_once_then_raises() -> None:
    with respx.mock:
        route = respx.post(GROQ_CHAT_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(LlmError) as excinfo:
            await _run()
    assert excinfo.value.code == "groq_server_error"
    assert excinfo.value.calls == 2
    assert len(route.calls) == 2


async def test_blank_key_is_coded_not_configured() -> None:
    with respx.mock:
        route = respx.post(GROQ_CHAT_URL)
        with pytest.raises(LlmError) as excinfo:
            await _run(key="   ")
    assert excinfo.value.code == "groq_not_configured"
    assert excinfo.value.calls == 0
    assert len(route.calls) == 0


async def test_mock_provider_is_deterministic_and_schema_adherent() -> None:
    result = await GroqMockProvider().complete(_request())
    assert result.mock is True
    assert result.calls == 1
    # enum honored -> first option; only `required` properties emitted.
    assert result.content == {"intent": "add_columns"}
    assert await GroqMockProvider().complete(_request()) == result
