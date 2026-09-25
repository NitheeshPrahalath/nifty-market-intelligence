"""Metrics engine (Phase 2) + analysis engines (Phase 3) — service layer.

Orchestrates bulk runs with the same audit/traceability conventions as the
ingestion layer: every job writes an ``ingestion_runs`` row and per-instrument
failures become ``ingestion_errors`` rows instead of aborting the run.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nmi.analysis.horizon import compute_horizon_metrics
from nmi.analysis.regime import compute_market_regime, index_return
from nmi.analysis.scoring import DEFAULT_WEIGHTS, compute_scoring
from nmi.analysis.sector import compute_sector_metrics
from nmi.core.config import settings
from nmi.core.enums import RunStatus, Severity
from nmi.core.models import (
    BalanceSheet,
    CashFlow,
    Company,
    CorporateAction,
    CorporateActionType,
    DailyPrice,
    FundamentalMetric,
    HorizonMetric,
    IncomeStatement,
    Index,
    IndexMembership,
    IndexPrice,
    IngestionError,
    IngestionRun,
    Instrument,
    MomentumMetric,
    RelativeStrengthMetric,
    SectorMetric,
    TechnicalIndicator,
    ValuationMetric,
)
from nmi.indicators.fundamental import compute_fundamental_metrics
from nmi.indicators.momentum import compute_momentum, compute_relative_strength
from nmi.indicators.technical import compute_technical_indicators
from nmi.indicators.valuation import compute_valuation_metrics
from nmi.ingestion import store
from nmi.ingestion.backfill import resolve_members
from nmi.ingestion.providers.base import index_price_provider

_TECHNICAL_FIELDS = (
    "sma20",
    "sma50",
    "sma200",
    "rsi14",
    "macd_hist",
    "adx14",
    "roc10",
    "hist_vol_20",
    "hist_vol_60",
    "drawdown_pct",
    "dist_from_high_52w_pct",
    "dist_from_low_52w_pct",
    "volume_ratio",
    "trend_state",
)
_MOMENTUM_FIELDS = ("return_1m", "return_3m", "return_6m", "return_12m")
_RS_FIELDS = ("rs_1m", "rs_3m", "rs_6m", "rs_12m", "rs_trend")
_VALUATION_FIELDS = (
    "pe",
    "pb",
    "pe_percentile_3y",
    "pb_percentile_3y",
    "ev_ebitda_percentile_3y",
    "valuation_label",
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class JobResult:
    run_id: int
    job_name: str
    status: RunStatus
    items_processed: int = 0
    items_failed: int = 0
    per_symbol: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "status": self.status.value,
            "items_processed": self.items_processed,
            "items_failed": self.items_failed,
            "per_symbol": self.per_symbol,
        }


def _prices_for(
    session: Session, instrument_id: int, start: date | None, end: date | None
) -> list[DailyPrice]:
    stmt = select(DailyPrice).where(DailyPrice.instrument_id == instrument_id)
    if start:
        stmt = stmt.where(DailyPrice.trade_date >= start)
    if end:
        stmt = stmt.where(DailyPrice.trade_date <= end)
    stmt = stmt.order_by(DailyPrice.trade_date)
    return list(session.scalars(stmt).all())


def _benchmark_prices(session: Session, code: str) -> list[IndexPrice]:
    stmt = (
        select(IndexPrice)
        .join(Index, Index.id == IndexPrice.index_id)
        .where(Index.code == code)
        .order_by(IndexPrice.trade_date)
    )
    return list(session.scalars(stmt).all())


def _row_dict(row, fields: Sequence[str]) -> dict:
    """Snapshot the requested columns, unwrapping enums to their values."""
    out = {}
    for field_name in fields:
        value = getattr(row, field_name, None)
        out[field_name] = getattr(value, "value", value)
    return out


def _load_metric_rows(
    session: Session,
    model,
    instrument_ids: Sequence[int],
    start: date,
    end: date,
    calc_version: str,
    fields: Sequence[str],
    benchmark: str | None = None,
) -> dict[tuple[int, date], dict]:
    if not instrument_ids:
        return {}
    stmt = select(model).where(
        model.instrument_id.in_(instrument_ids),
        model.as_of >= start,
        model.as_of <= end,
        model.calc_version == calc_version,
    )
    if benchmark is not None:
        stmt = stmt.where(model.benchmark == benchmark)
    return {
        (row.instrument_id, row.as_of): _row_dict(row, fields)
        for row in session.scalars(stmt).all()
    }


def _load_closes(
    session: Session, instrument_ids: Sequence[int], start: date, end: date
) -> dict[tuple[int, date], float]:
    if not instrument_ids:
        return {}
    stmt = select(DailyPrice.trade_date, DailyPrice.instrument_id, DailyPrice.close).where(
        DailyPrice.instrument_id.in_(instrument_ids),
        DailyPrice.trade_date >= start,
        DailyPrice.trade_date <= end,
    )
    return {(iid, d): float(c) for d, iid, c in session.execute(stmt).all() if c is not None}


def _load_fundamentals(
    session: Session, company_ids: Sequence[int]
) -> dict[int, dict[str, list[tuple[date, float | None]]]]:
    if not company_ids:
        return {}
    stmt = select(
        FundamentalMetric.company_id,
        FundamentalMetric.metric,
        FundamentalMetric.as_of,
        FundamentalMetric.value,
    ).where(FundamentalMetric.company_id.in_(company_ids))
    grouped: dict[int, dict[str, list[tuple[date, float | None]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for company_id, metric, as_of, value in session.execute(stmt).all():
        grouped[company_id][metric].append((as_of, value))
    for metrics in grouped.values():
        for series in metrics.values():
            series.sort(key=lambda item: item[0])
    return {company_id: dict(metrics) for company_id, metrics in grouped.items()}


def _membership_intervals(
    session: Session, index_codes: Sequence[str], start: date, end: date
) -> dict[int, list[tuple[date, date | None, int]]]:
    stmt = (
        select(IndexMembership)
        .join(Index, Index.id == IndexMembership.index_id)
        .where(
            Index.code.in_(index_codes),
            IndexMembership.effective_from <= end,
        )
    )
    out: dict[int, list[tuple[date, date | None, int]]] = defaultdict(list)
    for row in session.scalars(stmt).all():
        if row.effective_to is not None and row.effective_to < start:
            continue
        out[row.index_id].append((row.effective_from, row.effective_to, row.instrument_id))
    return dict(out)


def _members_on(intervals: Sequence[tuple[date, date | None, int]], as_of: date) -> list[int]:
    """Instrument ids whose membership interval covers ``as_of``."""
    return [
        instrument_id
        for effective_from, effective_to, instrument_id in intervals
        if effective_from <= as_of and (effective_to is None or effective_to >= as_of)
    ]


def _member_instrument_ids(
    intervals: dict[int, list[tuple[date, date | None, int]]]
) -> set[int]:
    """Every instrument with a membership interval overlapping the window."""
    return {
        instrument_id
        for rows in intervals.values()
        for (_from, _to, instrument_id) in rows
    }


@dataclass(slots=True)
class _AnalysisInputs:
    """Phase-2 metric rows loaded once and reused by every Phase-3 engine."""

    index_ids: dict[str, int]
    intervals: dict[int, list[tuple[date, date | None, int]]]
    company_by_instrument: dict[int, int]
    sector_by_instrument: dict[int, int | None]
    technical: dict[tuple[int, date], dict]
    closes: dict[tuple[int, date], float]
    momentum: dict[tuple[int, date], dict]
    rs: dict[tuple[int, date], dict]
    valuation: dict[tuple[int, date], dict]
    fundamentals: dict[int, dict[str, list[tuple[date, float | None]]]]
    start: date
    end: date

    def as_of_dates(self) -> list[date]:
        return sorted({as_of for _iid, as_of in self.technical})

    def technical_at(self, instrument_id: int, as_of: date) -> dict | None:
        row = self.technical.get((instrument_id, as_of))
        if row is None:
            return None
        return {
            **row,
            "as_of": as_of,
            "close": self.closes.get((instrument_id, as_of)),
        }

    def fundamentals_at(self, company_id: int, as_of: date) -> dict:
        metrics = self.fundamentals.get(company_id, {})
        out: dict[str, float | None] = {}
        for metric, series in metrics.items():
            idx = bisect_right([d for d, _v in series], as_of) - 1
            if idx >= 0:
                out[metric] = series[idx][1]
        return out


class MetricsService:
    def __init__(self, session: Session):
        self.session = session
        self.calc_version = settings.calc_version

    # ------------------------------------------------------------ benchmarks
    def backfill_index_prices(self, index_codes: Sequence[str]) -> JobResult:
        provider = index_price_provider()
        run = self._start_run("backfill_index_prices", {"source": provider.name})
        result = JobResult(
            run_id=run.id, job_name="backfill_index_prices", status=RunStatus.RUNNING
        )
        try:
            for code in index_codes:
                index_id = store.ensure_indices(self.session, [code]).get(code)
                records = provider.fetch_index_prices(code)
                rows = [r.model_dump() for r in records]
                stored = store.upsert_index_prices(
                    self.session,
                    index_id,
                    rows,
                    source=provider.name,
                    source_timestamp=datetime.utcnow(),
                )
                result.items_processed += stored
                result.per_symbol.append({"index": code, "stored": stored})
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # ---------------------------------------------------------- fundamentals
    def compute_fundamentals(
        self, index_codes: Sequence[str], isins: Sequence[str] | None = None
    ) -> JobResult:
        run = self._start_run(
            "compute_fundamentals",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(
            run_id=run.id, job_name="compute_fundamentals", status=RunStatus.RUNNING
        )
        try:
            instruments = resolve_members(
                self.session, index_codes, date(1900, 1, 1), date(2900, 1, 1)
            )
            company_ids = sorted({i.company_id for i in instruments})
            if isins:
                known = set(
                    self.session.scalars(
                        select(Company.id).where(Company.isin.in_(isins))
                    ).all()
                )
                company_ids = [c for c in company_ids if c in known]
            for company_id in company_ids:
                try:
                    company = self.session.get(Company, company_id)
                    income = list(
                        self.session.scalars(
                            select(IncomeStatement).where(IncomeStatement.company_id == company_id)
                        ).all()
                    )
                    balance = list(
                        self.session.scalars(
                            select(BalanceSheet).where(BalanceSheet.company_id == company_id)
                        ).all()
                    )
                    cash = list(
                        self.session.scalars(
                            select(CashFlow).where(CashFlow.company_id == company_id)
                        ).all()
                    )
                    rows = compute_fundamental_metrics(income, balance, cash)
                    stored = store.upsert_fundamental_metrics(self.session, company_id, rows)
                    result.items_processed += stored
                    result.per_symbol.append({"isin": company.isin, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id, "fundamental", Severity.ERROR, "COMPUTE_FAILED",
                        f"company {company_id}: {type(exc).__name__}: {exc}",
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------ technical
    def compute_technical(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        run = self._start_run(
            "compute_technical",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(
            run_id=run.id, job_name="compute_technical", status=RunStatus.RUNNING
        )
        try:
            instruments = resolve_members(
                self.session, index_codes, start or date(1900, 1, 1), end or date(2900, 1, 1)
            )
            for instrument in instruments:
                try:
                    prices = _prices_for(self.session, instrument.id, start, end)
                    snapshots = compute_technical_indicators(prices)
                    stored = store.upsert_technical_indicators(
                        self.session, instrument.id, snapshots, self.calc_version
                    )
                    result.items_processed += stored
                    result.per_symbol.append({"symbol": instrument.symbol, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id, "technical", Severity.ERROR, "COMPUTE_FAILED",
                        f"{instrument.symbol}: {type(exc).__name__}: {exc}",
                        instrument_id=instrument.id,
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------ momentum/RS
    def compute_momentum(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        run = self._start_run(
            "compute_momentum",
            {
                "index_codes": list(index_codes),
                "benchmarks": list(settings.rs_benchmarks),
                "calc_version": self.calc_version,
            },
        )
        result = JobResult(
            run_id=run.id, job_name="compute_momentum", status=RunStatus.RUNNING
        )
        try:
            benchmark_map = {
                code: _benchmark_prices(self.session, code) for code in settings.rs_benchmarks
            }
            instruments = resolve_members(
                self.session, index_codes, start or date(1900, 1, 1), end or date(2900, 1, 1)
            )
            for instrument in instruments:
                try:
                    prices = _prices_for(self.session, instrument.id, start, end)
                    mom = compute_momentum(prices)
                    stored = store.upsert_momentum_metrics(
                        self.session, instrument.id, mom, self.calc_version
                    )
                    result.items_processed += stored
                    for code, bench in benchmark_map.items():
                        rs = compute_relative_strength(
                            prices,
                            bench,
                            trend_threshold_pp=settings.rs_trend_threshold_pp,
                        )
                        store.upsert_relative_strength_metrics(
                            self.session,
                            instrument.id,
                            rs,
                            benchmark=code,
                            calc_version=self.calc_version,
                        )
                    result.per_symbol.append({"symbol": instrument.symbol, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id, "momentum", Severity.ERROR, "COMPUTE_FAILED",
                        f"{instrument.symbol}: {type(exc).__name__}: {exc}",
                        instrument_id=instrument.id,
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------ valuation
    def compute_valuation(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        run = self._start_run(
            "compute_valuation",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(
            run_id=run.id, job_name="compute_valuation", status=RunStatus.RUNNING
        )
        try:
            instruments = resolve_members(
                self.session, index_codes, start or date(1900, 1, 1), end or date(2900, 1, 1)
            )
            by_company: dict[int, list] = {}
            for inst in instruments:
                by_company.setdefault(inst.company_id, []).append(inst)
            fund_cache: dict[int, dict] = {}
            for company_id, insts in by_company.items():
                fund_cache[company_id] = {
                    "income": list(
                        self.session.scalars(
                            select(IncomeStatement).where(IncomeStatement.company_id == company_id)
                        ).all()
                    ),
                    "balance": list(
                        self.session.scalars(
                            select(BalanceSheet).where(BalanceSheet.company_id == company_id)
                        ).all()
                    ),
                    "cash": list(
                        self.session.scalars(
                            select(CashFlow).where(CashFlow.company_id == company_id)
                        ).all()
                    ),
                }
                for instrument in insts:
                    try:
                        prices = _prices_for(self.session, instrument.id, start, end)
                        dividends = list(
                            self.session.scalars(
                                select(CorporateAction).where(
                                    CorporateAction.instrument_id == instrument.id,
                                    CorporateAction.action_type == CorporateActionType.DIVIDEND,
                                )
                            ).all()
                        )
                        snapshots = compute_valuation_metrics(
                            prices,
                            fund_cache[company_id]["income"],
                            fund_cache[company_id]["balance"],
                            fund_cache[company_id]["cash"],
                            dividends,
                        )
                        stored = store.upsert_valuation_metrics(
                            self.session, instrument.id, snapshots, self.calc_version
                        )
                        result.items_processed += stored
                        result.per_symbol.append({"symbol": instrument.symbol, "stored": stored})
                    except Exception as exc:  # noqa: BLE001
                        result.items_failed += 1
                        self._record_error(
                            run.id, "valuation", Severity.ERROR, "COMPUTE_FAILED",
                            f"{instrument.symbol}: {type(exc).__name__}: {exc}",
                            instrument_id=instrument.id,
                        )
                        self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------------ P3
    def _analysis_inputs(
        self, index_codes: Sequence[str], start: date | None, end: date | None
    ) -> _AnalysisInputs:
        """Load every Phase-2 metric row the Phase-3 engines need, once."""
        start = start or date(1900, 1, 1)
        end = end or date(2900, 1, 1)
        index_ids = store.ensure_indices(self.session, list(index_codes))
        intervals = _membership_intervals(self.session, index_codes, start, end)
        instrument_ids = sorted({iid for rows in intervals.values() for (_f, _t, iid) in rows})

        company_by_instrument: dict[int, int] = {}
        sector_by_instrument: dict[int, int | None] = {}
        if instrument_ids:
            rows = self.session.execute(
                select(Instrument.id, Instrument.company_id, Company.sector_id)
                .join(Company, Company.id == Instrument.company_id)
                .where(Instrument.id.in_(instrument_ids))
            ).all()
            company_by_instrument = {iid: cid for iid, cid, _s in rows}
            sector_by_instrument = {iid: sector for iid, _c, sector in rows}

        benchmark = settings.rs_benchmarks[0] if settings.rs_benchmarks else None
        return _AnalysisInputs(
            index_ids=index_ids,
            intervals=intervals,
            company_by_instrument=company_by_instrument,
            sector_by_instrument=sector_by_instrument,
            technical=_load_metric_rows(
                self.session,
                TechnicalIndicator,
                instrument_ids,
                start,
                end,
                self.calc_version,
                _TECHNICAL_FIELDS,
            ),
            closes=_load_closes(self.session, instrument_ids, start, end),
            momentum=_load_metric_rows(
                self.session,
                MomentumMetric,
                instrument_ids,
                start,
                end,
                self.calc_version,
                _MOMENTUM_FIELDS,
            ),
            rs=_load_metric_rows(
                self.session,
                RelativeStrengthMetric,
                instrument_ids,
                start,
                end,
                self.calc_version,
                _RS_FIELDS,
                benchmark=benchmark,
            ),
            valuation=_load_metric_rows(
                self.session,
                ValuationMetric,
                instrument_ids,
                start,
                end,
                self.calc_version,
                _VALUATION_FIELDS,
            ),
            fundamentals=_load_fundamentals(
                self.session, sorted(set(company_by_instrument.values()))
            ),
            start=start,
            end=end,
        )

    def compute_sector(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        """Aggregate each sector's cross-section per as-of day."""
        run = self._start_run(
            "compute_sector",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(run_id=run.id, job_name="compute_sector", status=RunStatus.RUNNING)
        try:
            inputs = self._analysis_inputs(index_codes, start, end)
            for code in index_codes:
                index_id = inputs.index_ids.get(code)
                if index_id is None:
                    continue
                series = [
                    (r.trade_date, float(r.close)) for r in _benchmark_prices(self.session, code)
                ]
                rows: list[dict] = []
                for as_of in inputs.as_of_dates():
                    members_by_sector: dict[object, list[dict]] = {}
                    for instrument_id in _members_on(inputs.intervals.get(index_id, []), as_of):
                        technical = inputs.technical_at(instrument_id, as_of)
                        sector_id = inputs.sector_by_instrument.get(instrument_id)
                        if technical is None or sector_id is None:
                            continue
                        record = {
                            **technical,
                            **(inputs.momentum.get((instrument_id, as_of)) or {}),
                            **(inputs.rs.get((instrument_id, as_of)) or {}),
                        }
                        members_by_sector.setdefault(sector_id, []).append(record)
                    if not members_by_sector:
                        continue
                    rows.extend(
                        compute_sector_metrics(
                            as_of,
                            members_by_sector,
                            index_return_3m=index_return(series, as_of, 63),
                        )
                    )
                stored = store.upsert_sector_metrics(
                    self.session, index_id, rows, self.calc_version
                )
                result.items_processed += stored
                result.per_symbol.append({"index": code, "stored": stored})
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def compute_regime(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        """Score the market regime (breadth, momentum, vol, drawdown, sectors)."""
        run = self._start_run(
            "compute_regime",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(run_id=run.id, job_name="compute_regime", status=RunStatus.RUNNING)
        try:
            inputs = self._analysis_inputs(index_codes, start, end)
            for code in index_codes:
                index_id = inputs.index_ids.get(code)
                if index_id is None:
                    continue
                series = [
                    (r.trade_date, float(r.close)) for r in _benchmark_prices(self.session, code)
                ]
                member_technicals: dict[date, list[dict]] = {}
                participation: dict[date, float] = {}
                for as_of in inputs.as_of_dates():
                    members: list[dict] = []
                    sector_flags: dict[object, list[bool]] = defaultdict(list)
                    for instrument_id in _members_on(inputs.intervals.get(index_id, []), as_of):
                        technical = inputs.technical_at(instrument_id, as_of)
                        if technical is None:
                            continue
                        members.append(
                            {
                                key: technical.get(key)
                                for key in ("close", "sma20", "sma50", "sma200")
                            }
                        )
                        sector_id = inputs.sector_by_instrument.get(instrument_id)
                        close = technical.get("close")
                        sma50 = technical.get("sma50")
                        if sector_id is not None and close is not None and sma50 is not None:
                            sector_flags[sector_id].append(close > sma50)
                    if members:
                        member_technicals[as_of] = members
                    if sector_flags:
                        participating = sum(
                            1 for flags in sector_flags.values() if sum(flags) * 2 >= len(flags)
                        )
                        participation[as_of] = participating / len(sector_flags) * 100

                rows = [
                    row
                    for row in compute_market_regime(series, member_technicals, participation)
                    if inputs.start <= row["as_of"] <= inputs.end
                ]
                stored = store.upsert_market_regimes(
                    self.session, index_id, rows, self.calc_version
                )
                result.items_processed += stored
                result.per_symbol.append({"index": code, "stored": stored})
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def compute_horizon(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        """Score short/medium/long-horizon fit for every member instrument."""
        run = self._start_run(
            "compute_horizon",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(run_id=run.id, job_name="compute_horizon", status=RunStatus.RUNNING)
        try:
            inputs = self._analysis_inputs(index_codes, start, end)
            members = _member_instrument_ids(inputs.intervals)
            for instrument_id in sorted(members):
                try:
                    rows: list[dict] = []
                    for as_of in inputs.as_of_dates():
                        technical = inputs.technical_at(instrument_id, as_of)
                        if technical is None:
                            continue
                        row = compute_horizon_metrics(
                            technical,
                            inputs.momentum.get((instrument_id, as_of)),
                            inputs.rs.get((instrument_id, as_of)),
                        )
                        if row:
                            rows.append(row)
                    stored = store.upsert_horizon_metrics(
                        self.session, instrument_id, rows, self.calc_version
                    )
                    result.items_processed += stored
                    result.per_symbol.append({"instrument_id": instrument_id, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id,
                        "horizon",
                        Severity.ERROR,
                        "COMPUTE_FAILED",
                        f"instrument {instrument_id}: {type(exc).__name__}: {exc}",
                        instrument_id=instrument_id,
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def seed_strategy_parameters(self, parameter_set: str | None = None) -> int:
        """Persist the default component weights so scores are DB-reproducible."""
        parameter_set = parameter_set or settings.scoring_parameter_set
        return store.upsert_strategy_parameters(self.session, parameter_set, DEFAULT_WEIGHTS)

    def compute_scoring(
        self,
        index_codes: Sequence[str],
        start: date | None = None,
        end: date | None = None,
        parameter_set: str | None = None,
    ) -> JobResult:
        """Eight component scores plus the weighted composite per member-day."""
        parameter_set = parameter_set or settings.scoring_parameter_set
        run = self._start_run(
            "compute_scoring",
            {
                "index_codes": list(index_codes),
                "calc_version": self.calc_version,
                "parameter_set": parameter_set,
            },
        )
        result = JobResult(run_id=run.id, job_name="compute_scoring", status=RunStatus.RUNNING)
        try:
            weights = store.get_strategy_weights(self.session, parameter_set)
            if not weights:
                self.seed_strategy_parameters(parameter_set)
                self.session.commit()
                weights = store.get_strategy_weights(self.session, parameter_set) or dict(
                    DEFAULT_WEIGHTS
                )

            inputs = self._analysis_inputs(index_codes, start, end)
            horizon_rows = self._horizon_map(inputs)
            sector_scores = self._sector_score_map(index_codes, inputs.start, inputs.end)

            for instrument_id in sorted(_member_instrument_ids(inputs.intervals)):
                try:
                    rows: list[dict] = []
                    company_id = inputs.company_by_instrument[instrument_id]
                    for as_of in inputs.as_of_dates():
                        technical = inputs.technical_at(instrument_id, as_of)
                        if technical is None:
                            continue
                        index_id = self._primary_index(instrument_id, as_of, index_codes, inputs)
                        sector_id = inputs.sector_by_instrument.get(instrument_id)
                        sector_score = (
                            sector_scores.get((index_id, sector_id, as_of))
                            if index_id is not None and sector_id is not None
                            else None
                        )
                        rows.append(
                            compute_scoring(
                                as_of,
                                technical,
                                inputs.momentum.get((instrument_id, as_of)),
                                inputs.rs.get((instrument_id, as_of)),
                                inputs.valuation.get((instrument_id, as_of)),
                                inputs.fundamentals_at(company_id, as_of),
                                horizon_rows.get((instrument_id, as_of))
                                or compute_horizon_metrics(
                                    technical,
                                    inputs.momentum.get((instrument_id, as_of)),
                                    inputs.rs.get((instrument_id, as_of)),
                                ),
                                sector_score,
                                weights,
                                parameter_set,
                            )
                        )
                    stored = store.upsert_scoring_snapshots(
                        self.session, instrument_id, rows, self.calc_version
                    )
                    result.items_processed += stored
                    result.per_symbol.append({"instrument_id": instrument_id, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id,
                        "scoring",
                        Severity.ERROR,
                        "COMPUTE_FAILED",
                        f"instrument {instrument_id}: {type(exc).__name__}: {exc}",
                        instrument_id=instrument_id,
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def _horizon_map(self, inputs: _AnalysisInputs) -> dict[tuple[int, date], dict]:
        if not inputs.company_by_instrument:
            return {}
        stmt = select(HorizonMetric).where(
            HorizonMetric.instrument_id.in_(list(inputs.company_by_instrument)),
            HorizonMetric.as_of >= inputs.start,
            HorizonMetric.as_of <= inputs.end,
            HorizonMetric.calc_version == self.calc_version,
        )
        fields = (
            "short_term_score",
            "medium_term_score",
            "long_term_score",
            "preferred_horizon",
            "horizon_confidence",
        )
        return {
            (row.instrument_id, row.as_of): _row_dict(row, fields)
            for row in self.session.scalars(stmt).all()
        }

    def _sector_score_map(
        self, index_codes: Sequence[str], start: date, end: date
    ) -> dict[tuple[int, object, date], float]:
        stmt = select(SectorMetric).where(SectorMetric.as_of >= start, SectorMetric.as_of <= end)
        if not index_codes:
            return {}
        return {
            (row.index_id, row.sector_id, row.as_of): float(row.sector_score)
            for row in self.session.scalars(stmt).all()
            if row.sector_score is not None
        }

    def _primary_index(
        self,
        instrument_id: int,
        as_of: date,
        index_codes: Sequence[str],
        inputs: _AnalysisInputs,
    ) -> int | None:
        for code in index_codes:
            index_id = inputs.index_ids.get(code)
            if index_id is None:
                continue
            if instrument_id in _members_on(inputs.intervals.get(index_id, []), as_of):
                return index_id
        return None

    # --------------------------------------------------------------- combined
    def compute_eod(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> list[JobResult]:
        """Full EOD refresh in dependency order.

        Phase 2 (fundamentals, technical, momentum, valuation) feeds Phase 3
        (sector aggregates, market regime, horizon, scoring).
        """
        return [
            self.compute_fundamentals(index_codes),
            self.compute_technical(index_codes, start, end),
            self.compute_momentum(index_codes, start, end),
            self.compute_valuation(index_codes, start, end),
            self.compute_sector(index_codes, start, end),
            self.compute_regime(index_codes, start, end),
            self.compute_horizon(index_codes, start, end),
            self.compute_scoring(index_codes, start, end),
        ]

    # ---------------------------------------------------------------- audit
    def _start_run(self, job_name: str, config: dict) -> IngestionRun:
        run = IngestionRun(
            job_name=job_name,
            status=RunStatus.RUNNING,
            started_at=datetime.utcnow(),
            config={**config, "calc_version": self.calc_version},
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def _finish_run(self, run: IngestionRun, result: JobResult) -> None:
        run.status = RunStatus.SUCCEEDED
        run.finished_at = datetime.utcnow()
        run.items_processed = result.items_processed
        run.items_failed = result.items_failed
        run.error_summary = (
            f"{result.items_failed} flagged issue(s)" if result.items_failed else None
        )
        self.session.commit()
        result.status = RunStatus.SUCCEEDED
        log.info(
            "run %s finished: processed=%s failed=%s",
            run.job_name,
            result.items_processed,
            result.items_failed,
        )

    def _fail_run(self, run: IngestionRun, result: JobResult, exc: Exception) -> None:
        run.status = RunStatus.FAILED
        run.finished_at = datetime.utcnow()
        run.error_summary = f"{type(exc).__name__}: {exc}"
        self.session.commit()
        result.status = RunStatus.FAILED
        log.error("run %s failed: %s", run.job_name, exc)

    def _record_error(
        self,
        run_id: int,
        stage: str,
        severity: Severity,
        code: str,
        message: str,
        instrument_id: int | None = None,
        trade_date: date | None = None,
    ) -> None:
        self.session.add(
            IngestionError(
                run_id=run_id,
                stage=stage,
                severity=severity,
                code=code,
                message=message,
                instrument_id=instrument_id,
                trade_date=trade_date,
            )
        )
