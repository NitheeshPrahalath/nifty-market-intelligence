"""Metrics engine (Phase 2) + analysis engines (Phase 3) + strategies (Phase 4)
+ recommendation tracking, exit and notification engines (Phase 5).

Orchestrates bulk runs with the same audit/traceability conventions as the
ingestion layer: every job writes an ``ingestion_runs`` row and per-instrument
failures become ``ingestion_errors`` rows instead of aborting the run.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nmi.analysis.common import as_float, round_or_none
from nmi.analysis.horizon import compute_horizon_metrics
from nmi.analysis.inputs import (
    AnalysisInputs,
    load_analysis_inputs,
    member_instrument_ids,
    members_on,
    membership_intervals,
    regime_map,
    row_dict,
    scoring_map,
    sector_state_map,
)
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
    EventType,
    HorizonMetric,
    IncomeStatement,
    Index,
    IndexPrice,
    IngestionError,
    IngestionRun,
    Instrument,
    MarketRegime,
    Recommendation,
    RecommendationEvent,
    RecommendationState,
    RecommendationVersion,
    SectorMetric,
    Signal,
    Strategy,
    StrategyVersion,
)
from nmi.indicators.fundamental import compute_fundamental_metrics
from nmi.indicators.momentum import compute_momentum, compute_relative_strength
from nmi.indicators.technical import compute_technical_indicators
from nmi.indicators.valuation import compute_valuation_metrics
from nmi.ingestion import store
from nmi.ingestion.backfill import resolve_members
from nmi.ingestion.providers.base import index_price_provider
from nmi.strategies import (
    ENGINE_VERSION,
    StrategyContext,
    StrategyMeta,
    catalog_payloads,
    evaluate_strategy,
)
from nmi.strategies.rules import StrategyRules
from nmi.tracking import (
    DEFAULT_EXIT_POLICY,
    TRACKING_VERSION,
    ChangeDirection,
    ExitAction,
    ExitContext,
    ExitMechanism,
    ExitTrigger,
    LifecycleDecision,
    NotificationDraft,
    ThesisAssessment,
    ThesisChange,
    ThesisSnapshot,
    assessment_reason,
    build_snapshot,
    close_decision,
    compare,
    deduplicate,
    evaluate_exits,
    from_event,
    new_recommendation_draft,
    next_state,
    regime_change_draft,
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


def _parse_day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _assessment_from_detail(detail: Mapping | None) -> ThesisAssessment | None:
    """Rebuild a stored thesis assessment (event detail -> object)."""
    if not detail:
        return None
    changes = tuple(
        ThesisChange(
            key=item.get("key", ""),
            label=item.get("label", item.get("key", "")),
            group=item.get("group", "score"),
            before=item.get("before"),
            after=item.get("after"),
            direction=ChangeDirection(item.get("direction", "UNCHANGED")),
            before_text=str(item.get("before_text", "n/a")),
            after_text=str(item.get("after_text", "n/a")),
        )
        for item in detail.get("changes") or []
    )
    return ThesisAssessment(
        baseline_as_of=_parse_day(detail.get("baseline_as_of")),
        current_as_of=_parse_day(detail.get("current_as_of")),
        changes=changes,
    )


def _triggers_from_detail(detail: Sequence | None) -> list[ExitTrigger]:
    """Rebuild the exit triggers stored on an event."""
    return [
        ExitTrigger(
            mechanism=ExitMechanism(item["mechanism"]),
            action=ExitAction(item["action"]),
            reason=item["reason"],
            detail=item.get("detail") or {},
        )
        for item in (detail or [])
    ]


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
    ) -> AnalysisInputs:
        """Load every Phase-2 metric row the analysis engines need, once."""
        return load_analysis_inputs(
            self.session, index_codes, start, end, self.calc_version
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
                    for instrument_id in members_on(inputs.intervals.get(index_id, []), as_of):
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
                    for instrument_id in members_on(inputs.intervals.get(index_id, []), as_of):
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
            members = member_instrument_ids(inputs.intervals)
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

            for instrument_id in sorted(member_instrument_ids(inputs.intervals)):
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

    def _horizon_map(self, inputs: AnalysisInputs) -> dict[tuple[int, date], dict]:
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
            (row.instrument_id, row.as_of): row_dict(row, fields)
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
        inputs: AnalysisInputs,
    ) -> int | None:
        for code in index_codes:
            index_id = inputs.index_ids.get(code)
            if index_id is None:
                continue
            if instrument_id in members_on(inputs.intervals.get(index_id, []), as_of):
                return index_id
        return None

    # -------------------------------------------------- strategies (Phase 4)
    def seed_strategies(self) -> int:
        """Persist the default strategy catalog; a changed rule document
        becomes a new immutable strategy version. Returns versions created."""
        stored = 0
        for payload in catalog_payloads():
            store.upsert_strategies(self.session, [payload["strategy"]])
            strategy = store.get_strategy_by_code(self.session, payload["strategy"]["code"])
            if strategy is None:
                continue
            current = store.get_current_strategy_version(self.session, strategy.id)
            if current is not None and current.rules == payload["rules"]:
                continue
            latest = self.session.scalar(
                select(func.max(StrategyVersion.version)).where(
                    StrategyVersion.strategy_id == strategy.id
                )
            )
            if current is not None:
                current.is_current = False
            store.upsert_strategy_versions(
                self.session,
                strategy.id,
                [
                    {
                        "version": (latest or 0) + 1,
                        "engine_version": ENGINE_VERSION,
                        "rules": payload["rules"],
                        "notes": "seeded from nmi.strategies.catalog",
                        "is_current": True,
                    }
                ],
            )
            stored += 1
        self.session.flush()
        return stored

    def _active_strategies(
        self,
    ) -> list[tuple[Strategy, StrategyVersion, StrategyRules]]:
        active = store.get_active_strategy_versions(self.session)
        if not active:
            self.seed_strategies()
            self.session.flush()
            active = store.get_active_strategy_versions(self.session)
        return [
            (strategy, version, StrategyRules.from_dict(version.rules))
            for strategy, version in active
        ]

    def _scoring_map(self, inputs: AnalysisInputs) -> dict[tuple[int, date], dict]:
        return scoring_map(
            self.session,
            list(inputs.company_by_instrument),
            inputs.start,
            inputs.end,
            self.calc_version,
        )

    def _regime_map(self, start: date, end: date) -> dict[tuple[int, date], dict]:
        return regime_map(self.session, start, end, self.calc_version)

    def _sector_state_map(
        self, index_codes: Sequence[str], start: date, end: date
    ) -> dict[tuple[int, object, date], str]:
        return sector_state_map(self.session, index_codes, start, end)

    def _signal_context(
        self,
        inputs: AnalysisInputs,
        instrument_id: int,
        as_of: date,
        scoring: dict | None,
        regime: dict | None,
        sector: dict | None,
    ) -> StrategyContext | None:
        return inputs.strategy_context(instrument_id, as_of, scoring, regime, sector)

    def compute_signals(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> JobResult:
        """Evaluate every active strategy version for every member-day."""
        run = self._start_run(
            "compute_signals",
            {"index_codes": list(index_codes), "calc_version": self.calc_version},
        )
        result = JobResult(run_id=run.id, job_name="compute_signals", status=RunStatus.RUNNING)
        try:
            strategies = self._active_strategies()
            if not strategies:
                self.session.commit()
                self._finish_run(run, result)
                return result

            inputs = self._analysis_inputs(index_codes, start, end)
            scoring_rows = self._scoring_map(inputs)
            regime_rows = self._regime_map(inputs.start, inputs.end)
            sector_states = self._sector_state_map(index_codes, inputs.start, inputs.end)

            for instrument_id in sorted(member_instrument_ids(inputs.intervals)):
                try:
                    rows: list[dict] = []
                    for as_of in inputs.as_of_dates():
                        index_id = self._primary_index(instrument_id, as_of, index_codes, inputs)
                        sector_id = inputs.sector_by_instrument.get(instrument_id)
                        sector_state = sector_states.get((index_id, sector_id, as_of))
                        ctx = self._signal_context(
                            inputs,
                            instrument_id,
                            as_of,
                            scoring_rows.get((instrument_id, as_of)),
                            regime_rows.get((index_id, as_of)) if index_id is not None else None,
                            {"sector_state": sector_state} if sector_state else None,
                        )
                        if ctx is None:
                            continue
                        for strategy, version, rules in strategies:
                            meta = StrategyMeta(
                                code=strategy.code,
                                name=strategy.name,
                                horizon=getattr(strategy.horizon, "value", strategy.horizon),
                                version=version.version,
                            )
                            decision = evaluate_strategy(meta, rules, ctx)
                            rows.append(
                                {
                                    "instrument_id": instrument_id,
                                    "strategy_version_id": version.id,
                                    "as_of": as_of,
                                    "calc_version": self.calc_version,
                                    **decision.as_signal_columns(),
                                }
                            )
                    stored = store.upsert_signals(self.session, rows)
                    result.items_processed += stored
                    result.per_symbol.append({"instrument_id": instrument_id, "stored": stored})
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id,
                        "signals",
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

    def generate_recommendations(
        self, index_codes: Sequence[str], as_of: date | None = None
    ) -> JobResult:
        """Turn qualifying signals into tracked recommendations.

        A recommendation is created once per instrument-strategy pair; the
        original is never overwritten — Phase 5 appends new versions to
        ``recommendation_versions`` as the thesis evolves.
        """
        run = self._start_run("generate_recommendations", {"as_of": str(as_of) if as_of else None})
        result = JobResult(
            run_id=run.id, job_name="generate_recommendations", status=RunStatus.RUNNING
        )
        try:
            strategies = self._active_strategies()
            if not strategies:
                self.session.commit()
                self._finish_run(run, result)
                return result
            if as_of is None:
                as_of = self.session.scalar(
                    select(func.max(Signal.as_of)).where(Signal.calc_version == self.calc_version)
                )
            if as_of is None:
                self.session.commit()
                self._finish_run(run, result)
                return result

            by_version = {version.id: (strategy, version) for strategy, version, _r in strategies}
            signals = store.get_signals_on(
                self.session, as_of, [version.id for _s, version, _r in strategies]
            )
            inputs = self._analysis_inputs(index_codes, None, as_of)
            scoring_rows = self._scoring_map(inputs)
            regime_rows = self._regime_map(inputs.start, as_of)
            sector_states = self._sector_state_map(index_codes, inputs.start, as_of)
            for signal in signals:
                pair = by_version.get(signal.strategy_version_id)
                if pair is None:
                    continue
                strategy, version = pair
                if signal.state not in (
                    RecommendationState.WATCH,
                    RecommendationState.POTENTIAL_ENTRY,
                    RecommendationState.ENTRY,
                ):
                    continue
                if store.open_recommendation(self.session, signal.instrument_id, strategy.id):
                    continue
                recommendation = store.insert_recommendation(
                    self.session,
                    instrument_id=signal.instrument_id,
                    strategy_version_id=signal.strategy_version_id,
                    strategy_code=strategy.code,
                    state=signal.state,
                    horizon=signal.horizon,
                    confidence=signal.confidence,
                    current_price=signal.price,
                    entry_low=signal.entry_low,
                    entry_high=signal.entry_high,
                    target_low=signal.target_low,
                    target_high=signal.target_high,
                    invalidation_price=signal.invalidation_price,
                    risk_level=signal.risk_level,
                    expected_holding_days_min=signal.expected_holding_days_min,
                    expected_holding_days_max=signal.expected_holding_days_max,
                    thesis=signal.thesis,
                    created_reason=" | ".join(signal.reasons),
                    latest_version=1,
                    first_as_of=as_of,
                    last_as_of=as_of,
                )
                store.insert_recommendation_version(
                    self.session,
                    recommendation.id,
                    1,
                    {
                        "as_of": as_of,
                        "state": signal.state.value,
                        "signal_type": signal.signal_type.value,
                        "price": signal.price,
                        "confidence": signal.confidence,
                        "composite_score": signal.composite_score,
                        "risk_level": signal.risk_level.value,
                        "entry_low": signal.entry_low,
                        "entry_high": signal.entry_high,
                        "target_low": signal.target_low,
                        "target_high": signal.target_high,
                        "invalidation_price": signal.invalidation_price,
                        "rules_result": signal.rules_result,
                        "reasons": signal.reasons,
                        "change_summary": "Initial recommendation",
                    },
                )
                ctx = self._recommendation_context(
                    inputs,
                    signal.instrument_id,
                    as_of,
                    scoring_rows,
                    regime_rows,
                    sector_states,
                    index_codes,
                )
                self._store_baseline_snapshot(
                    recommendation,
                    as_of,
                    ctx.values if ctx is not None else {},
                    as_float(signal.price),
                    as_float(signal.composite_score),
                )
                result.items_processed += 1
                store.insert_recommendation_events(
                    self.session,
                    [
                        {
                            "recommendation_id": recommendation.id,
                            "as_of": as_of,
                            "event_type": EventType.RECOMMENDATION_CREATED.value,
                            "mechanism": None,
                            "previous_state": None,
                            "new_state": signal.state.value,
                            "title": f"Recommendation created for {strategy.code}",
                            "message": signal.thesis,
                            "detail": {
                                "reasons": list(signal.reasons),
                                "strategy_code": strategy.code,
                                "strategy_version_id": version.id,
                            },
                            "recommendation_version": 1,
                            "price": signal.price,
                            "calc_version": self.calc_version,
                            "dedupe_key": (
                                f"rec{recommendation.id}:{as_of.isoformat()}:"
                                f"{EventType.RECOMMENDATION_CREATED.value}:"
                            ),
                        }
                    ],
                )
                result.per_symbol.append(
                    {
                        "instrument_id": signal.instrument_id,
                        "strategy": strategy.code,
                        "state": signal.state.value,
                    }
                )
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    # -------------------------------------- tracking, exits, alerts (Phase 5)
    def _session_dates(self, inputs: AnalysisInputs) -> dict[int, list[date]]:
        dates: dict[int, list[date]] = defaultdict(list)
        for instrument_id, as_of in inputs.technical:
            dates[instrument_id].append(as_of)
        return {instrument_id: sorted(values) for instrument_id, values in dates.items()}

    def _symbol_map(self, instrument_ids: Sequence[int]) -> dict[int, str]:
        if not instrument_ids:
            return {}
        rows = self.session.execute(
            select(Instrument.id, Instrument.symbol).where(
                Instrument.id.in_(list(instrument_ids))
            )
        ).all()
        return {instrument_id: symbol for instrument_id, symbol in rows}

    def _recommendation_context(
        self,
        inputs: AnalysisInputs,
        instrument_id: int,
        as_of: date,
        scoring_rows: dict,
        regime_rows: dict,
        sector_states: dict,
        index_codes: Sequence[str],
    ) -> StrategyContext | None:
        """The strategy context for one instrument-day (same input as signals)."""
        index_id = self._primary_index(instrument_id, as_of, index_codes, inputs)
        sector_id = inputs.sector_by_instrument.get(instrument_id)
        sector_state = sector_states.get((index_id, sector_id, as_of))
        return self._signal_context(
            inputs,
            instrument_id,
            as_of,
            scoring_rows.get((instrument_id, as_of)),
            regime_rows.get((index_id, as_of)) if index_id is not None else None,
            {"sector_state": sector_state} if sector_state else None,
        )

    def _store_baseline_snapshot(
        self,
        recommendation: Recommendation,
        as_of: date,
        values: Mapping[str, Any] | None,
        price: float | None,
        composite_score: float | None = None,
    ) -> None:
        """Freeze the original thesis at creation time.

        Written the moment the recommendation is made, from the same context
        that produced the signal, so later reviews can never be compared
        against reconstructed or drifted data.
        """
        snapshot = build_snapshot(as_of, values or {})
        store.upsert_thesis_snapshots(
            self.session,
            [
                {
                    "recommendation_id": recommendation.id,
                    "as_of": as_of,
                    "state": RecommendationState(recommendation.state).value,
                    "factors": snapshot.stored_factors(),
                    "composite_score": (
                        snapshot.number("composite_score")
                        if composite_score is None
                        else as_float(composite_score)
                    ),
                    "confidence": as_float(recommendation.confidence),
                    "price": as_float(price),
                    "weakened_count": 0,
                    "improved_count": 0,
                    "summary": "Original thesis",
                }
            ],
        )

    def _rebuild_baseline(
        self,
        recommendation: Recommendation,
        as_of: date,
        inputs: AnalysisInputs,
        index_codes: Sequence[str],
        scoring_rows: dict,
        regime_rows: dict,
        sector_states: dict,
        current: StrategyContext,
    ) -> ThesisSnapshot | None:
        """Rebuild a missing original thesis from its own day (legacy rows).

        Returns ``None`` when that day can no longer be reconstructed, so the
        caller can record the gap instead of comparing against invented data.
        """
        original_ctx = (
            current
            if recommendation.first_as_of == as_of
            else self._recommendation_context(
                inputs,
                recommendation.instrument_id,
                recommendation.first_as_of,
                scoring_rows,
                regime_rows,
                sector_states,
                index_codes,
            )
        )
        if original_ctx is None:
            return None
        snapshot = build_snapshot(recommendation.first_as_of, original_ctx.values)
        self._store_baseline_snapshot(
            recommendation,
            recommendation.first_as_of,
            original_ctx.values,
            as_float(original_ctx.close),
        )
        return snapshot

    def track_recommendations(
        self, index_codes: Sequence[str], as_of: date | None = None
    ) -> JobResult:
        """Re-evaluate every open recommendation against its original thesis.

        For each recommendation and day this writes a thesis snapshot, the
        change list versus the original thesis, whatever exit mechanisms fired
        and — only when something actually changed — a new immutable
        ``recommendation_versions`` row plus lifecycle events. Price levels in
        the ``recommendations`` row stay frozen at the values the user was
        given; per-day levels live in the version history.
        """
        run = self._start_run("track_recommendations", {"index_codes": list(index_codes)})
        result = JobResult(
            run_id=run.id, job_name="track_recommendations", status=RunStatus.RUNNING
        )
        try:
            inputs = self._analysis_inputs(index_codes, None, as_of)
            recommendations = store.get_tracked_recommendations(self.session)
            if not recommendations:
                self.session.commit()
                self._finish_run(run, result)
                return result
            if as_of is None:
                session_dates = self._session_dates(inputs)
                if not session_dates:
                    self.session.commit()
                    self._finish_run(run, result)
                    return result
                as_of = max(max(values) for values in session_dates.values())
            scoring_rows = self._scoring_map(inputs)
            regime_rows = self._regime_map(inputs.start, as_of)
            sector_states = self._sector_state_map(index_codes, inputs.start, as_of)
            session_dates = self._session_dates(inputs)
            signals = {
                (signal.instrument_id, signal.strategy_version_id): signal
                for signal in store.get_signals_on(self.session, as_of)
            }

            for recommendation in recommendations:
                try:
                    self._track_one(
                        recommendation,
                        as_of,
                        inputs,
                        index_codes,
                        scoring_rows,
                        regime_rows,
                        sector_states,
                        session_dates,
                        signals,
                        result,
                    )
                except Exception as exc:  # noqa: BLE001
                    result.items_failed += 1
                    self._record_error(
                        run.id,
                        "tracking",
                        Severity.ERROR,
                        "TRACKING_FAILED",
                        (
                            f"recommendation {recommendation.id}: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        instrument_id=recommendation.instrument_id,
                        trade_date=as_of,
                    )
                    self.session.rollback()
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def _track_one(
        self,
        recommendation: Recommendation,
        as_of: date,
        inputs: AnalysisInputs,
        index_codes: Sequence[str],
        scoring_rows: dict,
        regime_rows: dict,
        sector_states: dict,
        session_dates: dict[int, list[date]],
        signals: dict,
        result: JobResult,
    ) -> None:
        """One recommendation, one day."""
        instrument_id = recommendation.instrument_id
        signal = signals.get((instrument_id, recommendation.strategy_version_id))
        ctx = self._recommendation_context(
            inputs, instrument_id, as_of, scoring_rows, regime_rows, sector_states, index_codes
        )
        if ctx is None:
            # No metrics for this day: record the gap, change nothing.
            if recommendation.last_as_of < as_of:
                self._record_error(
                    result.run_id,
                    "tracking",
                    Severity.INFO,
                    "NO_DATA_FOR_DAY",
                    f"recommendation {recommendation.id} not reviewed: no metrics for {as_of}",
                    instrument_id=instrument_id,
                    trade_date=as_of,
                )
                result.items_failed += 1
            return

        version = store.latest_recommendation_version(self.session, recommendation.id)
        baseline_row = store.get_thesis_snapshot(
            self.session, recommendation.id, recommendation.first_as_of
        )
        current = build_snapshot(as_of, ctx.values)
        if baseline_row is not None:
            baseline = ThesisSnapshot.from_stored(baseline_row.as_of, baseline_row.factors)
        else:
            baseline = self._rebuild_baseline(
                recommendation,
                as_of,
                inputs,
                index_codes,
                scoring_rows,
                regime_rows,
                sector_states,
                ctx,
            )
            if baseline is None:
                # No honest baseline: report the gap and claim no change rather
                # than measuring today's thesis against today's thesis.
                self._record_error(
                    result.run_id,
                    "tracking",
                    Severity.WARNING,
                    "BASELINE_UNAVAILABLE",
                    (
                        f"recommendation {recommendation.id}: the thesis snapshot for "
                        f"{recommendation.first_as_of} could not be rebuilt; change "
                        "detection is disabled for this review"
                    ),
                    instrument_id=instrument_id,
                    trade_date=as_of,
                )
                baseline = current
        assessment = compare(baseline, current)

        # A recommendation already in EXIT is closed on the next *new* review
        # day, so the exit is always visible before the close. Re-running the
        # same day must not close it (that would rewrite history).
        reviewed_on = version.as_of if version is not None else None
        if recommendation.state is RecommendationState.EXIT and (
            reviewed_on is None or reviewed_on < as_of
        ):
            decision = close_decision(recommendation.state, as_of)
            triggers: tuple = ()
        else:
            triggers = tuple(
                self._exit_triggers(
                    recommendation, ctx, signal, baseline, current, session_dates
                )
            )
            decision = next_state(
                recommendation.state,
                RecommendationState(signal.state) if signal is not None else None,
                assessment,
                triggers,
                min_weakened=DEFAULT_EXIT_POLICY.weaken_score_factors,
                # Only a recommendation the user actually holds can be exited;
                # one that was never entered is flagged for review instead.
                active=recommendation.active_since is not None,
            )

        self._store_thesis_snapshot(recommendation, current, decision, as_of, ctx.close)
        events = self._store_events(
            recommendation, decision, as_of, ctx.close, assessment, triggers
        )
        if decision.changed:
            self._apply_decision(recommendation, decision, as_of, signal, version)
        # ``last_reviewed_at`` tracks the last review, while ``last_as_of`` only
        # moves when something actually changed, so a no-op day is still visible.
        recommendation.last_reviewed_at = datetime.utcnow()
        self.session.flush()
        result.items_processed += 1
        result.per_symbol.append(
            {
                "instrument_id": instrument_id,
                "recommendation_id": recommendation.id,
                "state": recommendation.state.value,
                "changed": decision.changed,
                "events": [event["event_type"] for event in events],
                "weakened": assessment.weakened_count,
            }
        )

    def _exit_triggers(
        self,
        recommendation: Recommendation,
        ctx: StrategyContext,
        signal: Signal | None,
        baseline: ThesisSnapshot,
        current: ThesisSnapshot,
        session_dates: dict[int, list[date]],
    ) -> list[ExitTrigger]:
        """Run every independent exit mechanism for one recommendation-day."""
        active_since = recommendation.active_since or recommendation.first_as_of
        dates = session_dates.get(recommendation.instrument_id, [])
        held = sum(1 for day in dates if active_since <= day <= ctx.as_of)
        entry_version = self.session.scalar(
            select(RecommendationVersion)
            .where(
                RecommendationVersion.recommendation_id == recommendation.id,
                RecommendationVersion.state == RecommendationState.ENTRY,
            )
            .order_by(RecommendationVersion.version)
        )
        actions = store.get_corporate_actions(
            self.session, recommendation.instrument_id, recommendation.last_as_of, ctx.as_of
        )
        return evaluate_exits(
            ExitContext(
                as_of=ctx.as_of,
                price=ctx.close,
                values=ctx.values,
                signal_state=RecommendationState(signal.state) if signal is not None else None,
                signal_reasons=list(signal.reasons) if signal is not None else [],
                signal_rules_result=list(signal.rules_result) if signal is not None else [],
                invalidation_price=as_float(recommendation.invalidation_price),
                entry_low=as_float(recommendation.entry_low),
                entry_high=as_float(recommendation.entry_high),
                target_low=as_float(recommendation.target_low),
                target_high=as_float(recommendation.target_high),
                entry_price=as_float(entry_version.price) if entry_version is not None else None,
                sessions_since_active=held,
                expected_holding_days_max=recommendation.expected_holding_days_max,
                baseline_composite=baseline.number("composite_score"),
                current_composite=current.number("composite_score"),
                baseline_factors=baseline.factors,
                corporate_actions=[
                    {
                        "action_type": action.action_type,
                        "ex_date": action.ex_date,
                        "description": action.description,
                    }
                    for action in actions
                ],
            )
        )

    def _store_thesis_snapshot(
        self,
        recommendation: Recommendation,
        snapshot: ThesisSnapshot,
        decision: LifecycleDecision,
        as_of: date,
        price: float | None,
    ) -> None:
        assessment = decision.assessment
        summary = decision.change_summary
        if not summary:
            # Every snapshot explains today's picture, not only the state changes.
            summary = (
                assessment_reason(assessment)
                if assessment is not None
                else "No tracked change versus the original thesis."
            )
        store.upsert_thesis_snapshots(
            self.session,
            [
                {
                    "recommendation_id": recommendation.id,
                    "as_of": as_of,
                    "state": decision.state.value,
                    "factors": snapshot.stored_factors(),
                    "composite_score": snapshot.number("composite_score"),
                    "price": round_or_none(price),
                    "weakened_count": assessment.weakened_count if assessment else 0,
                    "improved_count": assessment.improved_count if assessment else 0,
                    "summary": summary,
                }
            ],
        )

    def _store_events(
        self,
        recommendation: Recommendation,
        decision: LifecycleDecision,
        as_of: date,
        price: float | None,
        assessment: ThesisAssessment | None,
        triggers: Sequence[ExitTrigger],
    ) -> list[dict]:
        if decision.event is None:
            return []
        version = recommendation.latest_version + (1 if decision.changed else 0)
        mechanism = decision.mechanism.value if decision.mechanism else ""
        dedupe = f"rec{recommendation.id}:{as_of.isoformat()}:{decision.event.value}:{mechanism}"
        detail: dict = {
            "reasons": list(decision.reasons),
            "assessment": assessment.as_dict() if assessment else None,
            "triggers": [trigger.as_dict() for trigger in triggers],
            "tracking_version": TRACKING_VERSION,
        }
        rows = [
            {
                "recommendation_id": recommendation.id,
                "as_of": as_of,
                "event_type": decision.event.value,
                "mechanism": mechanism or None,
                "previous_state": recommendation.state.value,
                "new_state": decision.state.value,
                "title": decision.change_summary or decision.event.value.replace("_", " ").title(),
                "message": " ".join(decision.reasons),
                "detail": detail,
                "recommendation_version": version,
                "price": round_or_none(price),
                "calc_version": self.calc_version,
                "dedupe_key": dedupe,
            }
        ]
        store.insert_recommendation_events(self.session, rows)
        return rows

    def _apply_decision(
        self,
        recommendation: Recommendation,
        decision: LifecycleDecision,
        as_of: date,
        signal: Signal | None,
        version: RecommendationVersion | None,
    ) -> None:
        """Append the new version and move the live view to the new state."""
        latest = version.version if version is not None else recommendation.latest_version
        new_version = latest + 1
        store.insert_recommendation_version(
            self.session,
            recommendation.id,
            new_version,
            {
                "as_of": as_of,
                "state": decision.state.value,
                "signal_type": signal.signal_type.value if signal is not None else None,
                "price": as_float(signal.price) if signal is not None else None,
                "confidence": as_float(signal.confidence) if signal is not None else None,
                "composite_score": as_float(signal.composite_score) if signal is not None else None,
                "risk_level": signal.risk_level.value if signal is not None else None,
                "entry_low": as_float(signal.entry_low) if signal is not None else None,
                "entry_high": as_float(signal.entry_high) if signal is not None else None,
                "target_low": as_float(signal.target_low) if signal is not None else None,
                "target_high": as_float(signal.target_high) if signal is not None else None,
                "invalidation_price": as_float(signal.invalidation_price)
                if signal is not None
                else None,
                "rules_result": signal.rules_result if signal is not None else [],
                "reasons": list(decision.reasons),
                "change_summary": decision.change_summary,
            },
        )
        recommendation.latest_version = new_version
        recommendation.state = decision.state
        recommendation.last_as_of = as_of
        if signal is not None:
            recommendation.current_price = signal.price
            recommendation.confidence = signal.confidence
        if decision.state is RecommendationState.ENTRY and recommendation.active_since is None:
            recommendation.active_since = as_of
        if decision.state is RecommendationState.CLOSED:
            recommendation.closed_at = datetime.utcnow()
        if decision.state is RecommendationState.EXIT:
            # Every mechanism that fired is kept, so an exit is never explained
            # by only the first reason that happened to sort first.
            recommendation.exit_reason = " | ".join(decision.reasons) or None
        self.session.flush()

    def dispatch_notifications(
        self, index_codes: Sequence[str], as_of: date | None = None
    ) -> JobResult:
        """Turn recommendation events and regime changes into notifications.

        Notifications are deduplicated on the exact fact that produced them, so
        re-running the EOD chain never re-alerts on unchanged data while every
        new state change, exit mechanism or regime label is reported with its
        reason.
        """
        run = self._start_run("dispatch_notifications", {"index_codes": list(index_codes)})
        result = JobResult(
            run_id=run.id, job_name="dispatch_notifications", status=RunStatus.RUNNING
        )
        try:
            if as_of is None:
                as_of = self.session.scalar(
                    select(func.max(RecommendationEvent.as_of))
                ) or self.session.scalar(select(func.max(Recommendation.last_as_of)))
            if as_of is None:
                self.session.commit()
                self._finish_run(run, result)
                return result

            index_ids = store.ensure_indices(self.session, list(index_codes))
            instrument_ids = sorted(
                member_instrument_ids(
                    membership_intervals(
                        self.session, list(index_codes), date(1900, 1, 1), date(2900, 1, 1)
                    )
                )
            )
            recommendations = store.get_recommendations(self.session, instrument_ids)
            symbols = self._symbol_map([r.instrument_id for r in recommendations])
            drafts: list[NotificationDraft] = []
            for recommendation in recommendations:
                original = self.session.scalar(
                    select(RecommendationVersion).where(
                        RecommendationVersion.recommendation_id == recommendation.id,
                        RecommendationVersion.version == 1,
                    )
                )
                drafts.append(
                    new_recommendation_draft(
                        recommendation_id=recommendation.id,
                        instrument_id=recommendation.instrument_id,
                        symbol=symbols.get(recommendation.instrument_id, "unknown"),
                        strategy_code=recommendation.strategy_code,
                        as_of=recommendation.first_as_of,
                        state=(
                            original.state.value
                            if original is not None
                            else recommendation.state.value
                        ),
                        message=recommendation.thesis,
                        reason=recommendation.created_reason,
                        price=as_float(recommendation.current_price),
                    )
                )
                for event in store.get_recommendation_events(
                    self.session, recommendation.id, as_of
                ):
                    drafts.extend(
                        self._event_drafts(event, recommendation, symbols, as_of)
                    )
            drafts.extend(self._regime_drafts(index_ids, as_of))

            keys = [draft.dedupe_key for draft in drafts]
            fresh = deduplicate(drafts, store.existing_notification_keys(self.session, keys))
            stored = store.insert_notifications(
                self.session, [draft.as_columns(self.calc_version) for draft in fresh]
            )
            result.items_processed = stored
            result.per_symbol = [
                {
                    "notification_type": draft.notification_type.value,
                    "recommendation_id": draft.recommendation_id,
                    "reason": draft.reason,
                }
                for draft in fresh
            ]
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, result, exc)
            raise

    def _event_drafts(
        self,
        event: RecommendationEvent,
        recommendation: Recommendation,
        symbols: dict[int, str],
        as_of: date,
    ) -> list[NotificationDraft]:
        detail = event.detail or {}
        assessment = _assessment_from_detail(detail.get("assessment"))
        triggers = _triggers_from_detail(detail.get("triggers"))
        return from_event(
            {
                "event_type": event.event_type,
                "as_of": event.as_of,
                "recommendation_id": recommendation.id,
                "recommendation_version": event.recommendation_version,
                "instrument_id": recommendation.instrument_id,
                "strategy_code": recommendation.strategy_code,
                "mechanism": event.mechanism.value if event.mechanism else None,
                "previous_state": event.previous_state.value if event.previous_state else None,
                "new_state": event.new_state.value if event.new_state else None,
                "title": event.title,
                "message": event.message,
                "price": as_float(event.price),
                "detail": detail,
            },
            symbol=symbols.get(recommendation.instrument_id, "unknown"),
            assessment=assessment,
            triggers=triggers,
        )

    def _regime_drafts(self, index_ids: dict[str, int], as_of: date) -> list[NotificationDraft]:
        """Alert when an index's regime label changed since its previous value."""
        drafts: list[NotificationDraft] = []
        for code, index_id in index_ids.items():
            labels = self.session.execute(
                select(MarketRegime.as_of, MarketRegime.regime_label, MarketRegime.regime_score)
                .where(
                    MarketRegime.index_id == index_id,
                    MarketRegime.calc_version == self.calc_version,
                    MarketRegime.as_of <= as_of,
                )
                .order_by(MarketRegime.as_of.desc())
                .limit(2)
            ).all()
            if len(labels) < 2:
                continue
            latest_day, latest_label, latest_score = labels[0]
            previous_label = labels[1][1]
            if latest_label == previous_label:
                continue
            drafts.append(
                regime_change_draft(
                    index_id=index_id,
                    index_code=code,
                    previous=str(previous_label),
                    current=str(latest_label),
                    as_of=latest_day,
                    score=as_float(latest_score),
                )
            )
        return drafts

    # --------------------------------------------------------------- combined
    def compute_eod(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> list[JobResult]:
        """Full EOD refresh in dependency order.

        Phase 2 (fundamentals, technical, momentum, valuation) feeds Phase 3
        (sector aggregates, market regime, horizon, scoring), which in turn
        feeds Phase 4 (per-strategy signals and tracked recommendations) and
        Phase 5 (daily thesis monitoring, exits and notifications).
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
            self.compute_signals(index_codes, start, end),
            self.generate_recommendations(index_codes),
            self.track_recommendations(index_codes),
            self.dispatch_notifications(index_codes),
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
