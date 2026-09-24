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
