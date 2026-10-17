"""Deterministic Exa fixture adapter (``FixtureExaProvider``).

Used only in tests or when ``EXA_MODE=mock`` is explicitly configured. Every
result carries ``mock=True``. Scenario selection is deterministic, driven by
substrings in the request ``query``/``include_domains`` (see
``scenario_fixture``); any unknown company honestly finds nothing.

Success-scenario hits have their host rewritten to the marker domain found
in the request text (e.g. ``moltsets-error.test``, ``fallback.test``), so a
fixture company can actually resolve for a domain other than
``example-robotics.test``. The ambiguous and empty fixtures are never
rewritten — ambiguity would be destroyed if every hit landed on one host.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from app.providers.types import (
    ExaCitation,
    ExaGroundingEntry,
    ExaHit,
    ExaOutput,
    ExaProvider,
    ExaSearchRequest,
    ExaSearchResult,
)

# Fixtures live in the test tree (synthetic data, obviously fictional).
_DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# Domains that select the full-success scenario.
_SUCCESS_MARKERS = ("example-robotics.test", "moltsets-error.test")
_EMPTY_MARKERS = ("no-evidence.test",)
_AMBIGUOUS_MARKERS = ("ambiguous",)
# The bounded identity-fallback scenario: the primary company-category
# identity search finds nothing; every other search gets its intent fixture.
_FALLBACK_MARKER = "fallback.test"
# Marker domains whose fixture hits are rewritten to the requested host.
_REWRITE_MARKERS = (*_SUCCESS_MARKERS, _FALLBACK_MARKER)

_LEADERSHIP_KEYWORDS = (
    "leadership",
    "leader",
    "executive",
    "founder",
    "ceo",
    "cto",
    "cfo",
    "vp",
    "team",
    "decision maker",
    "decision-maker",
)
_OVERVIEW_KEYWORDS = (
    "overview",
    "about",
    "product",
    "customers",
    "market",
    "description",
    "industry",
    "what does",
)


def _request_text(request: ExaSearchRequest) -> str:
    domains = " ".join(request.include_domains or [])
    return f"{request.query} {domains}".lower()


def _intent_fixture(request: ExaSearchRequest) -> str:
    """Map one request's intent to a fixture file for the success scenario."""
    if request.category == "news":
        return "exa_growth.json"
    query = request.query.lower()
    if any(k in query for k in _LEADERSHIP_KEYWORDS):
        return "exa_leadership.json"
    if any(k in query for k in _OVERVIEW_KEYWORDS):
        return "exa_overview.json"
    return "exa_identity_hit.json"


def scenario_fixture(request: ExaSearchRequest) -> str:
    """Choose the fixture file for a request, deterministically.

    Scenario markers (case-insensitive substring of ``query`` or
    ``include_domains``):

    - ``no-evidence.test`` -> empty results for every search
    - ``ambiguous`` -> the multi-host identity fixture for the identity
      search; later searches find nothing (the orchestrator would not run
      them anyway)
    - ``fallback.test`` -> the primary identity search (``category ==
      "company"``) finds nothing; every other search gets its intent fixture
      (the category-less fallback identity search finds a domain hit)
    - ``example-robotics.test`` / ``moltsets-error.test`` -> full success,
      fixture chosen by intent (identity/overview/growth/leadership)
    - anything else -> empty results (mock mode is honest: an unknown
      company finds nothing)
    """
    text = _request_text(request)
    if any(marker in text for marker in _EMPTY_MARKERS):
        return "exa_empty.json"
    if any(marker in text for marker in _AMBIGUOUS_MARKERS):
        if _intent_fixture(request) == "exa_identity_hit.json":
            return "exa_identity_ambiguous.json"
        return "exa_empty.json"
    if _FALLBACK_MARKER in text:
        if request.category == "company":
            return "exa_empty.json"
        return _intent_fixture(request)
    if any(marker in text for marker in _SUCCESS_MARKERS):
        return _intent_fixture(request)
    return "exa_empty.json"


def _rewrite_hit_host(hit: ExaHit, domain: str) -> ExaHit:
    """Swap the fixture host for the requested marker domain, keeping the
    scheme, path and any subdomain prefix (``news.`` etc.)."""
    if not hit.url:
        return hit
    try:
        parts = urlsplit(hit.url)
    except ValueError:
        return hit
    host = parts.hostname or ""
    if "example-robotics.test" not in host:
        return hit
    new_host = host.replace("example-robotics.test", domain)
    try:
        return hit.model_copy(update={"url": urlunsplit(parts._replace(netloc=new_host))})
    except ValueError:
        return hit


