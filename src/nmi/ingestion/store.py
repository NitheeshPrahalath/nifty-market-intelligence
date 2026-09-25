"""Persistence layer: dialect-safe upserts for the data entities.

All writes are idempotent (insert-or-update). Historical rows are never deleted;
re-ingestion supersedes the same (instrument, date) key and records the new
source. Index membership uses interval semantics (closing superseded intervals).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from nmi.core.models import (
    BalanceSheet,
    CashFlow,
    Company,
    CorporateAction,
    DailyPrice,
    FundamentalMetric,
    IncomeStatement,
    Index,
    IndexMembership,
    IndexPrice,
    Industry,
    Instrument,
    MomentumMetric,
    RelativeStrengthMetric,
    Sector,
    TechnicalIndicator,
    ValuationMetric,
)
from nmi.ingestion.adjustments import AdjustedCandle
from nmi.ingestion.records import (
    BalanceSheetRecord,
    Candle,
    CashFlowRecord,
    CorporateActionRecord,
    IncomeStatementRecord,
    IndexMembershipRecord,
    UniverseRow,
)

log = logging.getLogger(__name__)


def _insert_cls(session: Session):
    if session.get_bind().dialect.name == "postgresql":
        return pg_insert
    return sqlite_insert


def _bulk_upsert(
    session: Session,
    table,
    rows: Iterable[Mapping],
    index_elements: list[str],
    exclude_from_update: set[str] | None = None,
) -> None:
    rows = list(rows)
    if not rows:
        return
    insert = _insert_cls(session)
    stmt = insert(table).values(rows)
    excluded = stmt.excluded
    set_ = {
        k: excluded[k]
        for k in rows[0].keys()
        if k not in (index_elements or []) and k not in (exclude_from_update or set())
    }
    stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)
    session.execute(stmt)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def upsert_universe(session: Session, rows: list[UniverseRow]) -> dict[str, int]:
    """Upsert sectors/industries/companies/instruments. Returns symbol->instrument id."""
    symbol_to_instrument: dict[str, int] = {}
    sector_ids: dict[str, int] = {}
    industry_ids: dict[str, int] = {}

    for row in rows:
        sector_id: int | None = None
        if row.sector:
            existing = session.scalar(select(Sector.id).where(Sector.name == row.sector))
            if existing is not None:
                sector_id = sector_ids[row.sector] = existing
            else:
                s = Sector(name=row.sector)
                session.add(s)
                session.flush()
                sector_id = sector_ids[row.sector] = s.id

        industry_id: int | None = None
        if row.industry:
            existing = session.scalar(
                select(Industry.id).where(Industry.name == row.industry)
            )
            if existing is not None:
                industry_id = industry_ids[row.industry] = existing
            else:
                ind = Industry(name=row.industry, sector_id=sector_id)
                session.add(ind)
                session.flush()
                industry_id = industry_ids[row.industry] = ind.id

        company = session.scalar(
            select(Company).where(Company.isin == row.isin)
        )
        if company is None:
            company = Company(
                isin=row.isin,
                name=row.company_name,
                sector_id=sector_id,
                industry_id=industry_id,
            )
            session.add(company)
            session.flush()
        else:
            company.sector_id = sector_id
            company.industry_id = industry_id

        instrument = session.scalar(
            select(Instrument).where(
                Instrument.exchange == row.exchange.value,
                Instrument.symbol == row.symbol,
            )
        )
        if instrument is None:
            instrument = Instrument(
                company_id=company.id,
                exchange=row.exchange,
                symbol=row.symbol,
                instrument_type=row.instrument_type,
            )
            session.add(instrument)
            session.flush()
        symbol_to_instrument[row.symbol] = instrument.id

    session.flush()
    return symbol_to_instrument


def ensure_indices(
    session: Session, index_codes: Iterable[str]
) -> dict[str, int]:
    session.add_all(
        Index(code=code, name=code)
        for code in index_codes
        if session.scalar(select(Index.id).where(Index.code == code)) is None
    )
    session.flush()
    return {
        code: session.scalar(select(Index.id).where(Index.code == code))
        for code in index_codes
    }


def apply_memberships(
    session: Session,
    records: Iterable[IndexMembershipRecord],
    index_by_code: dict[str, int],
    instrument_by_symbol: dict[str, int],
) -> int:
    """Apply as-of membership, closing superseded intervals. Returns applied count."""
    applied = 0
    for rec in sorted(
        records, key=lambda r: (r.index_code, r.symbol, r.effective_from)
    ):
        index_id = index_by_code.get(rec.index_code)
        instrument_id = instrument_by_symbol.get(rec.symbol)
        if index_id is None or instrument_id is None:
            log.warning(
                "skipping membership for unknown index/symbol: %s / %s",
                rec.index_code,
                rec.symbol,
            )
            continue
        existing = session.scalars(
            select(IndexMembership).where(
                IndexMembership.index_id == index_id,
                IndexMembership.instrument_id == instrument_id,
            )
        ).all()
        same_from = [r for r in existing if r.effective_from == rec.effective_from]
        if same_from:
            r = same_from[0]
            r.effective_to = rec.effective_to
            r.is_defunct = rec.effective_to is not None
        else:
            for r in existing:
                if (
                    r.effective_from < rec.effective_from
                    and (r.effective_to is None or r.effective_to >= rec.effective_from)
                ):
                    r.effective_to = rec.effective_from - timedelta(days=1)
                    r.is_defunct = rec.effective_to is not None
            session.add(
                IndexMembership(
                    index_id=index_id,
                    instrument_id=instrument_id,
                    effective_from=rec.effective_from,
                    effective_to=rec.effective_to,
                    is_defunct=rec.effective_to is not None,
                )
            )
        applied += 1
    session.flush()
    return applied


def upsert_corporate_actions(
    session: Session,
    records: Iterable[CorporateActionRecord],
    instrument_by_symbol: dict[str, int],
) -> int:
    keys: list[dict] = []
    for rec in records:
        instrument_id = instrument_by_symbol.get(rec.symbol)
        if instrument_id is None:
            log.warning(
                "skipping corporate action for unknown symbol: %s", rec.symbol
            )
            continue
        keys.append(
            {
                "instrument_id": instrument_id,
                "action_type": rec.action_type.value,
                "ex_date": rec.ex_date,
                "record_date": rec.record_date,
                "pay_date": rec.pay_date,
                "ratio_numerator": rec.ratio_numerator,
                "ratio_denominator": rec.ratio_denominator,
                "dividend_amount": rec.dividend_amount,
                "currency": rec.currency,
                "description": rec.description,
                "source": rec.source,
                "source_identifier": rec.source_identifier,
                "source_timestamp": rec.source_timestamp,
            }
        )
    _bulk_upsert(
        session,
        CorporateAction.__table__,
        keys,
        index_elements=["instrument_id", "action_type", "ex_date", "source"],
    )
    session.flush()
    return len(keys)


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

def upsert_daily_prices(
    session: Session,
    instrument_id: int,
    candles: Iterable[Candle],
    adjusted: Iterable[AdjustedCandle],
    incomplete_dates: set[date] | None = None,
    source: str = "csv",
    source_timestamp: object = None,
) -> int:
    """Idempotently persist adjusted daily prices keyed on (instrument, trade_date)."""
    incomplete = incomplete_dates or set()
    c_iter = list(candles)
    a_iter = {a.trade_date: a for a in adjusted}
    rows = []
    for candle in c_iter:
        adj = a_iter.get(candle.trade_date)
        is_incomplete = candle.trade_date in incomplete
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": candle.trade_date,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "adjusted_close": adj.adjusted_close if adj else candle.close,
                "volume": candle.volume,
                "turnover": candle.turnover,
                "adjustment_factor": adj.adjustment_factor if adj else 1,
                "is_incomplete": is_incomplete,
                "source": candle.source,
                "source_timestamp": candle.source_timestamp,
            }
        )
    _bulk_upsert(
        session,
        DailyPrice.__table__,
        rows,
        index_elements=["instrument_id", "trade_date"],
    )
    session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Fundamentals (raw statements)
# ---------------------------------------------------------------------------

def upsert_statements(
    session: Session,
    table,
    records: list,
    company_by_isin: dict[str, int],
    skip_missing_isin: bool = False,
) -> int:
    rows: list[dict] = []
    for rec in records:
        company_id = company_by_isin.get(rec.isin)
        if company_id is None:
            if not skip_missing_isin:
                log.warning("unknown isin %s; statement dropped", rec.isin)
            continue
        rows.append(
            {
                "company_id": company_id,
                "period_end": rec.period_end,
                "period_type": rec.period_type.value,
                "fiscal_year": rec.fiscal_year,
                "currency": rec.currency,
                "source": rec.source,
                **{
                    k: v
                    for k, v in rec.model_dump(exclude={"isin", "period_end",
                                                       "period_type", "fiscal_year",
                                                       "currency", "source",
                                                       "source_timestamp"}).items()
                },
            }
        )
    _bulk_upsert(
        session,
        table,
        rows,
        index_elements=["company_id", "period_end", "period_type", "source"],
    )
    session.flush()
    return len(rows)


def upsert_income_statements(
    session: Session, records: list[IncomeStatementRecord], company_by_isin: dict[str, int]
) -> int:
    return upsert_statements(session, IncomeStatement.__table__, records, company_by_isin)


def upsert_balance_sheets(
    session: Session, records: list[BalanceSheetRecord], company_by_isin: dict[str, int]
) -> int:
    return upsert_statements(session, BalanceSheet.__table__, records, company_by_isin)


def upsert_cash_flows(
    session: Session, records: list[CashFlowRecord], company_by_isin: dict[str, int]
) -> int:
    return upsert_statements(session, CashFlow.__table__, records, company_by_isin)


# ---------------------------------------------------------------------------
# Phase 2: metrics / benchmarks
# ---------------------------------------------------------------------------

def _clean_value(v):
    """Map float NaN/Inf to None so Numeric columns stay valid on Postgres."""
    if v is None:
        return None
    import math

    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def upsert_index_prices(
    session: Session,
    index_id: int,
    rows: Iterable[Mapping],
    source: str,
    source_timestamp: object = None,
) -> int:
    """Idempotently persist index closes keyed on (index, trade_date)."""
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "index_id": index_id,
                "trade_date": row["trade_date"],
                "open": _clean_value(row.get("open")),
                "high": _clean_value(row.get("high")),
                "low": _clean_value(row.get("low")),
                "close": _clean_value(row["close"]),
                "volume": row.get("volume"),
                "source": row.get("source", source),
                "source_timestamp": row.get("source_timestamp", source_timestamp),
            }
        )
    _bulk_upsert(
        session, IndexPrice.__table__, cleaned, index_elements=["index_id", "trade_date"]
    )
    session.flush()
    return len(cleaned)


def upsert_technical_indicators(
    session: Session,
    instrument_id: int,
    snapshots: Iterable[Mapping],
    calc_version: str,
) -> int:
    rows = [
        {**{"instrument_id": instrument_id, "calc_version": calc_version}, **snap}
        for snap in snapshots
    ]
    rows = [{k: _clean_value(v) for k, v in r.items()} for r in rows]
    _bulk_upsert(
        session,
        TechnicalIndicator.__table__,
        rows,
        index_elements=["instrument_id", "as_of", "calc_version"],
    )
    session.flush()
    return len(rows)


def upsert_momentum_metrics(
    session: Session,
    instrument_id: int,
    snapshots: Iterable[Mapping],
    calc_version: str,
) -> int:
    rows = [
        {**{"instrument_id": instrument_id, "calc_version": calc_version}, **snap}
        for snap in snapshots
    ]
    rows = [{k: _clean_value(v) for k, v in r.items()} for r in rows]
    _bulk_upsert(
        session,
        MomentumMetric.__table__,
        rows,
        index_elements=["instrument_id", "as_of", "calc_version"],
    )
    session.flush()
    return len(rows)


def upsert_relative_strength_metrics(
    session: Session,
    instrument_id: int,
    snapshots: Iterable[Mapping],
    benchmark: str,
    calc_version: str,
) -> int:
    rows = [
        {
            **{
                "instrument_id": instrument_id,
                "benchmark": benchmark,
                "calc_version": calc_version,
            },
            **snap,
        }
        for snap in snapshots
    ]
    rows = [{k: _clean_value(v) for k, v in r.items()} for r in rows]
    _bulk_upsert(
        session,
        RelativeStrengthMetric.__table__,
        rows,
        index_elements=["instrument_id", "as_of", "benchmark", "calc_version"],
    )
    session.flush()
    return len(rows)


def upsert_valuation_metrics(
    session: Session,
    instrument_id: int,
    snapshots: Iterable[Mapping],
    calc_version: str,
) -> int:
    rows = [
        {**{"instrument_id": instrument_id, "calc_version": calc_version}, **snap}
        for snap in snapshots
    ]
    rows = [{k: _clean_value(v) for k, v in r.items()} for r in rows]
    _bulk_upsert(
        session,
        ValuationMetric.__table__,
        rows,
        index_elements=["instrument_id", "as_of", "calc_version"],
    )
    session.flush()
    return len(rows)


def upsert_fundamental_metrics(
    session: Session, company_id: int, rows: Iterable[Mapping]
) -> int:
    cleaned = [
        {
            "company_id": company_id,
            "metric": row["metric"],
            "value": _clean_value(row.get("value")),
            "as_of": row["as_of"],
            "period_end": row.get("period_end"),
            "source": row.get("source", "engine"),
        }
        for row in rows
    ]
    _bulk_upsert(
        session,
        FundamentalMetric.__table__,
        cleaned,
        index_elements=["company_id", "metric", "as_of"],
    )
    session.flush()
    return len(cleaned)
