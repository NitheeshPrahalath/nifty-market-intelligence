"""Ingestion orchestration: reference seeding + backfills with audit + retry.

Runtime model per instrument:
    provider -> fetch (retry) -> normalize -> validate -> adjust -> upsert

Each instrument is a checkpoint; per-row failures produce ``IngestionError``
audit rows and affected rows are *flagged incomplete* rather than silently
dropped. Every run is recorded in ``ingestion_runs`` for traceability.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from nmi.core.config import settings
from nmi.core.enums import RunStatus, Severity
from nmi.core.models import (
    Company,
    CorporateAction,
    Index,
    IndexMembership,
    IngestionError,
    IngestionRun,
    Instrument,
)
from nmi.ingestion import store
from nmi.ingestion.adjustments import apply_adjustments
from nmi.ingestion.normalize import normalize_candles
from nmi.ingestion.providers.base import (
    corporate_action_provider,
    fundamental_provider,
    get_provider,
    membership_provider,
    universe_provider,
)
from nmi.ingestion.records import (
    BalanceSheetRecord,
    CashFlowRecord,
    IncomeStatementRecord,
    IndexMembershipRecord,
    UniverseRow,
)
from nmi.ingestion.validation import (
    ValidationReport,
    incomplete_trade_dates,
    validate_candles,
)

log = logging.getLogger(__name__)

_fetch_retry = retry(
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    stop=stop_after_attempt(settings.ingest_retry_max),
    wait=wait_exponential(multiplier=0.5, min=1, max=10),
    reraise=True,
)


@dataclass(slots=True)
class PerSymbolOutcome:
    symbol: str
    stored: int
    issues_count: int
    issues: list[str] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "stored": self.stored,
            "issues_count": self.issues_count,
            "issues": self.issues,
            "summary": self.summary,
        }


@dataclass(slots=True)
class RunResult:
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


class IngestionService:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------ seed
    def seed_universe(
        self,
        universe: Sequence[UniverseRow] | None = None,
        memberships: Sequence[IndexMembershipRecord] | None = None,
    ) -> dict:
        rows = list(universe) if universe is not None else universe_provider().fetch_universe()
        mem = (
            list(memberships)
            if memberships is not None
            else membership_provider().fetch_membership()
        )
        symbol_to_instrument = store.upsert_universe(self.session, rows)
        index_by_code = store.ensure_indices(
            self.session, {m.index_code for m in mem}
        )
        applied = store.apply_memberships(
            self.session, mem, index_by_code, symbol_to_instrument
        )
        self.session.commit()
        return {
            "instruments": len(symbol_to_instrument),
            "memberships_applied": applied,
            "indices": list(index_by_code.keys()),
        }

    # ---------------------------------------------------------- backfill prices
    def backfill_prices(
        self,
        index_codes: Sequence[str],
        start: date,
        end: date,
        provider_name: str | None = None,
    ) -> RunResult:
        provider = _price_provider(provider_name)
        run = self._start_run(
            "backfill_prices",
            {
                "index_codes": list(index_codes),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "provider": provider.name,
            },
        )
        result = RunResult(
            run_id=run.id, job_name="backfill_prices", status=RunStatus.RUNNING
        )
        try:
            instruments = resolve_members(self.session, index_codes, start, end)
            for instrument in instruments:
                symbol = instrument.symbol
                try:
                    outcome = self._ingest_one_instrument(
                        run.id, instrument, start, end, provider
                    )
                    result.items_processed += outcome.stored
                    result.items_failed += outcome.issues_count
                    result.per_symbol.append(outcome.as_dict())
                except Exception as exc:  # noqa: BLE001 - isolation per symbol
                    result.items_failed += 1
                    result.per_symbol.append(
                        {"symbol": symbol, "stored": 0, "error": str(exc)}
                    )
                    self._record_error(
                        run.id, "fetch", Severity.ERROR, "FETCH_FAILED",
                        f"{symbol}: {type(exc).__name__}: {exc}",
                        instrument_id=instrument.id,
                    )
                    self._rollback_safe()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self._fail_run(run, result, exc)
            raise

    def _ingest_one_instrument(
        self, run_id, instrument, start, end, provider
    ) -> PerSymbolOutcome:
        raw = _fetch_retry(provider.fetch_prices)(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            start=start,
            end=end,
        )
        candles = normalize_candles(raw)
        report = validate_candles(candles)
        for issue in report.errors:
            self._record_error(
                run_id, "validate", Severity.ERROR, issue.code, issue.message,
                instrument_id=instrument.id, trade_date=issue.trade_date,
                raw_data=issue.context or None,
            )
        incomplete = incomplete_trade_dates(report)
        actions = _actions_for(self.session, instrument.id, start, end)
        adjusted = apply_adjustments(candles, actions)
        stored = store.upsert_daily_prices(
            self.session,
            instrument.id,
            candles,
            adjusted,
            incomplete_dates=incomplete,
            source=provider.name,
        )
        outcome = PerSymbolOutcome(
            symbol=instrument.symbol,
            stored=stored,
            issues_count=_issue_weight(report),
            issues=[i.code for i in report.issues],
            summary=report.summary,
        )
        self.session.commit()
        return outcome

    # -------------------------------------------------------- corporate actions
    def backfill_corporate_actions(
        self, index_codes: Sequence[str], start: date | None, end: date | None
    ) -> RunResult:
        provider = corporate_action_provider()
        run = self._start_run("backfill_corporate_actions", {"source": provider.name})
        result = RunResult(
            run_id=run.id,
            job_name="backfill_corporate_actions",
            status=RunStatus.RUNNING,
        )
        try:
            instruments = resolve_members(self.session, index_codes, start, end)
            instrument_by_symbol = {i.symbol: i.id for i in instruments}
            records = provider.fetch_actions(
                symbols=sorted(instrument_by_symbol), start=start, end=end
            )
            stored = store.upsert_corporate_actions(
                self.session, records, instrument_by_symbol
            )
            result.items_processed = stored
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------ fundamentals
    def backfill_fundamentals(self, isins: Sequence[str] | None = None) -> RunResult:
        provider = fundamental_provider()
        run = self._start_run("backfill_fundamentals", {"source": provider.name})
        result = RunResult(
            run_id=run.id,
            job_name="backfill_fundamentals",
            status=RunStatus.RUNNING,
        )
        try:
            isins = list(isins) if isins else list(
                self.session.scalars(select(Company.isin)).all()
            )
            if not isins:
                self._finish_run(run, result)
                return result
            company_by_isin = {
                isin: cid
                for isin, cid in self.session.execute(
                    select(Company.isin, Company.id).where(Company.isin.in_(isins))
                )
            }
            income: list[IncomeStatementRecord] = provider.fetch_income_statements(isins)
            balance: list[BalanceSheetRecord] = getattr(
                provider, "fetch_balance_sheets", lambda _: []
            )(isins)
            cash: list[CashFlowRecord] = getattr(
                provider, "fetch_cash_flows", lambda _: []
            )(isins)
            n = 0
            n += store.upsert_income_statements(self.session, income, company_by_isin)
            n += store.upsert_balance_sheets(self.session, balance, company_by_isin)
            n += store.upsert_cash_flows(self.session, cash, company_by_isin)
            result.items_processed = n
            self.session.commit()
            self._finish_run(run, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self._fail_run(run, result, exc)
            raise

    # ------------------------------------------------------------- run audit
    def _start_run(self, job_name: str, config: dict) -> IngestionRun:
        run = IngestionRun(
            job_name=job_name,
            status=RunStatus.RUNNING,
            started_at=datetime.utcnow(),
            config=config,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def _finish_run(self, run: IngestionRun, result: RunResult) -> None:
        run.status = RunStatus.SUCCEEDED
        run.finished_at = datetime.utcnow()
        run.items_processed = result.items_processed
        run.items_failed = result.items_failed
        run.error_summary = (
            f"{result.items_failed} flagged issue(s)"
            if result.items_failed
            else None
        )
        self.session.commit()
        result.status = RunStatus.SUCCEEDED
        log.info(
            "run %s finished: processed=%s failed=%s",
            run.job_name,
            result.items_processed,
            result.items_failed,
        )

    def _fail_run(self, run: IngestionRun, result: RunResult, exc: Exception) -> None:
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
        raw_data: dict | None = None,
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
                raw_data=raw_data,
            )
        )

    def _rollback_safe(self) -> None:
        try:
            self.session.rollback()
        except Exception:  # noqa: BLE001
            pass


def _price_provider(provider_name: str | None):
    if provider_name is None:
        return get_provider("price")
    previous = settings.price_provider
    settings.price_provider = provider_name
    try:
        return get_provider("price")
    finally:
        settings.price_provider = previous


def _issue_weight(report: ValidationReport) -> int:
    return len(report.errors) + len(report.warnings) // 4


def resolve_members(
    session: Session,
    index_codes: Sequence[str],
    start: date,
    end: date,
) -> list[Instrument]:
    """Instruments that were index members at any point within [start, end].

    Membership intersections are interval-based → an instrument that left a
    midcap index months ago is still resolvable for its prior months, which is
    what eliminates survivorship bias in later backtests.
    """
    overlap = and_(
        IndexMembership.effective_from <= end,
        or_(
            IndexMembership.effective_to.is_(None),
            IndexMembership.effective_to >= start,
        ),
    )
    stmt = (
        select(Instrument)
        .join(IndexMembership, IndexMembership.instrument_id == Instrument.id)
        .join(Index, Index.id == IndexMembership.index_id)
        .where(Index.code.in_(index_codes), overlap)
        .order_by(Instrument.symbol)
    )
    return list(session.scalars(stmt).unique().all())


def _actions_for(
    session: Session, instrument_id: int, start: date, end: date
) -> list[CorporateAction]:
    stmt = select(CorporateAction).where(
        CorporateAction.instrument_id == instrument_id,
        CorporateAction.ex_date >= start,
        CorporateAction.ex_date <= end,
    )
    return list(session.scalars(stmt).all())
