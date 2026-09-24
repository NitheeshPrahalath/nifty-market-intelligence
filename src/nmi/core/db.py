"""Database engine and session management (SQLAlchemy 2.0).

The engine is created lazily from ``Settings.database_url``; tests may call
``init_engine(url=...)`` before first use to point at SQLite in-memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from nmi.core.config import settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def init_engine(url: str | None = None, echo: bool | None = None) -> Engine:
    """Create (or recreate) the global engine and session factory."""
    global _engine, _sessionmaker
    _engine = create_engine(
        url or settings.database_url,
        echo=settings.db_echo if echo is None else echo,
        pool_pre_ping=True,
    )
    _sessionmaker = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session() -> Session:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope with commit/rollback semantics."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
