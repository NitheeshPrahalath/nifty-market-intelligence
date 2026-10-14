"""Screener endpoint: filter and rank the scored universe."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import Page as PageModel
from nmi.api.schemas import ScreenerRow

router = APIRouter(tags=["screener"])

_SORT_FIELDS = (
    "composite",
    "trend",
    "momentum",
    "relative_strength",
    "quality",
    "growth",
    "valuation",
    "risk",
    "liquidity",
    "horizon",
    "sector",
    "symbol",
)


@router.get("/screener", response_model=PageModel[ScreenerRow])
def screener(
    index: Annotated[str | None, Query(description="Limit to current members of an index")] = None,
    as_of: Annotated[
        date | None, Query(description="As-of date (default: latest scoring day)")
    ] = None,
    horizon: Annotated[str | None, Query(description="SHORT_TERM|MEDIUM_TERM|LONG_TERM")] = None,
    label: Annotated[str | None, Query(description="STRONG|GOOD|NEUTRAL|WEAK|POOR")] = None,
    sector: Annotated[str | None, Query(description="Sector name or code")] = None,
    q: Annotated[str | None, Query(description="Free-text symbol/name search")] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    sort: Annotated[str, Query(description="Sort field")] = "composite",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    if sort not in _SORT_FIELDS:
        sort = "composite"
    items, total, _resolved = service.screen(
        db,
        index_code=index,
        as_of=as_of,
        horizon=horizon,
        label=label,
        sector=sector,
        query=q,
        min_score=min_score,
        max_score=max_score,
        sort=sort,
        order=order,
        offset=page.offset,
        limit=page.limit,
    )
    pages = (total + page.page_size - 1) // page.page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": pages,
    }
