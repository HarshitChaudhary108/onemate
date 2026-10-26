"""Typed provider error raised by LLM adapters (Groq).

This mirrors ``ProviderError`` but is deliberately a separate class:
``ProviderError.provider`` is frozen into the report contract as
``Literal["Exa", "MoltSets"]`` (and its frontend Zod mirror), so LLM
failures carry their own type with ``provider == "Groq"`` and never
convert to a report error.

Every ``LlmError`` carries a stable ``code`` and a static, user-safe
``message``. Messages must never contain API keys, header values, or raw
provider bodies — ``str()``/``repr()`` only ever expose the safe message.
``calls`` records how many HTTP attempts the adapter made before failing
(0 when the error was raised locally before any request).
"""

from typing import Literal

LlmProviderName = Literal["Groq"]


class LlmError(Exception):
    """A safe, structured LLM provider failure."""

    def __init__(
        self,
        *,
        provider: LlmProviderName,
        code: str,
        message: str,
        retryable: bool,
        retry_after: float | None = None,
        http_status: int | None = None,
        calls: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider: LlmProviderName = provider
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after
        self.http_status = http_status
        self.calls = calls
