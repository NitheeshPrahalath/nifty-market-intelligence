"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from nmi.core.db import get_session

MAX_PAGE_SIZE = 200


def get_db() -> Iterator[Session]:
    """A request-scoped session, always closed."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@dataclass(frozen=True)
class Page:
    """1-based pagination, shared by every list endpoint."""

    page: int = 1
    page_size: int = 50

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def get_page(
    page: int = 1,
    page_size: int = 50,
) -> Page:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    return Page(page=page, page_size=page_size)


def get_principal(request: Request) -> str:
    return getattr(request.state, "principal", "anonymous")


def get_role(request: Request) -> str:
    return getattr(request.state, "role", "viewer")


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def require_scope(scope: str) -> Callable[[Request], None]:
    """A dependency that enforces a role scope on a route.

    Authentication (401) is the gateway middleware's job; this is authorization
    (403) — a valid key that lacks ``scope`` is refused before the handler runs.
    """

    def dependency(request: Request) -> None:
        scopes: frozenset[str] = getattr(request.state, "scopes", frozenset())
        if scope not in scopes:
            raise HTTPException(status_code=403, detail=f"missing required scope: {scope}")

    return dependency
