"""Stock detail and price history."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import Page as PageModel
from nmi.api.schemas import PriceBar, StockDetail

router = APIRouter(tags=["stocks"])


@router.get("/stocks/{symbol}", response_model=StockDetail)
def stock_detail(
    symbol: str,
    as_of: Annotated[
        date | None, Query(description="As-of date (default: latest technical day)")
    ] = None,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> StockDetail:
    detail = service.stock_detail(db, symbol, as_of)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
    return detail


@router.get("/stocks/{symbol}/prices", response_model=PageModel[PriceBar])
def stock_prices(
    symbol: str,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    instrument = service.get_instrument(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")
    items, total = service.price_history(db, instrument.id, start, end, page.offset, page.limit)
    pages = (total + page.page_size - 1) // page.page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": pages,
    }
