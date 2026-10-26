"""P9a — list-building rows via ONE Exa ``category="company"`` search.

Generated rows are identical to pasted rows: ``input_domain`` only (never
a name guessed from hit titles), ``identity_status="pending"``, resolved
inside the next user-chosen run. Nothing auto-runs — generation creates
idle cells only and no ``runs`` row.

In-memory fake providers only — the fixture-backed mocks are broken (the
fixture files were never committed). No network anywhere.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from conftest import FakeExa, identity_result, make_hit, structured_result, utcnow
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.engine.budget import ESTIMATED_SEARCH_COST_USD
from app.engine.limiter import RateLimiter
from app.engine.runner import SheetRunner
from app.errors.provider_error import ProviderError
from app.errors.sheet_error import SheetError
from app.main import create_app
from app.normalize.domain import normalize_domain
from app.providers.exa_mock import FixtureExaProvider
from app.providers.types import ExaSearchRequest, ExaSearchResult
from app.research.rows_query import generate_rows_request
from app.sheets import repo
from app.sheets.models import RowInput
from app.sheets.service import SheetService

Handler = Callable[[ExaSearchRequest], Awaitable[ExaSearchResult]]

USAGE = {"total": 0.011, "search": {"neural": 0.011}}


@pytest_asyncio.fixture
async def service(db: str) -> SheetService:
    return SheetService(db)


def company_handler(
    *urls: str | None,
    usage: dict[str, Any] | None = None,
    mock: bool = True,
) -> Handler:
    """A fake company search returning one hit per URL (usage default: the
    documented upper bound — tests override for cost assertions)."""

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        return ExaSearchResult(
            hits=[make_hit(url, f"Synthetic hit {url}") for url in urls],
            query=request.query,
            usage=USAGE if usage is None else usage,
            calls=1,
            mock=mock,
        )

    return handler


async def _column(service: SheetService, sheet_id: str, name: str = "Col") -> None:
    await service.add_column(
        sheet_id,
        name=name,
        prompt_template="describe {{Company}}",
        output_type="text",
        source_scope="web",
    )


# -- request builder -----------------------------------------------------------


def test_generate_rows_request_is_open_company_search() -> None:
    request = generate_rows_request("robotics suppliers in Germany", 3)
    assert request.query == "robotics suppliers in Germany"
    assert request.num_results == 3
    assert request.category == "company"
    assert request.search_type == "auto"
    # Open-web list-building: no domain scoping, no date filters (the
    # company category 400s on them), no structured output.
    assert request.include_domains is None
    assert request.start_published_date is None
    assert request.end_published_date is None
    assert request.output_schema is None
    assert request.system_prompt is None


def test_generate_rows_request_stays_under_the_shared_cap() -> None:
    with pytest.raises(ValidationError):
        generate_rows_request("q", 6)  # num_results is hard-capped at 5


# -- service: happy path ---------------------------------------------------------


async def test_generate_rows_creates_pending_rows_like_pasted(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    await _column(service, sheet.id)
    exa = FakeExa(
        company_handler(
            "https://acme-robotics.test/",
            "https://www.globex.test/about",  # host extracted, path dropped
        )
    )

    outcome = await service.generate_rows(exa, sheet.id, "robotics suppliers", 5)

    assert len(outcome.rows) == 2
    assert [row.input_domain for row in outcome.rows] == [
        "acme-robotics.test",
        "www.globex.test",
    ]
    # Identical to pasted rows: no name guessed from hit titles, pending.
    assert all(row.input_name is None for row in outcome.rows)
    assert all(row.identity_status == "pending" for row in outcome.rows)
    assert outcome.mock is True
    assert outcome.estimated_cost_usd == pytest.approx(ESTIMATED_SEARCH_COST_USD)
    assert outcome.cost_usd == pytest.approx(0.011)

    request = exa.requests[0]
    assert request.category == "company" and request.include_domains is None
    assert request.output_schema is None  # raw hits, not synthesized output


async def test_generate_rows_dedupes_against_existing_and_batch(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    await service.import_rows(sheet.id, [RowInput(input_domain="acme.test")])
    exa = FakeExa(
        company_handler(
            "https://www.acme.test/",  # dup of existing (compare_key)
            "https://globex.test/",
            "https://globex.test/team",  # dup within the batch
        )
    )

    outcome = await service.generate_rows(exa, sheet.id, "q", 5)

    assert [row.input_domain for row in outcome.rows] == ["globex.test"]
    assert outcome.skipped == 2  # two hits did not become rows
    assert len(await repo.list_rows(db, sheet.id)) == 2


async def test_generate_rows_skips_missing_and_invalid_urls(db: str, service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    exa = FakeExa(
        company_handler(
            "https://acme.test/",
            None,
            "not a url",
            "https://localhost:8080/x",  # explicit port — not a domain
        )
    )

    outcome = await service.generate_rows(exa, sheet.id, "q", 5)

    assert len(outcome.rows) == 1
    assert outcome.rows[0].input_domain == "acme.test"
    assert outcome.skipped == 3


async def test_generate_rows_empty_hits_creates_nothing(db: str, service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    exa = FakeExa(company_handler())

    outcome = await service.generate_rows(exa, sheet.id, "q", 5)

    assert outcome.rows == []
    assert outcome.skipped == 0
    assert await repo.list_rows(db, sheet.id) == []


async def test_generate_rows_truncates_at_the_sheet_cap(db: str) -> None:
    service = SheetService(db, max_rows=3)
    sheet = await service.create_sheet("s")
    await service.import_rows(sheet.id, [RowInput(input_domain="acme.test")])
    exa = FakeExa(company_handler(*[f"https://co{i}.test/" for i in range(5)]))

    outcome = await service.generate_rows(exa, sheet.id, "q", 5)

    assert len(outcome.rows) == 2  # capacity 3 - 1 existing
    assert outcome.skipped == 3
    assert len(await repo.list_rows(db, sheet.id)) == 3


# -- cost / cap ------------------------------------------------------------------


async def test_generate_rows_cap_blocks_before_any_provider_call(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    exa = FakeExa(company_handler("https://acme.test/"))

    with pytest.raises(SheetError) as exc:
        await service.generate_rows(
            exa,
            sheet.id,
            "q",
            5,
            cap=ESTIMATED_SEARCH_COST_USD - 0.001,
        )
    assert exc.value.code == "cap_exceeded"
    assert exa.requests == []  # never spend past a cap — not even one call


async def test_generate_rows_cap_equal_to_estimate_is_allowed(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    exa = FakeExa(company_handler("https://acme.test/"))
    outcome = await service.generate_rows(exa, sheet.id, "q", 5, cap=ESTIMATED_SEARCH_COST_USD)
    assert len(outcome.rows) == 1


async def test_generate_rows_cost_is_provider_reported_only(db: str, service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    exa = FakeExa(company_handler("https://acme.test/", usage={"total": 0.007}))
    outcome = await service.generate_rows(exa, sheet.id, "q", 5)
    assert outcome.cost_usd == pytest.approx(0.007)

    exa_no_usage = FakeExa(company_handler("https://globex.test/", usage={}))
    sheet2 = await service.create_sheet("s2")
    outcome2 = await service.generate_rows(exa_no_usage, sheet2.id, "q", 5)
    assert outcome2.cost_usd is None  # absent usage -> null, never invented


async def test_generate_rows_not_found(db: str, service: SheetService) -> None:
    exa = FakeExa(company_handler("https://acme.test/"))
    with pytest.raises(SheetError) as exc:
        await service.generate_rows(exa, "nope", "q", 5)
    assert exc.value.code == "not_found"
    assert exa.requests == []


# -- no auto-run / runner integration ---------------------------------------------


async def test_generate_rows_creates_idle_cells_and_never_runs(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    await _column(service, sheet.id, "A")
    await _column(service, sheet.id, "B")
    exa = FakeExa(company_handler("https://acme.test/", "https://globex.test/"))

    outcome = await service.generate_rows(exa, sheet.id, "q", 5)

    snap = await repo.snapshot(db, sheet.id)
    assert snap is not None
    new_cells = [c for c in snap.cells if c.row_id in {r.id for r in outcome.rows}]
    assert len(new_cells) == 4  # 2 rows x 2 columns
    assert all(cell.status == "idle" for cell in new_cells)
    # One search, and it was the company search — zero cell searches.
    assert len(exa.requests) == 1
    assert exa.cell_requests() == []
    # No runs row exists — generation is not a run.
    assert snap.latest_run is None


async def test_generated_row_resolves_in_a_later_run_like_a_pasted_row(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    await _column(service, sheet.id)
    finder = FakeExa(company_handler("https://acme.test/"))
    await service.generate_rows(finder, sheet.id, "robotics suppliers", 5)

    async def run_handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is not None:
            return structured_result(value="synthetic answer")
        # Identity-first path, unchanged: the pending row's domain query.
        domain = (request.include_domains or ["acme.test"])[0]
        return identity_result(f"https://{domain}/", "Acme Inc")

    runner = SheetRunner(db, FakeExa(run_handler), limiter=RateLimiter(1000), clock=utcnow)
    run = await runner.run(sheet.id, "empty")

    rows = await repo.list_rows(db, sheet.id)
    assert rows[0].identity_status == "resolved"
    assert rows[0].resolved_domain == "acme.test"
    snap = await repo.snapshot(db, sheet.id)
    assert snap is not None
    assert all(cell.status == "done" for cell in snap.cells)
    assert run.spent > 0


# -- API -------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    # Keys forced None and modes pinned so the developer's .env can't leak in.
    return Settings(
        exa_api_key=None,
        exa_mode="live",
        groq_api_key=None,
        groq_mode="live",
        moltsets_api_key=None,
        moltsets_mode="off",
        sheets_db_path=str(tmp_path / "sheets.db"),
    )


@asynccontextmanager
async def _app_client(
    tmp_path: Path, handler: Handler | None
) -> AsyncIterator[tuple[FastAPI, AsyncClient, FakeExa | None]]:
    """The app inside its lifespan; ``handler=None`` leaves Exa
    unconfigured (the 503 path — never a silent mock)."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        fake: FakeExa | None = None
        if handler is not None:
            fake = FakeExa(handler)
            app.state.providers.exa = fake
            app.state.runner = SheetRunner(
                settings.sheets_db_path,
                fake,
                hubs=app.state.hubs,
                limiter=RateLimiter(1000),
                clock=utcnow,
            )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, fake


