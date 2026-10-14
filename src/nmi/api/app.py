"""FastAPI application factory.

The app is thin on purpose: routers translate HTTP to calls on
``nmi.api.service``, and the gateway middleware owns auth, limiting, caching
and audit. That split means the query layer can be tested without a client and
the gateway can be tested with a two-line route.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nmi.api.cache import ResponseCache
from nmi.api.middleware import GatewayMiddleware
from nmi.api.ratelimit import RateLimiter
from nmi.api.routers import (
    alerts,
    audit,
    backtests,
    health,
    market,
    recommendations,
    screener,
    stocks,
)
from nmi.core.config import settings

_DESCRIPTION = """
Read-only decision-support API for the Nifty Market Intelligence platform.

Every view is **as-of**: membership, scores, signals and recommendations are all
resolved for the requested date, never "now", so a historical question gets the
historical answer. Responses are typed, paginated, cached (``X-Cache``) and
rate-limited (``X-RateLimit-*``); every request is written to the audit log.
"""


def create_app(
    *,
    cache: ResponseCache | None = None,
    limiter: RateLimiter | None = None,
) -> FastAPI:
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=_DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        GatewayMiddleware,
        cache=cache or ResponseCache(),
        limiter=limiter or RateLimiter(),
    )

    app.include_router(health.router)
    for module in (
        market,
        screener,
        stocks,
        recommendations,
        alerts,
        backtests,
        audit,
    ):
        app.include_router(module.router, prefix=settings.api_prefix)

    @app.get("/", tags=["health"])
    def root() -> dict:
        return {
            "name": settings.api_title,
            "version": settings.api_version,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "path": request.url.path},
        )

    return app


app = create_app()
