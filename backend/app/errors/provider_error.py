"""Typed provider error raised by the Exa and MoltSets adapters.

Every ``ProviderError`` carries a stable ``code`` and a static, user-safe
``message``. Messages must never contain API keys, header values, raw provider
bodies, or business emails — ``str()``/``repr()`` only ever expose the safe
message. ``calls`` records how many HTTP attempts the adapter made before
failing (0 when the error was raised locally before any request).
"""

from typing import Literal, Self

from app.schemas.report import ReportError

ProviderName = Literal["Exa", "MoltSets"]
Stage = Literal[
    "identity",
    "overview",
    "growth",
    "leadership",
    "objective",
    "enrichment",
    "normalization",
    "api",
]


class ProviderError(Exception):
    """A safe, structured provider failure.

    ``stage`` defaults to ``"api"``; the caller that knows which pipeline
    stage failed should override it via :meth:`with_stage` before the error
    is converted into a report error.
    """

    def __init__(
        self,
        *,
        provider: ProviderName,
        code: str,
        message: str,
        retryable: bool,
        stage: Stage = "api",
        retry_after: float | None = None,
        http_status: int | None = None,
        calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider: ProviderName = provider
        self.stage: Stage = stage
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        self.http_status = http_status
        self.calls = calls

    def with_stage(self, stage: Stage) -> Self:
        """Tag the error with the pipeline stage that produced it."""
        self.stage = stage
        return self

    def to_report_error(self, request_id: str | None) -> ReportError:
        """Convert to the public report error shape (spec section 8)."""
        return ReportError(
            provider=self.provider,
            stage=self.stage,
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            request_id=request_id,
        )


class ResearchUnavailable(Exception):
    """Raised when the workflow cannot produce any report at all.

    ``code`` is a stable machine-readable code (``exa_not_configured`` maps to
    HTTP 503; every other code maps to 502). ``message`` must stay user-safe —
    no keys, headers, emails, or raw provider payloads.
    """

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