async def test_generate_endpoint_happy_path(tmp_path: Path) -> None:
    async with _app_client(
        tmp_path, company_handler("https://acme.test/", "https://acme.test/x")
    ) as (app, client, _):
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]
        resp = await client.post(
            f"/api/sheets/{sheet_id}/rows/generate",
            json={"query": "robotics suppliers", "max_results": 2},
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["created"] == 1 and payload["skipped"] == 1
        assert payload["rows"][0]["input_domain"] == "acme.test"
        assert payload["rows"][0]["identity_status"] == "pending"
        assert payload["estimated_cost_usd"] == pytest.approx(0.015)
        assert payload["cost_usd"] == pytest.approx(0.011)
        assert payload["mock"] is True


async def test_generate_endpoint_503_when_exa_unconfigured(tmp_path: Path) -> None:
    async with _app_client(tmp_path, None) as (app, client, _):
        assert app.state.providers.exa is None
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]
        resp = await client.post(
            f"/api/sheets/{sheet_id}/rows/generate", json={"query": "anything"}
        )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "exa_not_configured"


async def test_generate_endpoint_provider_error_surfaces_its_code(
    tmp_path: Path,
) -> None:
    async def failing(request: ExaSearchRequest) -> ExaSearchResult:
        raise ProviderError(
            provider="Exa",
            code="exa_credits_exhausted",
            message="synthetic credits exhausted",
            retryable=False,
            http_status=402,
        )

    async with _app_client(tmp_path, failing) as (app, client, _):
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]
        resp = await client.post(
            f"/api/sheets/{sheet_id}/rows/generate", json={"query": "anything"}
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "exa_credits_exhausted"


async def test_generate_endpoint_validation_and_404(tmp_path: Path) -> None:
    async with _app_client(tmp_path, company_handler("https://acme.test/")) as (app, client, _):
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]

        empty = await client.post(f"/api/sheets/{sheet_id}/rows/generate", json={"query": "  "})
        assert empty.status_code == 422

        too_many = await client.post(
            f"/api/sheets/{sheet_id}/rows/generate",
            json={"query": "q", "max_results": 6},
        )
        assert too_many.status_code == 422  # the ≤5 shared cap

        under_cap = await client.post(
            f"/api/sheets/{sheet_id}/rows/generate",
            json={"query": "q", "cap": 0.001},
        )
        assert under_cap.status_code == 422
        assert under_cap.json()["error"]["code"] == "cap_exceeded"

        missing = await client.post("/api/sheets/nope/rows/generate", json={"query": "q"})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


