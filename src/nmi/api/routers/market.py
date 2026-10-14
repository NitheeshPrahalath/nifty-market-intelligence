"""Market overview endpoint."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import get_db
from nmi.api.schemas import MarketOverview

router = APIRouter(tags=["market"])


@router.get("/market/overview", response_model=MarketOverview)
def market_overview(
    index: Annotated[str, Query(description="Index code, e.g. NIFTY_50")] = "NIFTY_50",
    as_of: Annotated[
        date | None, Query(description="As-of date (default: latest regime day)")
    ] = None,
    movers: Annotated[int, Query(ge=0, le=25, description="Top/bottom movers to return")] = 5,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> MarketOverview:
    overview = service.market_overview(db, index, as_of, movers)
    if overview is None:
        raise HTTPException(status_code=404, detail=f"unknown index {index!r}")
    return overview
