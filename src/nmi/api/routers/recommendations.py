"""Recommendation dashboard and detail."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import Page as PageModel
from nmi.api.schemas import RecommendationDetail, RecommendationRow

router = APIRouter(tags=["recommendations"])


@router.get("/recommendations", response_model=PageModel[RecommendationRow])
def list_recommendations(
    index: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query(description="RecommendationState value")] = None,
    strategy: Annotated[str | None, Query(description="Strategy code")] = None,
    horizon: Annotated[str | None, Query()] = None,
    as_of: Annotated[
        date | None, Query(description="Only recommendations live on this day")
    ] = None,
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    items, total = service.list_recommendations(
        db,
        index_code=index,
        state=state,
        strategy_code=strategy,
        horizon=horizon,
        as_of=as_of,
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


@router.get("/recommendations/{recommendation_id}", response_model=RecommendationDetail)
def recommendation_detail(
    recommendation_id: int,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> RecommendationDetail:
    detail = service.recommendation_detail(db, recommendation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown recommendation {recommendation_id}")
    return detail