# -- EXA_MODE=mock branch ---------------------------------------------------------


async def test_exa_mock_company_search_is_synthetic_and_reads_no_fixture() -> None:
    """The company-search branch synthesizes hits on reserved .test hosts —
    deterministic per query and never touching the missing fixture files."""
    provider = FixtureExaProvider()  # default fixture dir is absent
    request = ExaSearchRequest(
        query="robotics suppliers in Germany", category="company", num_results=3
    )
    result = await provider.search(request)
    assert result.mock is True
    assert len(result.hits) == 3
    for hit in result.hits:
        assert hit.url is not None
        assert hit.url.endswith(".mock-companies.test/")
        # Every synthetic hit normalizes into a real row candidate.
        normalize_domain(hit.url)

    # Deterministic: the same query yields the same companies.
    again = await provider.search(request)
    assert [h.url for h in again.hits] == [h.url for h in result.hits]


async def test_exa_mock_identity_search_keeps_the_fixture_path() -> None:
    """Identity searches set ``include_domains`` — they do NOT take the new
    synthetic branch (they still read the absent fixture files, the known
    broken-mock behavior)."""
    provider = FixtureExaProvider()
    request = ExaSearchRequest(
        query="acme company", category="company", include_domains=["acme.test"]
    )
    with pytest.raises(OSError):
        await provider.search(request)
