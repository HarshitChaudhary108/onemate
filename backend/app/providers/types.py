"""Provider adapter boundary types (tech section 6.1).

Typed request/result models keep raw provider payloads inside the adapter
modules. Result models ignore unknown provider fields (``extra="ignore"``)
and treat every provider-supplied field as optional — missing fields are a
normal outcome, not an error.

``calls`` on every result counts actual HTTP attempts, including a retry
(0 for a process-local cache hit). ``mock`` is provenance metadata.
"""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors.provider_error import ProviderError


class ExaSearchRequest(BaseModel):
    """One focused Exa search.

    Field names use snake_case internally; the adapter maps them to the
    documented camelCase request body. ``num_results`` is capped at 5 for
    this prototype. Publication-date filters are only valid with
    ``category == "news"`` — the Exa docs state that the `company` and
    `people` categories do not support them and return a 400, so we reject
    that combination locally with ``exa_invalid_request`` before any call.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1)
    num_results: int = Field(default=5, ge=1, le=5)
    category: str | None = None
    include_domains: list[str] | None = None
    start_published_date: str | None = None
    end_published_date: str | None = None

    @model_validator(mode="after")
    def _date_filters_need_news_category(self) -> "ExaSearchRequest":
        has_date_filter = bool(self.start_published_date or self.end_published_date)
        if has_date_filter and self.category != "news":
            # ProviderError (not ValueError) propagates out of model_validate
            # unchanged, so callers see the stable internal code.
            raise ProviderError(
                provider="Exa",
                code="exa_invalid_request",
                message=(
                    "Publication-date filters are only supported for news "
                    "searches; the request was not sent."
                ),
                retryable=False,
            )
        return self


class ExaHit(BaseModel):
    """One Exa result, parsed at the adapter boundary.

    ``score`` is kept for forward compatibility; the current documented
    response schema does not return it (only ``highlightScores``).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: str | None = None
    title: str | None = None
    published_date: str | None = Field(default=None, alias="publishedDate")
    highlights: list[str] = []
    score: float | None = None


class ExaSearchResult(BaseModel):
    """Normalized result of one Exa search."""

    model_config = ConfigDict(extra="forbid")

    hits: list[ExaHit]
    query: str
    usage: dict[str, Any] | None = None
    calls: int = 0
    mock: bool = False


class MoltSetsCompany(BaseModel):
    """Subset of the documented ``search_companies`` result fields we use."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    domain: str | None = None
    industry: str | None = None
    revenue_range: str | None = None
    employee_count: str | None = None
    employee_range: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    logo_url: str | None = None


class MoltSetsPerson(BaseModel):
    """Subset of the documented ``search_people`` result fields we use.

    ``business_email``, ``business_email_risk_score`` and
    ``business_email_validated_at`` are copied exactly as returned — the risk
    grade is a deliverability signal, never identity proof.
    """

    model_config = ConfigDict(extra="ignore")

    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    headline: str | None = None
    seniority: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    business_email: str | None = None
    business_email_risk_score: str | None = None
    business_email_validated_at: str | None = None


class MoltSetsCompanyResult(BaseModel):
    """Normalized company lookup. ``found=False`` is a clean miss, not an error."""

    model_config = ConfigDict(extra="forbid")

    found: bool
    company: MoltSetsCompany | None = None
    calls: int = 0
    usage: dict[str, Any] | None = None
    mock: bool = False


class MoltSetsPeopleResult(BaseModel):
    """Normalized people search. ``people`` may legitimately be empty."""

    model_config = ConfigDict(extra="forbid")

    people: list[MoltSetsPerson]
    calls: int = 0
    usage: dict[str, Any] | None = None
    mock: bool = False


class ExaProvider(Protocol):
    """One focused search per call (tech section 6.1)."""

    async def search(self, request: ExaSearchRequest) -> ExaSearchResult: ...


class MoltSetsProvider(Protocol):
    """Company lookup by confirmed domain, then people search."""

    async def search_company(self, domain: str) -> MoltSetsCompanyResult: ...

    async def search_people(
        self,
        company_domain: str,
        seniority: list[str],
        limit: int = 5,
    ) -> MoltSetsPeopleResult: ...