def _synthetic_value(prop: Any) -> Any:
    """Deterministic placeholder for one declared JSON-schema property."""
    prop_type = prop.get("type") if isinstance(prop, dict) else None
    if prop_type == "boolean":
        return True
    if prop_type in ("integer", "number"):
        return 0
    if prop_type == "array":
        return []
    if prop_type == "object":
        return _synthetic_object(prop)
    return "synthetic mock value"


def _synthetic_object(schema: dict[str, Any]) -> dict[str, Any]:
    """Fill every declared property of an object schema with a placeholder."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {name: _synthetic_value(prop) for name, prop in properties.items()}


def _synthetic_output(request: ExaSearchRequest) -> ExaOutput:
    """Build a deterministic structured result from the request's schema.

    Clearly synthetic: grounding uses ``confidence="low"`` and citations on
    the reserved ``mock.test`` domain, so mock output can never surface as
    verified data downstream.
    """
    schema = request.output_schema or {}
    content: Any = (
        "synthetic mock value" if schema.get("type") == "text" else _synthetic_object(schema)
    )
    fields = list(content) if isinstance(content, dict) else ["content"]
    grounding = [
        ExaGroundingEntry(
            field=name,
            citations=[
                ExaCitation(
                    url="https://synthetic.mock.test/citation",
                    title="Synthetic mock citation",
                )
            ],
            confidence="low",
        )
        for name in fields
    ]
    return ExaOutput(content=content, grounding=grounding)


def _company_search_hits(request: ExaSearchRequest) -> ExaSearchResult:
    """Deterministic synthetic hits for a mock list-building search (P9a).

    An open-web ``category="company"`` search (no ``include_domains``, no
    ``output_schema``) gets ``num_results`` synthetic hits on the reserved
    ``.test`` TLD, with the host label derived from the query text so the
    same query always yields the same companies. No fixture file is read —
    the fixture tree is absent from this repo. Clearly synthetic: never
    real company facts.
    """
    words = re.findall(r"[a-z0-9]+", request.query.lower())
    slug = "-".join(words[:3])[:40].strip("-") or "company"
    hits = [
        ExaHit(
            url=f"https://{slug}-{i}.mock-companies.test/",
            title=f"Synthetic company {i} for {slug}",
        )
        for i in range(1, request.num_results + 1)
    ]
    return ExaSearchResult(
        hits=hits,
        query=request.query,
        usage={"total": 0.015, "search": {"neural": 0.015}},
        calls=1,
        mock=True,
    )


class FixtureExaProvider:
    """Fixture-backed Exa provider. Never performs I/O beyond local files."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir or _DEFAULT_FIXTURE_DIR

    async def search(self, request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is not None:
            # Structured-output branch: deterministic synthetic result built
            # from the request schema. No fixture file is read — the fixture
            # tree is absent from this repo.
            return ExaSearchResult(
                hits=[],
                query=request.query,
                output=_synthetic_output(request),
                calls=1,
                mock=True,
            )
        if request.category == "company" and not request.include_domains:
            # List-building search (P9a): synthetic company hits — no
            # fixture file. Identity searches (which set include_domains)
            # keep the fixture-driven path below.
            return _company_search_hits(request)
        fixture_name = scenario_fixture(request)
        payload = json.loads((self._fixture_dir / fixture_name).read_text())
        results = payload.get("results", [])
        hits = [ExaHit.model_validate(item) for item in results if isinstance(item, dict)]
        if fixture_name not in ("exa_empty.json", "exa_identity_ambiguous.json"):
            text = _request_text(request)
            marker = next((m for m in _REWRITE_MARKERS if m in text), None)
            if marker is not None:
                hits = [_rewrite_hit_host(hit, marker) for hit in hits]
        usage = payload.get("costDollars")
        return ExaSearchResult(
            hits=hits,
            query=request.query,
            usage=usage if isinstance(usage, dict) else None,
            calls=1,
            mock=True,
        )


if TYPE_CHECKING:
    _conformance_check: ExaProvider = FixtureExaProvider()
