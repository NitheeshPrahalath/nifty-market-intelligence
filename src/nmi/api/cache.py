"""HTTP response caching for read endpoints.

Only ``GET`` responses that are cheap to reproduce and safe to share are
cached; anything that carries a principal-specific view stays uncached. The
cache is keyed on the exact request target (path + sorted query) so two
different filters never collide.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from nmi.api.kv import KVStore, get_store
from nmi.core.config import settings

_CACHEABLE_METHODS = frozenset({"GET"})
# Endpoints whose whole point is freshness or whose body depends on the caller.
_NEVER_CACHE = ("/health", "/audit")


@dataclass(frozen=True)
class CacheResult:
    value: bytes | None
    hit: bool


class ResponseCache:
    def __init__(self, store: KVStore | None = None, ttl: int | None = None) -> None:
        self._store = store or get_store()
        self.ttl = settings.api_cache_ttl_seconds if ttl is None else ttl

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def key(self, method: str, path: str, query: str, principal: str) -> str:
        """A stable cache key for one request target.

        The principal is part of the key: the same URL can legitimately return a
        different body to different callers once auth is on, and a shared cache
        must never leak one tenant's view to another.
        """
        raw = f"{method}|{path}|{query}|{principal}".encode()
        digest = hashlib.sha256(raw).hexdigest()
        return f"resp:{digest}"

    def cacheable(self, method: str, path: str) -> bool:
        if not self.enabled or method not in _CACHEABLE_METHODS:
            return False
        return not any(path.endswith(suffix) or suffix in path for suffix in _NEVER_CACHE)

    def get(self, key: str) -> CacheResult:
        value = self._store.get(key)
        return CacheResult(value=value, hit=value is not None)

    def put(self, key: str, value: bytes) -> None:
        self._store.set(key, value, self.ttl)

    def clear(self) -> None:
        self._store.clear()
