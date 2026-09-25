"""Relational models for the data layer.

The platform keeps each responsibility in its own entity family:

* reference data        -> Sector, Industry, Company, Instrument, Index
* membership history    -> IndexMembership (effective-date ranges)
* market data           -> DailyPrice, IntradayPrice, CorporateAction
* fundamentals raw      -> IncomeStatement, BalanceSheet, CashFlow
* fundamentals derived  -> FundamentalMetric
* pipeline audit        -> IngestionRun, IngestionError

Time-series rows always carry dates/timestamps. Historical analytical data is
never overwritten in place; later phases add version-aware tables.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Index as SqlIndex,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nmi.core.db import Base
from nmi.core.enums import (
    CorporateActionType,
    Exchange,
    InstrumentType,
    PeriodType,
    RunStatus,
    Severity,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(60), unique=True)

    industries: Mapped[list[Industry]] = relationship(back_populates="sector")


class Industry(Base):
    __tablename__ = "industries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"))

    sector: Mapped[Sector | None] = relationship(back_populates="industries")
    companies: Mapped[list[Company]] = relationship(back_populates="industry")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    listing_date: Mapped[date | None] = mapped_column(Date)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"))
    industry_id: Mapped[int | None] = mapped_column(ForeignKey("industries.id"))

    sector: Mapped[Sector | None] = relationship()
    industry: Mapped[Industry | None] = relationship(back_populates="companies")
    instruments: Mapped[list[Instrument]] = relationship(back_populates="company")
    income_statements: Mapped[list[IncomeStatement]] = relationship(
        back_populates="company"
    )
    balance_sheets: Mapped[list[BalanceSheet]] = relationship(
        back_populates="company"
    )
    cash_flows: Mapped[list[CashFlow]] = relationship(back_populates="company")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_instrument_exchange_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    exchange: Mapped[Exchange] = mapped_column(
        Enum(Exchange, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument_type: Mapped[InstrumentType] = mapped_column(
        Enum(
            InstrumentType,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        default=InstrumentType.EQUITY,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    company: Mapped[Company] = relationship(back_populates="instruments")
    memberships: Mapped[list[IndexMembership]] = relationship(back_populates="instrument")
    prices: Mapped[list[DailyPrice]] = relationship(back_populates="instrument")
    corporate_actions: Mapped[list[CorporateAction]] = relationship(
        back_populates="instrument"
    )


class Index(Base):
    __tablename__ = "indices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)

    memberships: Mapped[list[IndexMembership]] = relationship(back_populates="index")


class IndexMembership(Base):
    """As-of index membership.

    Membership is an interval ``[effective_from, effective_to)``. ``effective_to``
    is NULL for currently-held membership. This is what enables accurate
    survivorship-safe historical analysis and backtests (section 3 of the spec).
    """

    __tablename__ = "index_memberships"
    __table_args__ = (
        UniqueConstraint(
            "index_id", "instrument_id", "effective_from", name="uq_membership_interval"
        ),
        SqlIndex("ix_membership_lookup", "instrument_id", "effective_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_id: Mapped[int] = mapped_column(ForeignKey("indices.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_defunct: Mapped[bool] = mapped_column(default=False, nullable=False)

    index: Mapped[Index] = relationship(back_populates="memberships")
    instrument: Mapped[Instrument] = relationship(back_populates="memberships")


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("instrument_id", "trade_date", name="uq_daily_price_per_day"),
        SqlIndex("ix_daily_prices_instrument_date", "instrument_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    adjustment_factor: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    is_incomplete: Mapped[bool] = mapped_column(default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    instrument: Mapped[Instrument] = relationship(back_populates="prices")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "action_type",
            "ex_date",
            "source",
            name="uq_corporate_action_identity",
        ),
        SqlIndex(
            "ix_corporate_actions_instrument_ex",
            "instrument_id",
            "ex_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    action_type: Mapped[CorporateActionType] = mapped_column(
        Enum(
            CorporateActionType,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_date: Mapped[date | None] = mapped_column(Date)
    pay_date: Mapped[date | None] = mapped_column(Date)
    ratio_numerator: Mapped[int | None] = mapped_column(BigInteger)
    ratio_denominator: Mapped[int | None] = mapped_column(BigInteger)
    dividend_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_identifier: Mapped[str | None] = mapped_column(String(128))
    source_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    instrument: Mapped[Instrument] = relationship(back_populates="corporate_actions")

    @property
    def share_ratio(self) -> Decimal | None:
        """Shares after / shares before for share-ratio actions (split/bonus/rights)."""
        if self.ratio_numerator is None or self.ratio_denominator is None:
            return None
        return Decimal(self.ratio_numerator) / Decimal(self.ratio_denominator)


class IncomeStatement(Base):
    __tablename__ = "income_statements"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_end",
            "period_type",
            "source",
            name="uq_income_statement_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    fiscal_year: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    total_revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    operating_revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    ebitda: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    ebitda_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    net_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    extras: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    company: Mapped[Company] = relationship(back_populates="income_statements")


class BalanceSheet(Base):
    __tablename__ = "balance_sheets"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_end",
            "period_type",
            "source",
            name="uq_balance_sheet_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    fiscal_year: Mapped[str | None] = mapped_column(String(16))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    net_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    net_worth: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    current_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    current_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    extras: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    company: Mapped[Company] = relationship(back_populates="balance_sheets")


class CashFlow(Base):
    __tablename__ = "cash_flows"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_end",
            "period_type",
            "source",
            name="uq_cash_flow_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    fiscal_year: Mapped[str | None] = mapped_column(String(16))
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    investing_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    financing_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    free_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    extras: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    company: Mapped[Company] = relationship(back_populates="cash_flows")


class FundamentalMetric(Base):
    """Derived fundamental metrics (computed by the Phase-2 engine).

    Keyed on (company, metric, as_of) so every calculation is versionable and
    historically preserved.
    """

    __tablename__ = "fundamental_metrics"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "metric", "as_of", name="uq_fundamental_metric_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class IndexPrice(Base):
    """Close history of an index (benchmark for relative strength / regimes).

    Benchmarks (Nifty 50, sector indices, ...) are their own time series; they
    are intentionally separate from instrument prices so their loading logic
    can differ from equity EOD ingestion.
    """

    __tablename__ = "index_prices"
    __table_args__ = (
        UniqueConstraint("index_id", "trade_date", name="uq_index_price_per_day"),
        SqlIndex("ix_index_prices_index_date", "index_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_id: Mapped[int] = mapped_column(ForeignKey("indices.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TechnicalIndicator(Base):
    """Wide, versioned time-series of daily technical indicators.

    Only numeric values are stored here (interpretations like "bullish" are
    derived downstream). ``calc_version`` lets the platform recompute with a
    newer definition while keeping historical values auditably separated.
    """

    __tablename__ = "technical_indicators"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "as_of", "calc_version", name="uq_tech_snapshot"
        ),
        SqlIndex("ix_technical_instrument_asof", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    sma20: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sma50: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sma100: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sma200: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ema20: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ema50: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ema200: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))

    rsi14: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    macd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    macd_hist: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    roc10: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    stoch_k: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    stoch_d: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    williams_r: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    cci20: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    adx14: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    plus_di14: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    minus_di14: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    atr14: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    hist_vol_20: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    hist_vol_60: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    bollinger_upper: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    bollinger_middle: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    bollinger_lower: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    bollinger_width: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    bollinger_percent_b: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    volume_sma20: Mapped[int | None] = mapped_column(BigInteger)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume_spike: Mapped[bool] = mapped_column(default=False, nullable=False)
    obv: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))

    high_52w: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    low_52w: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    dist_from_high_52w_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    dist_from_low_52w_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    recovery_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    breakout_52w: Mapped[bool] = mapped_column(default=False, nullable=False)
    breakdown_52w: Mapped[bool] = mapped_column(default=False, nullable=False)
    trend_state: Mapped[str | None] = mapped_column(String(24))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MomentumMetric(Base):
    __tablename__ = "momentum_metrics"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "as_of", "calc_version", name="uq_momentum_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)
    return_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_6m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    return_12m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class RSTrend(enum.StrEnum):
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DETERIORATING = "DETERIORATING"


class RelativeStrengthMetric(Base):
    __tablename__ = "relative_strength_metrics"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "as_of", "benchmark", "calc_version",
            name="uq_relative_strength_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    benchmark: Mapped[str] = mapped_column(String(64), nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rs_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    rs_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    rs_6m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    rs_12m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    rs_trend: Mapped[RSTrend] = mapped_column(
        Enum(RSTrend, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ValuationLabel(enum.StrEnum):
    CHEAP = "CHEAP"
    FAIRLY_VALUED = "FAIRLY_VALUED"
    MODERATELY_EXPENSIVE = "MODERATELY_EXPENSIVE"
    EXPENSIVE = "EXPENSIVE"


class ValuationMetric(Base):
    __tablename__ = "valuation_metrics"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "as_of", "calc_version", name="uq_valuation_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    pe: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    ev_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    peg: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    dividend_yield: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    fcf_yield: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    pe_median_3y: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    pe_percentile_3y: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    pe_deviation_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pb_percentile_3y: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    ev_ebitda_percentile_3y: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    valuation_label: Mapped[ValuationLabel] = mapped_column(
        Enum(
            ValuationLabel,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MarketRegimeLabel(enum.StrEnum):
    RISK_ON = "RISK_ON"
    CAUTIOUS = "CAUTIOUS"
    RISK_OFF = "RISK_OFF"
    STRESSED = "STRESSED"


class MarketRegime(Base):
    """Market-regime snapshot (Phase 3) for one index, per as-of day.

    Combines breadth (% of members above their own SMAs), index momentum,
    realised volatility, index drawdown and sector participation into a single
    0-100 ``regime_score`` plus an interpretable ``regime_label``.
    """

    __tablename__ = "market_regimes"
    __table_args__ = (
        UniqueConstraint("index_id", "as_of", "calc_version", name="uq_market_regime_snapshot"),
        SqlIndex("ix_market_regime_index_asof", "index_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_id: Mapped[int] = mapped_column(ForeignKey("indices.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    members_above_sma20: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    members_above_sma50: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    members_above_sma200: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    breadth_above_sma20_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    breadth_above_sma50_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    breadth_above_sma200_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    index_return_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    index_return_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    index_return_6m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    index_hist_vol_20: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    index_hist_vol_60: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    index_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    sector_participation_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    regime_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    regime_label: Mapped[MarketRegimeLabel] = mapped_column(
        Enum(
            MarketRegimeLabel,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SectorState(enum.StrEnum):
    LEADING = "LEADING"
    NEUTRAL = "NEUTRAL"
    LAGGING = "LAGGING"
    DEFENSIVE = "DEFENSIVE"


class SectorMetric(Base):
    """Cross-sectional sector aggregates (Phase 3) within one index."""

    __tablename__ = "sector_metrics"
    __table_args__ = (
        UniqueConstraint(
            "index_id",
            "sector_id",
            "as_of",
            "calc_version",
            name="uq_sector_metric_snapshot",
        ),
        SqlIndex("ix_sector_metric_index_asof", "index_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_id: Mapped[int] = mapped_column(ForeignKey("indices.id"), nullable=False)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    breadth_above_sma50_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    breadth_above_sma200_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    avg_return_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    avg_return_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    avg_return_6m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    avg_rs_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    avg_rsi14: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    relative_to_index_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    sector_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    sector_state: Mapped[SectorState] = mapped_column(
        Enum(
            SectorState,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class HorizonType(enum.StrEnum):
    SHORT_TERM = "SHORT_TERM"
    MEDIUM_TERM = "MEDIUM_TERM"
    LONG_TERM = "LONG_TERM"


class HorizonMetric(Base):
    """Short/medium/long-horizon fit scores (Phase 3) per instrument-day.

    The three scores are always comparable on a 0-100 scale; ``preferred_horizon``
    names the dominant one, which the scoring engine uses to select component
    emphasis downstream.
    """

    __tablename__ = "horizon_metrics"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", "calc_version", name="uq_horizon_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    short_term_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    medium_term_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    long_term_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    preferred_horizon: Mapped[HorizonType] = mapped_column(
        Enum(
            HorizonType,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )
    horizon_confidence: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ScoreLabel(enum.StrEnum):
    STRONG = "STRONG"
    GOOD = "GOOD"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    POOR = "POOR"


class ScoringSnapshot(Base):
    """Eight component scores plus the weighted composite (Phase 3).

    ``parameter_set`` records which ``strategy_parameters`` set produced the
    weights, so a score can always be explained and reproduced.
    """

    __tablename__ = "scoring_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", "calc_version", name="uq_scoring_snapshot"),
        SqlIndex("ix_scoring_instrument_asof", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parameter_set: Mapped[str] = mapped_column(String(32), nullable=False)
    preferred_horizon: Mapped[HorizonType] = mapped_column(
        Enum(
            HorizonType,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )

    trend_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    relative_strength_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    growth_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    valuation_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    horizon_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    sector_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    composite_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    score_label: Mapped[ScoreLabel] = mapped_column(
        Enum(
            ScoreLabel,
            native_enum=False,
            values_callable=lambda e: [x.value for x in e],
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class StrategyParameter(Base):
    """Named, versioned strategy tunables (Phase 3).

    Scoring weights live here (one row per component per parameter set) so they
    can be audited, adjusted and rolled back without touching code.
    """

    __tablename__ = "strategy_parameters"
    __table_args__ = (UniqueConstraint("parameter_set", "name", name="uq_strategy_parameter_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parameter_set: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        default=RunStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class IngestionError(Base):
    """Audit trail for every failed/incomplete ingestion step."""

    __tablename__ = "ingestion_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    trade_date: Mapped[date | None] = mapped_column(Date)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
