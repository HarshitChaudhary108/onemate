"""Deterministic MoltSets fixture adapter (``FixtureMoltSetsProvider``).

Used only in tests or when ``MOLTSETS_MODE=mock`` is explicitly configured.
Every result carries ``mock=True``; fixture people are named "Sample …" with
emails only on ``*.test`` domains, so they can never be mistaken for real
contacts.

Scenario selection is deterministic, driven by the domain:

- ``moltsets-error.test`` -> raises a retryable ``moltsets_server_error``
- ``no-evidence.test`` / anything containing ``ambiguous`` -> clean
  ``not_found`` (company) / empty list (people)
- anything else -> the normal synthetic fixture data

Company results are cached per-process like the live adapter; people results
are never cached.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.errors.provider_error import ProviderError
from app.providers.types import (
    MoltSetsCompany,
    MoltSetsCompanyResult,
    MoltSetsPeopleResult,
    MoltSetsPerson,
    MoltSetsProvider,
)

_DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

_ERROR_DOMAIN = "moltsets-error.test"
_MISS_MARKERS = ("no-evidence.test", "ambiguous")


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results")
    if isinstance(results, dict):
        items = results.get("results")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


class FixtureMoltSetsProvider:
    """Fixture-backed MoltSets provider. Never performs network I/O."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir or _DEFAULT_FIXTURE_DIR
        self._company_cache: dict[str, MoltSetsCompanyResult] = {}

    async def search_company(self, domain: str) -> MoltSetsCompanyResult:
        key = domain.strip().lower()
        cached = self._company_cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"calls": 0})

        if _ERROR_DOMAIN in key:
            raise ProviderError(
                provider="MoltSets",
                code="moltsets_server_error",
                message="MoltSets returned a server error; try again later.",
                retryable=True,
                stage="enrichment",
            )

        if any(marker in key for marker in _MISS_MARKERS):
            result = MoltSetsCompanyResult(found=False, calls=1, mock=True)
        else:
            payload = self._load("moltsets_company.json")
            items = _items(payload)
            company = MoltSetsCompany.model_validate(items[0]) if items else None
            result = MoltSetsCompanyResult(
                found=company is not None,
                company=company,
                calls=1,
                usage=payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else None,
                mock=True,
            )

        self._company_cache[key] = result
        return result

    async def search_people(
        self,
        company_domain: str,
        seniority: list[str],
        limit: int = 5,
    ) -> MoltSetsPeopleResult:
        key = company_domain.strip().lower()
        if _ERROR_DOMAIN in key:
            raise ProviderError(
                provider="MoltSets",
                code="moltsets_server_error",
                message="MoltSets returned a server error; try again later.",
                retryable=True,
                stage="enrichment",
            )
        capped = max(1, min(limit, 5))
        if any(marker in key for marker in _MISS_MARKERS):
            return MoltSetsPeopleResult(people=[], calls=1, mock=True)

        payload = self._load("moltsets_people.json")
        people = [MoltSetsPerson.model_validate(item) for item in _items(payload)][:capped]
        usage = payload.get("metadata")
        return MoltSetsPeopleResult(
            people=people,
            calls=1,
            usage=usage if isinstance(usage, dict) else None,
            mock=True,
        )

    def _load(self, name: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self._fixture_dir / name).read_text())
        return data


if TYPE_CHECKING:
    _conformance_check: MoltSetsProvider = FixtureMoltSetsProvider()
