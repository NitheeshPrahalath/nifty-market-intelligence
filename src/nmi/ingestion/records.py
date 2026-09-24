"""Canonical, validated input records produced by provider adapters.

Adapters (vendors) translate their own formats into these record types. Anything
that reaches the persistence layer has passed through these shapes, which makes
the pipeline vendor-agnostic and auditable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from nmi.core.enums import (
    CorporateActionType,
    Exchange,
    InstrumentType,
    PeriodType,
)


def _decimal(v) -> Decimal | None:
    """Coerce floats/strings to Decimal without float artifacts."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "nat", "-nan", "inf", "-inf", "none", "null"}:
        return None
    return Decimal(s).normalize()


def _date(v) -> date | None:
    if isinstance(v, date):
        return v
    if v in (None, "", "nan", "NaT"):
        return None
    return date.fromisoformat(str(v).strip())


class Candle(BaseModel):
    """One validated OHLCV trading row."""

    symbol: str = Field(max_length=32)
    exchange: Exchange = Exchange.NSE
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None = None
    turnover: Decimal | None = None
    source: str
    source_timestamp: datetime | None = None

    @field_validator(
        "open", "high", "low", "close", "turnover", mode="before"
    )
    @classmethod
    def _dex(cls, v) -> Decimal | None:
        return _decimal(v)

    @field_validator("trade_date", mode="before")
    @classmethod
    def _ddate(cls, v) -> date:
        d = _date(v)
        if d is None:
            raise ValueError("trade_date is required")
        return d


class CorporateActionRecord(BaseModel):
    symbol: str = Field(max_length=32)
    exchange: Exchange = Exchange.NSE
    action_type: CorporateActionType
    ex_date: date
    record_date: date | None = None
    pay_date: date | None = None
    ratio_numerator: int | None = None
    ratio_denominator: int | None = None
    dividend_amount: Decimal | None = None
    currency: str = "INR"
    description: str | None = None
    source: str
    source_identifier: str | None = None
    source_timestamp: datetime | None = None

    @field_validator(
        "ex_date", "record_date", "pay_date", mode="before"
    )
    @classmethod
    def _ddate(cls, v) -> date | None:
        return _date(v)

    @field_validator("dividend_amount", mode="before")
    @classmethod
    def _dex(cls, v) -> Decimal | None:
        return _decimal(v)

    @field_validator("ratio_numerator", "ratio_denominator", mode="before")
    @classmethod
    def _int(cls, v) -> int | None:
        if v in (None, ""):
            return None
        return int(v)


class IndexMembershipRecord(BaseModel):
    """An as-of interval ``[effective_from, effective_to)`` on an index."""

    index_code: str
    symbol: str = Field(max_length=32)
    exchange: Exchange = Exchange.NSE
    effective_from: date
    effective_to: date | None = None

    @field_validator("effective_from", mode="before")
    @classmethod
    def _from(cls, v) -> date:
        d = _date(v)
        if d is None:
            raise ValueError("effective_from is required")
        return d

    @field_validator("effective_to", mode="before")
    @classmethod
    def _to(cls, v) -> date | None:
        return _date(v)


class UniverseRow(BaseModel):
    symbol: str = Field(max_length=32)
    isin: str = Field(min_length=12, max_length=12)
    company_name: str
    exchange: Exchange = Exchange.NSE
    instrument_type: InstrumentType = InstrumentType.EQUITY
    sector: str | None = None
    industry: str | None = None


class StatementRecord(BaseModel):
    """Raw statement line item from a fundamental provider."""

    isin: str = Field(min_length=12, max_length=12)
    period_end: date
    period_type: PeriodType
    fiscal_year: str | None = None
    currency: str = "INR"
    source: str
    source_timestamp: datetime | None = None

    @field_validator("period_end", mode="before")
    @classmethod
    def _pend(cls, v) -> date:
        d = _date(v)
        if d is None:
            raise ValueError("period_end is required")
        return d


class IncomeStatementRecord(StatementRecord):
    total_revenue: Decimal | None = None
    operating_revenue: Decimal | None = None
    net_profit: Decimal | None = None
    eps: Decimal | None = None
    ebitda: Decimal | None = None
    ebitda_margin_pct: Decimal | None = None
    net_margin_pct: Decimal | None = None

    @field_validator(
        "total_revenue",
        "operating_revenue",
        "net_profit",
        "eps",
        "ebitda",
        "ebitda_margin_pct",
        "net_margin_pct",
        mode="before",
    )
    @classmethod
    def _dex(cls, v) -> Decimal | None:
        return _decimal(v)


class BalanceSheetRecord(StatementRecord):
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    total_debt: Decimal | None = None
    net_debt: Decimal | None = None
    net_worth: Decimal | None = None
    current_assets: Decimal | None = None
    current_liabilities: Decimal | None = None

    @field_validator(
        "total_assets",
        "total_liabilities",
        "total_debt",
        "net_debt",
        "net_worth",
        "current_assets",
        "current_liabilities",
        mode="before",
    )
    @classmethod
    def _dex(cls, v) -> Decimal | None:
        return _decimal(v)


class CashFlowRecord(StatementRecord):
    operating_cash_flow: Decimal | None = None
    investing_cash_flow: Decimal | None = None
    financing_cash_flow: Decimal | None = None
    free_cash_flow: Decimal | None = None

    @field_validator(
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "free_cash_flow",
        mode="before",
    )
    @classmethod
    def _dex(cls, v) -> Decimal | None:
        return _decimal(v)


StatementRecordUnion = (
    IncomeStatementRecord | BalanceSheetRecord | CashFlowRecord
)

StatementKind = Literal["income_statements", "balance_sheets", "cash_flows"]
