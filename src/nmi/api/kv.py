"""A tiny key-value abstraction shared by the response cache and the rate limiter.

Both features want the same three primitives — read, write-with-ttl, and an
atomic counter — and both must keep working when Redis is not deployed. The
in-memory implementation is therefore the default and Redis is an optimisation,
never a dependency: a platform that refuses to answer a read because a cache
box is down is worse than one that answers slightly slower.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from nmi.core.config import settings


class KVStore(Protocol):
    """The minimal surface the cache and limiter rely on."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl: int) -> None: ...

    def incr(self, key: str, ttl: int) -> int: ...

    def clear(self) -> None: ...


class MemoryStore:
    """Process-local store with per-key expiry.

    Values are byte strings so the same code path serves a Redis backend; the
    expiry is checked lazily on read, which is accurate enough for caching and
    fixed-window limiting and avoids a background reaper thread.
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at and expires_at < time.monotonic():
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._values[key] = (value, time.monotonic() + ttl)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            entry = self._values.get(key)
            now = time.monotonic()
            if entry is None or (entry[1] and entry[1] < now):
                self._values[key] = (b"1", now + ttl)
                return 1
            count = int(entry[0]) + 1
            self._values[key] = (str(count).encode(), entry[1])
            return count

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class RedisStore:
    """Redis-backed store; every operation degrades to a miss on failure.

    A cache or limiter that can take the API down is a liability, so connection
    errors are swallowed (a miss, or an allowed request) rather than raised.
    """

    def __init__(self, url: str, prefix: str = "nmi:") -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=False)
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> bytes | None:
        try:
            return self._client.get(self._key(key))
        except Exception:  # pragma: no cover - depends on a live Redis
            return None

    def set(self, key: str, value: bytes, ttl: int) -> None:
        if ttl <= 0:
            return
        try:
            self._client.set(self._key(key), value, ex=ttl)
        except Exception:  # pragma: no cover
            return

    def incr(self, key: str, ttl: int) -> int:
        try:
            pipe = self._client.pipeline()
            pipe.incr(self._key(key))
            pipe.expire(self._key(key), ttl)
            return int(pipe.execute()[0])
        except Exception:  # pragma: no cover
            return 1

    def clear(self) -> None:
        try:
            keys = list(self._client.scan_iter(f"{self._prefix}*"))
            if keys:
                self._client.delete(*keys)
        except Exception:  # pragma: no cover
            return


def build_store(url: str | None) -> KVStore:
    """Pick Redis when configured and reachable, memory otherwise."""
    if url and url.startswith(("redis://", "rediss://", "unix://")):
        try:
            return RedisStore(url)
        except Exception:  # pragma: no cover - import/connection failure
            return MemoryStore()
    return MemoryStore()


_store: KVStore | None = None


def get_store() -> KVStore:
    """The process-wide store, built from settings on first use."""
    global _store
    if _store is None:
        _store = build_store(settings.redis_url)
    return _store


def reset_store() -> None:
    """Drop the process-wide store (used by tests and by ``serve`` reloads)."""
    global _store
    _store = None
