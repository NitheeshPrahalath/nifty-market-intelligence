"""Health, readiness and metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from nmi.api.deps import get_db
from nmi.api.schemas import HealthResponse
from nmi.core.config import settings

router = APIRouter(tags=["health"])


def _database_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - exercised only on a broken database
        return False


@router.get("/health", response_model=HealthResponse)
def health(db: Annotated[Session, Depends(get_db)] = None) -> HealthResponse:  # type: ignore[assignment]
    """Liveness plus a real database round-trip.

    Always returns 200 so an orchestrator can distinguish "process is up" from
    "process is up but its dependency is not" via the ``database`` field. Use
    ``/health/ready`` when a 503 on a bad dependency is what you want.
    """
    return HealthResponse(
        version=settings.api_version,
        database="ok" if _database_ok(db) else "unavailable",
    )


@router.get("/health/ready", response_model=HealthResponse)
def readiness(db: Annotated[Session, Depends(get_db)] = None) -> JSONResponse:  # type: ignore[assignment]
    """Readiness probe: 200 when the database is reachable, 503 otherwise."""
    ok = _database_ok(db)
    body = HealthResponse(
        status="ready" if ok else "not_ready",
        version=settings.api_version,
        database="ok" if ok else "unavailable",
    )
    return JSONResponse(status_code=200 if ok else 503, content=body.model_dump())
