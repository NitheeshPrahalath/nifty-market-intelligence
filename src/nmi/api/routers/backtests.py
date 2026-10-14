"""Backtest run inspection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import (
    BacktestDetail,
    BacktestRunView,
    BacktestTradeView,
    EquityPointView,
)
from nmi.api.schemas import Page as PageModel

router = APIRouter(tags=["backtests"])


@router.get("/backtests", response_model=PageModel[BacktestRunView])
def list_backtests(
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    items, total = service.list_backtest_runs_db(db, page.offset, page.limit)
    pages = (total + page.page_size - 1) // page.page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": pages,
    }


@router.get("/backtests/{run_id}", response_model=BacktestDetail)
def backtest_detail(
    run_id: int,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> BacktestDetail:
    detail = service.backtest_detail(db, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown backtest run {run_id}")
    return detail


@router.get("/backtests/{run_id}/trades", response_model=PageModel[BacktestTradeView])
def backtest_trades(
    run_id: int,
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    if service.backtest_detail(db, run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown backtest run {run_id}")
    items, total = service.backtest_trades(db, run_id, page.offset, page.limit)
    pages = (total + page.page_size - 1) // page.page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": pages,
    }


@router.get("/backtests/{run_id}/equity", response_model=list[EquityPointView])
def backtest_equity(
    run_id: int,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> list[EquityPointView]:
    if service.backtest_detail(db, run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown backtest run {run_id}")
    return service.backtest_equity(db, run_id)
