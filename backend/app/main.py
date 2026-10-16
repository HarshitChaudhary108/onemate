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
from app.config import Settings, get_settings
from app.errors.provider_error import ResearchUnavailable
from app.schemas.report import ErrorBody, ErrorDetail, ErrorResponse

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
        providers = build_providers(settings)
        app.state.providers = providers
        try:
            yield
        finally:
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
    return app


app = create_app()
