"""Deterministic Groq mock (``GroqMockProvider``).

Used only in tests or when ``GROQ_MODE=mock`` is explicitly configured.
Every result carries ``mock=True``. The response content is synthesized
from the request's ``json_schema`` — ``const`` and ``enum`` hints are
honored so the placeholder stays schema-adherent — and never performs
any I/O or fabricates real data.
"""

from typing import TYPE_CHECKING, Any

from app.providers.types import LlmProvider, LlmRequest, LlmResult


def _placeholder(schema: Any) -> Any:
    """Deterministic placeholder value for one JSON-schema node."""
    if not isinstance(schema, dict):
        return "synthetic mock value"
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required = schema.get("required")
        if isinstance(required, list):
            names = [name for name in required if name in properties]
        else:
            names = list(properties)
        return {name: _placeholder(properties[name]) for name in names}
    if schema_type == "array":
        return []
    if schema_type == "boolean":
        return True
    if schema_type in ("integer", "number"):
        return 0
    return "synthetic mock value"


class GroqMockProvider:
    """Deterministic in-memory LlmProvider. Never performs I/O."""

    async def complete(self, request: LlmRequest) -> LlmResult:
        return LlmResult(
            content=_placeholder(request.json_schema),
            calls=1,
            mock=True,
        )


if TYPE_CHECKING:
    _conformance_check: LlmProvider = GroqMockProvider()
