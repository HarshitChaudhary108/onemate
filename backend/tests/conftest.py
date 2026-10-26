"""Shared test helpers: real file-DB fixture and in-memory fake providers.

No fixture files are read anywhere in this suite — the fixture-backed
mock providers are broken (``backend/tests/fixtures`` was never
committed). Fakes are in-memory, implement the provider protocols
directly, and every payload is hand-written from the documented shapes.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest_asyncio

from app.providers.types import (
    ExaCitation,
    ExaGroundingEntry,
    ExaHit,
    ExaOutput,
    ExaSearchRequest,
    ExaSearchResult,
)
from app.sheets.db import init_db
from app.sheets.models import Column, Row

SYNTHETIC_COST = {"total": 0.007, "search": {"neural": 0.007}}


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> str:
    path = str(tmp_path / "sheets.db")
    await init_db(path)
    return path


def utcnow() -> datetime:
    return datetime.now(UTC)


Handler = Callable[[ExaSearchRequest], Awaitable[ExaSearchResult]]


class FakeExa:
    """In-memory ``ExaProvider``: records requests, delegates to a handler."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.requests: list[ExaSearchRequest] = []

    async def search(self, request: ExaSearchRequest) -> ExaSearchResult:
        self.requests.append(request)
        return await self._handler(request)

    def cell_requests(self) -> list[ExaSearchRequest]:
        """Requests carrying an outputSchema (cell searches, not identity)."""
        return [r for r in self.requests if r.output_schema is not None]


def make_hit(url: str | None, title: str | None = None) -> ExaHit:
    return ExaHit(url=url, title=title)


def identity_result(url: str, title: str) -> ExaSearchResult:
    """One identity-search hit on the given URL (resolves the row)."""
    return ExaSearchResult(
        hits=[make_hit(url, title)],
        query="identity",
        usage=dict(SYNTHETIC_COST),
        calls=1,
    )


def empty_result() -> ExaSearchResult:
    return ExaSearchResult(hits=[], query="identity", calls=1)


def structured_result(
    *,
    found: bool = True,
    value: Any = "synthetic answer",
    confidence: Literal["low", "medium", "high"] = "high",
    grounding_field: str | None = "value",
    citations: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    content: Any = "use-default",
) -> ExaSearchResult:
    """Synthetic structured result from the documented ``output`` shape."""
    if content == "use-default":
        content = {"found": found, "value": value}
    grounding: list[ExaGroundingEntry] = []
    if grounding_field is not None:
        raw_citations = (
            citations
            if citations is not None
            else [{"url": "https://evidence.test/claim", "title": "Synthetic evidence"}]
        )
        grounding = [
            ExaGroundingEntry(
                field=grounding_field,
                citations=[ExaCitation.model_validate(c) for c in raw_citations],
                confidence=confidence,
            )
        ]
    return ExaSearchResult(
        hits=[],
        query="cell",
        usage=SYNTHETIC_COST if usage is None else usage,
        output=ExaOutput(content=content, grounding=grounding),
        calls=1,
        mock=True,
    )


def make_column(**overrides: Any) -> Column:
    base: dict[str, Any] = {
        "id": "col1",
        "sheet_id": "sheet1",
        "name": "Col",
        "prompt_template": "Describe {{Company}}",
        "output_type": "text",
        "source_scope": "web",
        "position": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    base.update(overrides)
    return Column(**base)


def make_row(**overrides: Any) -> Row:
    base: dict[str, Any] = {
        "id": "row1",
        "sheet_id": "sheet1",
        "input_name": "Acme Corp",
        "input_domain": "acme.test",
        "position": 0,
        "identity_status": "resolved",
        "resolved_name": "Acme Corp",
        "resolved_domain": "acme.test",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    base.update(overrides)
    return Row(**base)
