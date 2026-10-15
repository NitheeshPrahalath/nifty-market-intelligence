"""The gateway middleware: authenticate, limit, cache and audit every request.

Doing all four in one middleware is not a shortcut — they share the same
per-request facts (who is calling, how long it took, whether it was served from
cache), and splitting them would mean either four DB round-trips or threading
mutable state through the stack. Keeping them together also makes the audit row
the reliable record of *everything* the gateway decided.

Phase 10 adds three concerns that also depend on those same facts and so belong
in the same pass: a Prometheus metric for every outcome, security headers on
every response, and binding the request id to the logging context so downstream
log lines correlate with the audit row.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nmi.api.auth import Principal, resolve_principal
from nmi.api.cache import ResponseCache
from nmi.api.ratelimit import RateLimiter
from nmi.core.config import settings
from nmi.core.db import get_session
from nmi.core.logging import bind_request_id, reset_request_id
from nmi.core.metrics import REGISTRY
from nmi.core.models import ApiRequest

log = logging.getLogger(__name__)

# Headers that must not survive a rebuild of the response from cached bytes.
_STRIP_HEADERS = {"content-length"}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def route_label(request: Request) -> str:
    """A bounded-cardinality label for the matched route.

    The router records the matched path template on ``scope["route"]`` once it
    has dispatched, so ``/api/v1/stocks/TCS`` becomes ``/api/v1/stocks/{symbol}``
    instead of one time series per symbol. Pre-routing requests (401/429/cache
    hits) fall back to a path with numeric segments masked.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    parts = [part if not part.isdigit() else "{id}" for part in request.url.path.split("/")]
    return "/".join(parts)


class GatewayMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cache: ResponseCache, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.cache = cache
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        try:
            return await self._dispatch(request, call_next, started)
        finally:
            reset_request_id(token)

    async def _dispatch(self, request: Request, call_next, started: float) -> Response:
        principal = resolve_principal(request.headers)
        request.state.principal = principal.label
        request.state.role = str(principal.role)
        request.state.scopes = principal.scopes
        request.state.authenticated = principal.authenticated

        metrics_request = settings.metrics_enabled and request.url.path == settings.metrics_path
        if metrics_request:
            # Operational surface: never authenticated, limited or cached, and
            # not audited (a scraper would otherwise flood the request log).
            response = await call_next(request)
            return self._finish(request, response, principal, started, cache_hit=False, audit=False)

        if not principal.authenticated:
            REGISTRY.inc_counter(
                "http_auth_failures_total",
                help="Requests rejected for a missing or invalid API key.",
                labels={"path": route_label(request)},
            )
            return self._finish(
                request, self._error(401, "invalid or missing API key"), principal, started,
                cache_hit=False,
            )

        decision = self.limiter.check(principal.label)
        if not decision.allowed:
            REGISTRY.inc_counter(
                "http_rate_limited_total",
                help="Requests rejected by the per-principal rate limit.",
                labels={"principal_role": str(principal.role)},
            )
            response = self._error(429, "rate limit exceeded")
            response.headers["Retry-After"] = str(decision.retry_after)
            return self._finish(request, response, principal, started, cache_hit=False)

        cache_key = self.cache.key(
            request.method, request.url.path, request.url.query, principal.label
        )
        cacheable = self.cache.cacheable(request.method, request.url.path)
        if cacheable:
            cached = self.cache.get(cache_key)
            if cached.hit and cached.value is not None:
                response = Response(
                    content=cached.value,
                    status_code=200,
                    media_type="application/json",
                )
                response.headers["X-Cache"] = "HIT"
                response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
                return self._finish(request, response, principal, started, cache_hit=True)

        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in _STRIP_HEADERS
        }
        rebuilt = Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
        rebuilt.headers["X-Cache"] = "MISS"
        rebuilt.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        if cacheable and response.status_code == 200:
            self.cache.put(cache_key, body)
        return self._finish(request, rebuilt, principal, started, cache_hit=False)

    def _error(self, status: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": detail})

    def _finish(
        self,
        request: Request,
        response: Response,
        principal: Principal,
        started: float,
        *,
        cache_hit: bool,
        audit: bool = True,
    ) -> Response:
        response.headers["X-Request-Id"] = getattr(request.state, "request_id", "")
        if settings.security_headers_enabled:
            for header, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(header, value)
            if request.url.scheme == "https":
                response.headers.setdefault(
                    "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                )
        elapsed = time.perf_counter() - started
        self._record_metrics(request, response.status_code, cache_hit, elapsed)
        if audit:
            self._audit(request, response.status_code, principal, started, cache_hit)
        return response

    def _record_metrics(
        self, request: Request, status_code: int, cache_hit: bool, elapsed: float
    ) -> None:
        if not settings.metrics_enabled:
            return
        path = route_label(request)
        REGISTRY.inc_counter(
            "http_requests_total",
            help="Total HTTP requests by method, route and status.",
            labels={"method": request.method, "path": path, "status": str(status_code)},
        )
        REGISTRY.observe(
            "http_request_duration_seconds",
            elapsed,
            help="HTTP request latency in seconds.",
            labels={"method": request.method, "path": path},
        )
        if cache_hit:
            REGISTRY.inc_counter(
                "http_cache_hits_total",
                help="Responses served from the response cache.",
                labels={"path": path},
            )

    def _audit(
        self,
        request: Request,
        status_code: int,
        principal: Principal,
        started: float,
        cache_hit: bool,
    ) -> None:
        if not settings.api_audit_enabled:
            return
        duration_ms = (time.perf_counter() - started) * 1000
        client_ip = request.client.host if request.client else None
        try:
            session = get_session()
            try:
                session.add(
                    ApiRequest(
                        principal=principal.label,
                        role=str(principal.role),
                        request_id=getattr(request.state, "request_id", None),
                        method=request.method,
                        path=request.url.path,
                        query=request.url.query or None,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        cache_hit=cache_hit,
                        client_ip=client_ip,
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception:  # pragma: no cover - auditing must never break a request
            log.debug("api audit write failed", exc_info=True)
