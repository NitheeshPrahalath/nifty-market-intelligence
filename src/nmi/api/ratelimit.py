"""Fixed-window rate limiting, per principal.

A fixed one-minute window is deliberate: it is trivially explainable ("you get
N requests a minute"), cheap to enforce in Redis, and its worst case — a caller
bursting at a window boundary — is bounded at 2N, which is acceptable for a
read API. A limit of ``0`` disables limiting entirely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from nmi.api.kv import KVStore, get_store
from nmi.core.config import settings


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimiter:
    def __init__(self, store: KVStore | None = None, limit_per_minute: int | None = None) -> None:
        self._store = store or get_store()
        self.limit = (
            settings.api_rate_limit_per_minute if limit_per_minute is None else limit_per_minute
        )

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, principal: str, now: float | None = None) -> LimitDecision:
        if not self.enabled:
            return LimitDecision(True, self.limit, 0)
        moment = time.time() if now is None else now
        window = int(moment // 60)
        key = f"rate:{principal}:{window}"
        count = self._store.incr(key, ttl=60)
        remaining = max(self.limit - count, 0)
        if count > self.limit:
            retry_after = int(60 - (moment % 60)) or 1
            return LimitDecision(False, 0, retry_after)
        return LimitDecision(True, remaining, 0)
