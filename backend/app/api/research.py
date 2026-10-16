"""Research API route and provider factory.

``build_providers`` maps settings to provider implementations; mock mode is
explicit opt-in via ``*_MODE=mock``. ``live`` mode with a missing/blank key
yields ``None`` and the orchestrator reports ``not_configured`` — never a
silent mock fallback (spec section 6 / plan section 5.2).

``POST /api/research`` validates the request with Pydantic, injects
``ResearchDeps`` (tests substitute it via ``app.dependency_overrides``), and
returns the validated ``Report``. Provider keys never leave this layer.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.config import Settings
from app.providers.exa import HttpExaProvider
from app.providers.exa_mock import FixtureExaProvider
from app.providers.moltsets import HttpMoltSetsProvider
from app.providers.moltsets_mock import FixtureMoltSetsProvider
from app.providers.types import ExaProvider, MoltSetsProvider
from app.research.orchestrator import ResearchDeps, run_research
from app.schemas.report import Report
from app.schemas.research import ResearchRequest


@dataclass
class Providers:
    """Provider bundle handed to the orchestrator via ``app.state``."""

    exa: ExaProvider | None
    moltsets: MoltSetsProvider | None
    moltsets_mode: str
    exa_mode: str


def build_providers(settings: Settings) -> Providers:
    exa: ExaProvider | None = None
    if settings.exa_mode == "mock":
        exa = FixtureExaProvider()
    else:
        exa_key = settings.exa_api_key
        if exa_key is not None and exa_key.get_secret_value().strip():
            exa = HttpExaProvider(api_key=exa_key)

    moltsets: MoltSetsProvider | None = None
    if settings.moltsets_mode == "mock":
        moltsets = FixtureMoltSetsProvider()
    elif settings.moltsets_mode == "live":
        ms_key = settings.moltsets_api_key
        if ms_key is not None and ms_key.get_secret_value().strip():
            moltsets = HttpMoltSetsProvider(api_key=ms_key, user_agent=settings.moltsets_user_agent)

    return Providers(
        exa=exa,
        moltsets=moltsets,
        moltsets_mode=settings.moltsets_mode,
        exa_mode=settings.exa_mode,
    )


def get_research_deps(request: Request) -> ResearchDeps:
    """Build ``ResearchDeps`` from the providers created in the app lifespan.

    The request ID set by middleware is reused so report and error bodies
    share one correlation ID.
    """
    providers: Providers = request.app.state.providers
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    return ResearchDeps(
        exa=providers.exa,
        moltsets=providers.moltsets,
        moltsets_mode=providers.moltsets_mode,
        exa_mode=providers.exa_mode,
        clock=lambda: datetime.now(UTC),
        request_id=request_id,
    )


router = APIRouter()


@router.post("/api/research")
async def research(
    request: ResearchRequest,
    deps: Annotated[ResearchDeps, Depends(get_research_deps)],
) -> Report:
    return await run_research(request, deps)
