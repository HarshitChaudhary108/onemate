"""Centralized Exa query builders (plan M3 task 1, tech section 6.3).

Pure functions, no I/O. At most four logical searches per report:

1. ``identity_query``   — resolve/confirm the company identity.
2. ``overview_query``   — about / products / customers / market; the optional
   research objective is folded in HERE (binding session decision), never in
   any other query.
3. ``growth_query``     — ``category="news"`` with a 365-day
   ``start_published_date`` (ISO 8601, per docs/provider-notes.md).
4. ``leadership_query`` — ``category="people"``, never combined with a date
   filter (Exa rejects that combination with a 400).

Provider-notes verification (2026-10-16): ``includeDomains`` is NOT listed as
unsupported for the ``company`` or ``people`` categories, so the identity
search may combine ``category="company"`` with ``include_domains=[host]``.
Date filters are only ever set with ``category="news"``.

A blank objective contributes no text to ANY query.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.normalize.domain import NormalizedDomain
from app.providers.types import ExaSearchRequest

NUM_RESULTS = 5
GROWTH_LOOKBACK_DAYS = 365


@dataclass(frozen=True)
class SearchIdentity:
    """The resolved company identity used to scope searches 2-4.

    ``domain`` is the normalized comparison key (``www.`` stripped); ``name``
    is the resolved company name (may be derived from the host label).
    """

    name: str | None = None
    domain: str | None = None

    def terms(self) -> str:
        return " ".join(part for part in (self.name, self.domain) if part)


def identity_query(name: str | None, domain: NormalizedDomain | None) -> ExaSearchRequest:
    """Search 1 — company identity / official site.

    With a known domain the query is ``"<name or domain> company"`` and Exa is
    scoped via ``category="company"`` + ``include_domains=[compare_key]``
    (verified supported in docs/provider-notes.md). With a name only the
    category is omitted. Never carries a date filter.
    """
    if domain is not None:
        subject = name or domain.compare_key
        return ExaSearchRequest(
            query=f"{subject} company",
            num_results=NUM_RESULTS,
            category="company",
            include_domains=[domain.compare_key],
        )
    subject = name or ""
    return ExaSearchRequest(query=f"{subject} company", num_results=NUM_RESULTS)


def overview_query(identity: SearchIdentity, objective: str | None) -> ExaSearchRequest:
    """Search 2 — about / products / customers / market.

    The optional research objective's terms are appended here and only here.
    """
    parts = [identity.terms(), "overview products customers market"]
    if objective:
        parts.append(objective)
    return ExaSearchRequest(
        query=" ".join(part for part in parts if part),
        num_results=NUM_RESULTS,
    )


def growth_query(identity: SearchIdentity, now: datetime) -> ExaSearchRequest:
    """Search 3 — growth / news signals for the last 365 days.

    ``category="news"`` is required for publication-date filters; the
    ``start_published_date`` is an ISO 8601 date-time string as the Exa docs
    require.
    """
    start = (now - timedelta(days=GROWTH_LOOKBACK_DAYS)).isoformat()
    return ExaSearchRequest(
        query=f"{identity.terms()} hiring funding expansion leadership launch",
        num_results=NUM_RESULTS,
        category="news",
        start_published_date=start,
    )


def leadership_query(identity: SearchIdentity) -> ExaSearchRequest:
    """Search 4 — public leadership / decision-makers.

    Uses ``category="people"`` with no date filter (the ``people`` category
    rejects publication-date filters with a 400).
    """
    return ExaSearchRequest(
        query=f"{identity.terms()} leadership executives CEO founder",
        num_results=NUM_RESULTS,
        category="people",
    )
