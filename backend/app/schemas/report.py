"""Report contract for ``POST /api/research`` responses (spec section 7).

Every user-facing factual field uses the ``Field[T]`` wrapper: value + status +
sources. ``mock`` is provenance metadata on sources/providers, never a status.
Datetimes serialize as ISO-8601 strings via Pydantic's JSON mode.
"""

from datetime import datetime
from typing import Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.research import ResearchRequest
from app.utils.safe_url import safe_url

Status = Literal["verified", "provider-reported", "inferred", "unavailable"]
ProviderName = Literal["Exa", "MoltSets"]
ProviderState = Literal[
    "ok",
    "partial",
    "error",
    "not_configured",
    "mock",
    "disabled",
    "not_found",
    "quota_exhausted",
]
DataMode = Literal["live", "mock", "mixed"]

EMAIL_RISK_NOTE: Literal["deliverability signal, not identity proof"] = (
    "deliverability signal, not identity proof"
)


class Source(BaseModel):
    """Provenance for one claim. ``provider``, ``mock``, and ``retrieved_at``
    are always present; the rest are optional because providers may omit them.
    A MoltSets source legitimately has no ``url``."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    mock: bool
    retrieved_at: datetime
    url: str | None = None
    title: str | None = None
    published_date: str | None = None
    highlight: str | None = None
    query: str | None = None

    @field_validator("url", mode="before")
    @classmethod
    def _sanitize_url(cls, value: object) -> object:
        # Unsafe URLs are dropped to None, never a validation error.
        if isinstance(value, str) or value is None:
            return safe_url(value)
        return None


def _verified_needs_url_source(status: Status, sources: list[Source]) -> None:
    """A "verified" claim must cite at least one source with a real URL."""
    if status == "verified" and not any(s.url is not None for s in sources):
        raise ValueError('"verified" requires at least one source with a URL')


T = TypeVar("T")


class Field(BaseModel, Generic[T]):
    """Value + status + sources wrapper used by every factual report field."""

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    status: Status
    sources: list[Source] = []
    note: str | None = None

    @model_validator(mode="after")
    def _check_status_consistency(self) -> Self:
        has_value = self.value is not None and not (
            isinstance(self.value, list) and len(self.value) == 0
        )
        if not has_value and self.status != "unavailable":
            raise ValueError('an empty value must have status "unavailable"')
        _verified_needs_url_source(self.status, self.sources)
        return self


class GrowthSignal(BaseModel):
    """One dated, individually sourced growth event."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["hiring", "funding", "expansion", "leadership_change", "product_launch", "other"]
    summary: str
    date: str | None = None
    sources: list[Source] = []


class DecisionMaker(BaseModel):
    """One named person relevant to the account."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None
    seniority: str | None = None
    profile_url: str | None = None
    status: Status
    sources: list[Source] = []
    mock: bool = False

    @field_validator("profile_url", mode="before")
    @classmethod
    def _sanitize_profile_url(cls, value: object) -> object:
        if isinstance(value, str) or value is None:
            return safe_url(value)
        return None

    @model_validator(mode="after")
    def _check_status_consistency(self) -> Self:
        _verified_needs_url_source(self.status, self.sources)
        return self


class ContactInfo(BaseModel):
    """Provider-returned business contact detail. The email risk grade is a
    deliverability signal only — never identity proof."""

    model_config = ConfigDict(extra="forbid")

    person_name: str
    business_email: str | None = None
    email_risk_grade: str | None = None
    email_last_validated: str | None = None
    email_risk_note: Literal["deliverability signal, not identity proof"] = EMAIL_RISK_NOTE
    status: Status
    sources: list[Source] = []
    mock: bool = False

    @model_validator(mode="after")
    def _check_status_consistency(self) -> Self:
        _verified_needs_url_source(self.status, self.sources)
        return self


class RationalePoint(BaseModel):
    """One point of a GTM hypothesis, tied to the evidence that supports it."""

    model_config = ConfigDict(extra="forbid")

    point: str
    sources: list[Source] = []


class GtmHypothesis(BaseModel):
    """Cautious, evidence-tied opportunity hypothesis (status is ``inferred``)."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    rationale: list[RationalePoint]
    caveat: str


class CompanyIdentity(BaseModel):
    """Resolution result for the submitted name/domain."""

    model_config = ConfigDict(extra="forbid")

    resolved: bool
    company_name: Field[str]
    domain: Field[str]
    note: str | None = None


class ProviderSummary(BaseModel):
    """Per-provider outcome for this report run."""

    model_config = ConfigDict(extra="forbid")

    state: ProviderState
    mock: bool
    calls: int
    usage: dict[str, Any] | None = None
    note: str | None = None


class Providers(BaseModel):
    """Provider summary block. Keys are fixed: exactly exa and moltsets."""

    model_config = ConfigDict(extra="forbid")

    exa: ProviderSummary
    moltsets: ProviderSummary


class ReportError(BaseModel):
    """One structured, display-safe error."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["Exa", "MoltSets", "Application"]
    stage: Literal[
        "identity",
        "overview",
        "growth",
        "leadership",
        "objective",
        "enrichment",
        "normalization",
        "api",
    ]
    code: str
    message: str
    retryable: bool
    request_id: str | None = None


class Report(BaseModel):
    """The versioned report returned by ``POST /api/research``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    data_mode: DataMode
    request: ResearchRequest
    company_identity: CompanyIdentity
    description: Field[str]
    industry: Field[str]
    target_market: Field[str]
    employee_size: Field[str]
    growth_signals: Field[list[GrowthSignal]]
    decision_makers: Field[list[DecisionMaker]]
    contact_information: Field[list[ContactInfo]]
    potential_gtm_opportunity: Field[GtmHypothesis]
    overall_status: Status
    missing_fields: list[str]
    errors: list[ReportError]
    warnings: list[str]
    providers: Providers
    request_id: str
    generated_at: datetime


class ErrorDetail(BaseModel):
    """One field-level detail inside a validation error body."""

    model_config = ConfigDict(extra="forbid")

    field: str
    msg: str


class ErrorBody(BaseModel):
    """The ``error`` object of a non-200 response."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    """Structured body for non-200 responses (spec section 8)."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
