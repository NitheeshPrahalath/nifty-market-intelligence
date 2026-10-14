"""The gateway middleware: authenticate, limit, cache and audit every request.

Doing all four in one middleware is not a shortcut — they share the same
per-request facts (who is calling, how long it took, whether it was served from
cache), and splitting them would mean either four DB round-trips or threading
mutable state through the stack. Keeping them together also makes the audit row
the reliable record of *everything* the gateway decided.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nmi.api.auth import resolve_principal
from nmi.api.cache import ResponseCache
from nmi.api.ratelimit import RateLimiter
from nmi.core.config import settings
from nmi.core.db import get_session
from nmi.core.models import ApiRequest

log = logging.getLogger(__name__)

# Headers that must not survive a rebuild of the response from cached bytes.
_STRIP_HEADERS = {"content-length"}


class GatewayMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cache: ResponseCache, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.cache = cache
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        principal, authenticated = resolve_principal(request.headers)
        request.state.principal = principal
        request.state.authenticated = authenticated

        if not authenticated:
            return self._finish(
                request,
                self._error(401, "invalid or missing API key"),
                principal,
                started,
                cache_hit=False,
            )

        decision = self.limiter.check(principal)
        if not decision.allowed:
            response = self._error(429, "rate limit exceeded")
            response.headers["Retry-After"] = str(decision.retry_after)
            return self._finish(request, response, principal, started, cache_hit=False)

        cache_key = self.cache.key(request.method, request.url.path, request.url.query, principal)
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
        principal: str,
        started: float,
        *,
        cache_hit: bool,
    ) -> Response:
        response.headers["X-Request-Id"] = getattr(request.state, "request_id", "")
        self._audit(request, response.status_code, principal, started, cache_hit)
        return response

    def _audit(
        self,
        request: Request,
        status_code: int,
        principal: str,
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
                        principal=principal,
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


def error_payload(detail: str) -> str:
    return json.dumps({"detail": detail})
