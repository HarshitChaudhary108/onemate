"""Exa structured-output extension tests (P2).

All response payloads below are SYNTHETIC, hand-written from the
documented shapes in ``docs/p0-openapi-notes.md`` (``output`` =
{content, grounding[{field, citations[{url,title}], confidence}]},
``costDollars`` object, optional ``warnings``/``notices`` tolerated and
ignored). No captured live fixtures exist; no network is touched — respx
intercepts every call.
"""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.providers.exa import EXA_SEARCH_URL, HttpExaProvider
from app.providers.exa_mock import FixtureExaProvider
from app.providers.types import ExaSearchRequest, ExaSearchResult

SYNTHETIC_SEARCH_PAYLOAD: dict[str, Any] = {
    "results": [
        {
            "title": "Example Robotics Ltd — About",
            "url": "https://example-robotics.test/about",
            "publishedDate": "2025-06-01",
            "highlights": ["Example Robotics Ltd is a synthetic fixture company."],
        }
    ],
    "costDollars": {"total": 0.007, "search": {"neural": 0.007}},
}

SYNTHETIC_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Synthetic test schema",
    "properties": {
        "found": {"type": "boolean", "description": "Whether a value was found"},
        "value": {"type": "string", "description": "The found value"},
    },
    "required": ["found", "value"],
}


async def _no_sleep(_: float) -> None:
    return None


async def _run_search(
    request: ExaSearchRequest, payload: dict[str, Any], status: int = 200
) -> tuple[ExaSearchResult, respx.Route]:
    with respx.mock:
        route = respx.post(EXA_SEARCH_URL).mock(return_value=httpx.Response(status, json=payload))
        provider = HttpExaProvider(SecretStr("synthetic-key"), sleep=_no_sleep)
        try:
            result = await provider.search(request)
        finally:
            await provider.aclose()
    return result, route


async def test_legacy_request_body_byte_identical() -> None:
    """A request without the new fields must send exactly today's body."""
    request = ExaSearchRequest(
        query="Example Robotics Ltd",
        category="company",
        include_domains=["example-robotics.test"],
    )
    _, route = await _run_search(request, SYNTHETIC_SEARCH_PAYLOAD)
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "query": "Example Robotics Ltd",
        "type": "auto",
        "numResults": 5,
        "contents": {"highlights": True},
        "category": "company",
        "includeDomains": ["example-robotics.test"],
    }


async def test_contents_omitted_when_include_contents_false() -> None:
    """``include_contents=False`` drops ``contents`` — nothing else changes."""
    request = ExaSearchRequest(
        query="Example Robotics Ltd",
        category="company",
        include_domains=["example-robotics.test"],
        include_contents=False,
    )
    _, route = await _run_search(request, SYNTHETIC_SEARCH_PAYLOAD)
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "query": "Example Robotics Ltd",
        "type": "auto",
        "numResults": 5,
        "category": "company",
        "includeDomains": ["example-robotics.test"],
    }


async def test_fast_search_type_serializes() -> None:
    """``search_type="fast"`` maps to the documented ``type`` field."""
    request = ExaSearchRequest(query="Example Robotics Ltd", search_type="fast")
    _, route = await _run_search(request, SYNTHETIC_SEARCH_PAYLOAD)
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "fast"


async def test_structured_request_body_contains_new_fields() -> None:
    request = ExaSearchRequest(
        query="Example Robotics Ltd revenue",
        include_domains=["example-robotics.test"],
        search_type="deep",
        system_prompt="Answer only about the resolved company.",
        output_schema=SYNTHETIC_OUTPUT_SCHEMA,
    )
    _, route = await _run_search(request, SYNTHETIC_SEARCH_PAYLOAD)
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "deep"
    assert body["systemPrompt"] == "Answer only about the resolved company."
    assert body["outputSchema"] == SYNTHETIC_OUTPUT_SCHEMA


async def test_output_parsed_when_present() -> None:
    payload = {
        **SYNTHETIC_SEARCH_PAYLOAD,
        "output": {
            "content": {"found": True, "value": "synthetic value"},
            "grounding": [
                {
                    "field": "value",
                    "citations": [
                        {
                            "url": "https://example-robotics.test/about",
                            "title": "Example Robotics Ltd — About",
                        }
                    ],
                    "confidence": "high",
                }
            ],
        },
        # Documented optional fields: tolerated and ignored.
        "warnings": [{"code": "IGNORED_FIELD", "message": "synthetic warning"}],
        "notices": ["synthetic notice"],
    }
    result, _ = await _run_search(
        ExaSearchRequest(query="q", output_schema=SYNTHETIC_OUTPUT_SCHEMA), payload
    )
    assert result.output is not None
    assert result.output.content == {"found": True, "value": "synthetic value"}
    assert result.output.grounding[0].field == "value"
    assert result.output.grounding[0].confidence == "high"
    assert result.output.grounding[0].citations[0].url == ("https://example-robotics.test/about")
    assert result.usage == {"total": 0.007, "search": {"neural": 0.007}}


async def test_output_none_when_absent() -> None:
    result, _ = await _run_search(
        ExaSearchRequest(query="q", output_schema=SYNTHETIC_OUTPUT_SCHEMA),
        SYNTHETIC_SEARCH_PAYLOAD,
    )
    assert result.output is None


async def test_output_none_when_malformed() -> None:
    malformed_payloads = [
        {**SYNTHETIC_SEARCH_PAYLOAD, "output": "not-an-object"},
        {**SYNTHETIC_SEARCH_PAYLOAD, "output": {"grounding": "bad"}},
        {
            **SYNTHETIC_SEARCH_PAYLOAD,
            "output": {
                "content": {},
                "grounding": [{"field": "value", "confidence": "ultra"}],
            },
        },
    ]
    for payload in malformed_payloads:
        result, _ = await _run_search(
            ExaSearchRequest(query="q", output_schema=SYNTHETIC_OUTPUT_SCHEMA), payload
        )
        assert result.output is None


def test_search_type_literal_rejects_deep_reasoning() -> None:
    with pytest.raises(ValidationError):
        ExaSearchRequest(query="q", search_type="deep-reasoning")  # type: ignore[arg-type]


async def test_fixture_mock_returns_synthetic_structured_result() -> None:
    """The mock's structured branch must not touch the missing fixture tree."""
    result = await FixtureExaProvider().search(
        ExaSearchRequest(query="anything", output_schema=SYNTHETIC_OUTPUT_SCHEMA)
    )
    assert result.mock is True
    assert result.output is not None
    assert result.output.content == {"found": True, "value": "synthetic mock value"}
    assert result.output.grounding[0].confidence == "low"
