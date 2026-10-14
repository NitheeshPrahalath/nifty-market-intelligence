"""Fixtures for the Phase 7 API tests.

The app is exercised through ``TestClient``, which runs the ASGI app on a
*separate thread*; the database engine therefore needs ``check_same_thread=False``
plus a ``StaticPool`` so every session — the request session and the one the test
seeds with — shares the same in-memory database. The global engine in
``nmi.core.db`` is monkeypatched so ``get_session`` inside the middleware and
dependencies resolves to that engine too.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import nmi.core.models  # noqa: F401 - register tables
from nmi.api.app import create_app
from nmi.api.cache import ResponseCache
from nmi.api.kv import MemoryStore, reset_store
from nmi.api.ratelimit import RateLimiter
from nmi.core.config import settings
from nmi.core.db import Base

START = date(2024, 1, 1)
END = date(2024, 6, 28)


@pytest.fixture()
def api_engine(monkeypatch) -> Iterator:
    """A thread-safe in-memory SQLite engine wired into ``nmi.core.db``."""
    import nmi.core.db as db

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_sessionmaker", Session)
    reset_store()
    try:
        yield engine
    finally:
        reset_store()
        engine.dispose()


@pytest.fixture()
def make_client(api_engine, monkeypatch):
    """Build a TestClient factory with per-test auth/limit/cache settings."""
    clients: list[TestClient] = []

    def _make(
        *,
        auth: bool = False,
        keys: tuple[str, ...] = (),
        limit: int = 0,
        ttl: int = 60,
        audit: bool = True,
    ) -> TestClient:
        monkeypatch.setattr(settings, "api_auth_enabled", auth)
        monkeypatch.setattr(settings, "api_keys", list(keys))
        monkeypatch.setattr(settings, "api_rate_limit_per_minute", limit)
        monkeypatch.setattr(settings, "api_cache_ttl_seconds", ttl)
        monkeypatch.setattr(settings, "api_audit_enabled", audit)
        app = create_app(
            cache=ResponseCache(store=MemoryStore(), ttl=ttl),
            limiter=RateLimiter(store=MemoryStore(), limit_per_minute=limit),
        )
        client = TestClient(app)
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            client.close()


@pytest.fixture()
def seeded_engine(api_engine, pointed_at_long_fixtures):
    """Run the full compute pipeline once, into the API's engine."""
    from nmi.ingestion.backfill import IngestionService
    from nmi.metrics.service import MetricsService

    Session = sessionmaker(bind=api_engine, expire_on_commit=False)
    session = Session()
    try:
        ingest = IngestionService(session)
        ingest.seed_universe()
        ingest.backfill_corporate_actions(["NIFTY_50"], START, END)
        ingest.backfill_prices(["NIFTY_50"], START, END)
        ingest.backfill_fundamentals()

        metrics = MetricsService(session)
        metrics.backfill_index_prices(["NIFTY_50"])
        metrics.seed_strategies()
        metrics.compute_eod(["NIFTY_50"], START, END)
        session.commit()
    finally:
        session.close()
    return api_engine
