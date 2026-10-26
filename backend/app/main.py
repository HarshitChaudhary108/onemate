"""FastAPI application factory.

Wires the research router, an explicit CORS allowlist, per-request
correlation IDs, safe error boundaries, and provider lifecycle (adapters are
created in the lifespan and their clients closed on shutdown).

Error contract (tech section 4.3 / spec section 8):
- 422 validation errors -> structured safe body, no input echo.
- ``ResearchUnavailable`` -> 503 ``exa_not_configured``, otherwise 502.
- Any other exception -> generic 500. The handler for bare ``Exception``
  runs OUTSIDE user middleware (Starlette's ServerErrorMiddleware), so it
  sets ``X-Request-ID`` / ``Cache-Control: no-store`` itself and logs the
  traceback server-side only.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.research import build_providers, router
from app.api.sheets import router as sheets_router
from app.config import Settings, get_settings
from app.engine.events import HubRegistry
from app.engine.runner import SheetRunner
from app.errors.llm_error import LlmError
from app.errors.provider_error import ResearchUnavailable
from app.errors.sheet_error import SheetError
from app.schemas.report import ErrorBody, ErrorDetail, ErrorResponse
from app.sheets import repo as sheets_repo
from app.sheets.db import init_db
from app.sheets.service import SheetService

logger = logging.getLogger("outmate.api")


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) and rid else uuid.uuid4().hex


def _validation_detail(error: dict[str, Any]) -> ErrorDetail:
    loc = [str(part) for part in error.get("loc", ()) if part != "body"]
    field = ".".join(loc) if loc else "body"
    msg = str(error.get("msg", "invalid value"))
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, ") :]
    return ErrorDetail(field=field, msg=msg[:200])


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    origins = settings.cors_origins_list
    if not origins or "*" in origins:
        raise ValueError(
            "CORS_ORIGINS must be an explicit comma-separated allowlist "
            "(e.g. http://localhost:3000); wildcard '*' is not allowed."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Sheets store first: initialize the schema, then requeue cells
        # left "running" by a crash (recovery pairs with runner.aclose).
        await init_db(settings.sheets_db_path)
        await sheets_repo.requeue_orphans(settings.sheets_db_path)
        providers = build_providers(settings)
        app.state.providers = providers
        app.state.settings = settings
        hubs = HubRegistry()
        app.state.hubs = hubs
        app.state.sheet_service = SheetService(
            settings.sheets_db_path,
            max_rows=settings.sheet_max_rows,
            max_columns=settings.sheet_max_columns,
        )
        runner: SheetRunner | None = None
        if providers.exa is not None:
            runner = SheetRunner(
                settings.sheets_db_path,
                providers.exa,
                hubs=hubs,
                max_concurrency=settings.sheet_max_concurrency,
                qps=float(settings.exa_max_qps),
                default_cap=settings.run_cost_cap_usd,
            )
        app.state.runner = runner
        try:
            yield
        finally:
            # Read from app.state so a substituted runner (tests) is closed too.
            current: SheetRunner | None = app.state.runner
            if current is not None:
                await current.aclose()
            for provider in (providers.exa, providers.moltsets):
                aclose = getattr(provider, "aclose", None)
                if aclose is not None:
                    await aclose()

    app = FastAPI(title="outmate-backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(
                code="validation_error",
                message="The request failed validation.",
                request_id=_request_id(request),
                details=[_validation_detail(e) for e in exc.errors()],
            )
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(ResearchUnavailable)
    async def research_unavailable_handler(
        request: Request, exc: ResearchUnavailable
    ) -> JSONResponse:
        status_code = 503 if exc.code == "exa_not_configured" else 502
        body = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                request_id=_request_id(request),
            )
        )
        return JSONResponse(status_code=status_code, content=body.model_dump())

    _SHEET_ERROR_STATUS = {
        "not_found": 404,
        "name_conflict": 409,
        "column_referenced": 409,
        "has_dependents": 409,
        "confirm_required": 409,
        "run_active": 409,
    }

    @app.exception_handler(SheetError)
    async def sheet_error_handler(request: Request, exc: SheetError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                request_id=_request_id(request),
            )
        )
        return JSONResponse(
            status_code=_SHEET_ERROR_STATUS.get(exc.code, 422),
            content=body.model_dump(),
        )

    @app.exception_handler(LlmError)
    async def llm_error_handler(request: Request, exc: LlmError) -> JSONResponse:
        status_code = 503 if exc.code == "groq_not_configured" else 502
        body = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                message=exc.message,
                request_id=_request_id(request),
            )
        )
        return JSONResponse(status_code=status_code, content=body.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception("unhandled error request_id=%s", request_id)
        body = ErrorResponse(
            error=ErrorBody(
                code="internal_error",
                message="An unexpected error occurred.",
                request_id=request_id,
            )
        )
        # This handler runs outside user middleware: set both headers here.
        return JSONResponse(
            status_code=500,
            content=body.model_dump(),
            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(sheets_router)
    return app


app = create_app()
