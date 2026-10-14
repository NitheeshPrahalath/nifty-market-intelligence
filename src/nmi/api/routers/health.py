"""Health and readiness."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from nmi.api.deps import get_db
from nmi.api.schemas import HealthResponse
from nmi.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: Annotated[Session, Depends(get_db)] = None) -> HealthResponse:  # type: ignore[assignment]
    """Liveness plus a real database round-trip."""
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover
        database = "unavailable"
    return HealthResponse(version=settings.api_version, database=database)
