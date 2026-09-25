"""Shared structural types for the metrics layer (Phase 2).

Engines accept minimal duck-typed structs (ORM rows from the data layer or
test doubles), so they stay pure and framework-free.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class CandleLike(Protocol):
    trade_date: date
    open: object | None
    high: object
    low: object
    close: object
    adjusted_close: object | None
    volume: object | None


class RowWithPeriod(Protocol):
    period_end: date
    period_type: object


class IncomeLike(RowWithPeriod, Protocol):
    total_revenue: object | None
    operating_revenue: object | None
    net_profit: object | None
    eps: object | None
    ebitda: object | None
    ebitda_margin_pct: object | None
    net_margin_pct: object | None
    shares_outstanding: object | None


class BalanceLike(RowWithPeriod, Protocol):
    total_assets: object | None
    total_liabilities: object | None
    total_debt: object | None
    net_debt: object | None
    net_worth: object | None
    current_assets: object | None
    current_liabilities: object | None


class CashFlowLike(RowWithPeriod, Protocol):
    operating_cash_flow: object | None
    investing_cash_flow: object | None
    financing_cash_flow: object | None
    free_cash_flow: object | None
