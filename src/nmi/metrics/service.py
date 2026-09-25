"""Metrics engine (Phase 2) — service layer.

Orchestrates bulk runs with the same audit/traceability conventions as the
ingestion layer: every job writes an ``ingestion_runs`` row and per-instrument
failures become ``ingestion_errors`` rows instead of aborting the run.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nmi.core.config import settings
from nmi.core.enums import RunStatus, Severity
from nmi.core.models import (
    BalanceSheet,
    CashFlow,
    Company,
    CorporateAction,
    CorporateActionType,
    DailyPrice,
    IncomeStatement,
    Index,
    IndexPrice,
    IngestionError,
    IngestionRun,
)
from nmi.indicators.fundamental import compute_fundamental_metrics
from nmi.indicators.momentum import compute_momentum, compute_relative_strength
from nmi.indicators.technical import compute_technical_indicators
from nmi.indicators.valuation import compute_valuation_metrics
from nmi.ingestion import store
from nmi.ingestion.backfill import resolve_members
from nmi.ingestion.providers.base import index_price_provider

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

    # --------------------------------------------------------------- combined
    def compute_eod(
        self, index_codes: Sequence[str], start: date | None = None, end: date | None = None
    ) -> list[JobResult]:
        """Asset-metrics refresh in dependency order (technical, momentum, valuation)."""
        return [
            self.compute_fundamentals(index_codes),
            self.compute_technical(index_codes, start, end),
            self.compute_momentum(index_codes, start, end),
            self.compute_valuation(index_codes, start, end),
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
