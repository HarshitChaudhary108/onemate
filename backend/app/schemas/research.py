"""Request contract for ``POST /api/research`` (spec section 8 / tech section 4)."""

import unicodedata
from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from app.normalize.domain import DomainError, normalize_domain

_COMPANY_NAME_MAX = 200
_OBJECTIVE_MAX = 1000


def _has_control_chars(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


class ResearchRequest(BaseModel):
    """Validated research request. At least one identifier is required.

    ``domain`` stores the trimmed string the user typed (host or URL); the
    normalized host is derived later via ``normalize_domain``.
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    company_name: str | None = None
    domain: str | None = None
    research_objective: str | None = None

    @field_validator("company_name", "research_objective", mode="after")
    @classmethod
    def _clean_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        limit = _COMPANY_NAME_MAX if info.field_name == "company_name" else _OBJECTIVE_MAX
        if len(value) > limit:
            raise ValueError(f"must be at most {limit} characters")
        return value or None

    @field_validator("domain", mode="after")
    @classmethod
    def _clean_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        if not value:
            return None
        try:
            normalize_domain(value)
        except DomainError as exc:
            raise ValueError(f"invalid domain: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _require_identifier(self) -> Self:
        if self.company_name is None and self.domain is None:
            raise ValueError("at least one of company_name or domain is required")
        return self
