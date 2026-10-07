"""Backtest orchestration (Phase 6).

This is the only part of Phase 6 that touches the database. It turns stored,
date-stamped data into a :class:`~nmi.backtest.engine.BacktestDataset` and hands
that to the pure engine, so the simulation itself is testable without a
database and the data access stays in one auditable place.

Everything read here is as-of: prices and metric rows come from the requested
window, fundamentals are looked up with ``as_of <= day``, membership is resolved
through the effective-date intervals, and the benchmark curve is the index's own
close series. The Phase-4 strategy engine and the Phase-5 exit engine are the
*same* functions the live pipeline uses, which is what makes a backtest
comparable to what the platform would actually have done.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from nmi.analysis.common import as_float
from nmi.analysis.inputs import (
    AnalysisInputs,
    benchmark_prices,
    corporate_actions_between,
    load_analysis_inputs,
    members_on,
    regime_map,
    scoring_map,
    sector_state_map,
)
from nmi.backtest.engine import (
    BacktestConfig,
    BacktestDataset,
    BacktestResult,
    Bar,
    HeldPosition,
    SignalAction,
    SignalView,
    run_backtest,
)
from nmi.backtest.walkforward import WindowOutcome, summarize_windows, walk_forward_windows
from nmi.core.config import settings
from nmi.core.enums import BacktestExitReason, BacktestKind, RunStatus, Severity
from nmi.core.models import (
    BacktestRun,
    IngestionError,
    IngestionRun,
    Instrument,
    RecommendationState,
)
from nmi.ingestion import store
from nmi.metrics.service import JobResult
from nmi.strategies import StrategyContext, StrategyDecision, StrategyMeta, evaluate_strategy
from nmi.strategies.rules import StrategyRules
from nmi.tracking import (
    ExitAction,
    ExitContext,
    ThesisSnapshot,
    build_snapshot,
    evaluate_exits,
)

log = logging.getLogger(__name__)

MECHANISM_EXITS = {
    "RISK": BacktestExitReason.RISK_EXIT,
    "TECHNICAL": BacktestExitReason.TECHNICAL_EXIT,
    "FUNDAMENTAL": BacktestExitReason.FUNDAMENTAL_EXIT,
    "VALUATION": BacktestExitReason.VALUATION_EXIT,
    "TARGET": BacktestExitReason.TARGET,
    "TIME": BacktestExitReason.TIME_STOP,
    "EVENT": BacktestExitReason.EVENT_EXIT,
    "STRATEGY": BacktestExitReason.STRATEGY_EXIT,
}


def _action_for(state: RecommendationState) -> SignalAction:
    if state is RecommendationState.ENTRY:
        return SignalAction.ENTER
    if state is RecommendationState.EXIT:
        return SignalAction.EXIT
    return SignalAction.NONE


class BacktestService:
    """Builds as-of datasets, runs simulations and stores the results."""

    def __init__(self, session: Session):
        self.session = session
        self.calc_version = settings.calc_version

    # ------------------------------------------------------------- auditing
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

    def _fail_run(self, run: IngestionRun, exc: Exception) -> None:
        run.status = RunStatus.FAILED
        run.finished_at = datetime.utcnow()
        run.error_summary = f"{type(exc).__name__}: {exc}"
        self.session.commit()

    # ------------------------------------------------------------ strategies
    def _strategies(
        self, strategy_codes: Sequence[str] | None
    ) -> list[tuple[str, StrategyMeta, StrategyRules]]:
        """Active rule documents, optionally filtered to specific codes."""
        active = store.get_active_strategy_versions(self.session)
        if not active:
            return []
        wanted = {c.strip().upper() for c in strategy_codes} if strategy_codes else None
        out: list[tuple[str, StrategyMeta, StrategyRules]] = []
        for strategy, version in active:
            if wanted and strategy.code.upper() not in wanted:
                continue
            out.append(
                (
                    strategy.code,
                    StrategyMeta(
                        code=strategy.code,
                        name=strategy.name,
                        horizon=getattr(strategy.horizon, "value", strategy.horizon),
                        version=version.version,
                    ),
                    StrategyRules.from_dict(version.rules),
                )
            )
        return out

    def _context(
        self,
        inputs: AnalysisInputs,
        instrument_id: int,
        as_of: date,
        scoring_rows: dict,
        regime_rows: dict,
        sector_states: dict,
    ) -> StrategyContext | None:
        """The as-of strategy context for one instrument-day."""
        index_id = inputs.primary_index(instrument_id, as_of)
        sector_id = inputs.sector_by_instrument.get(instrument_id)
        sector_state = sector_states.get((index_id, sector_id, as_of))
        return inputs.strategy_context(
            instrument_id,
            as_of,
            scoring_rows.get((instrument_id, as_of)),
            regime_rows.get((index_id, as_of)) if index_id is not None else None,
            {"sector_state": sector_state} if sector_state else None,
        )

    def _decisions(
        self,
        ctx: StrategyContext,
        strategies: Sequence[tuple[str, StrategyMeta, StrategyRules]],
    ) -> list[tuple[str, StrategyDecision]]:
        return [(code, evaluate_strategy(meta, rules, ctx)) for code, meta, rules in strategies]

    def _views(
        self, decisions: Sequence[tuple[str, StrategyDecision]]
    ) -> tuple[SignalView, ...]:
        return tuple(
            SignalView(
                action=_action_for(decision.state),
                strategy_code=code,
                entry_low=decision.entry_low,
                entry_high=decision.entry_high,
                target_low=decision.target_low,
                target_high=decision.target_high,
                stop_price=decision.invalidation_price,
                strength=decision.confidence,
                reasons=tuple(decision.reasons),
                exit_reason=BacktestExitReason.STRATEGY_EXIT,
                exit_detail="; ".join(decision.reasons[:2]) or "Strategy exit rules met.",
            )
            for code, decision in decisions
        )

    # -------------------------------------------------------------- dataset
    def build_dataset(
        self,
        index_codes: Sequence[str],
        start: date,
        end: date,
        strategy_codes: Sequence[str] | None = None,
    ) -> BacktestDataset:
        """Assemble the as-of dataset for one simulation window."""
        inputs = load_analysis_inputs(
            self.session, index_codes, start, end, self.calc_version, with_bars=True
        )
        strategies = self._strategies(strategy_codes)
        scoring_rows = scoring_map(
            self.session,
            list(inputs.company_by_instrument),
            start,
            end,
            self.calc_version,
        )
        regime_rows = regime_map(self.session, start, end, self.calc_version)
        sector_states = sector_state_map(self.session, list(index_codes), start, end)

        dates = tuple(day for day in inputs.as_of_dates() if start <= day <= end)
        # Prices come back from the database as Decimal; the engine is written in
        # plain floats, so convert once here, at the boundary.
        bars = {
            key: Bar(**{field: as_float(raw) for field, raw in value.items()})
            for key, value in inputs.bars.items()
            if start <= key[1] <= end and value.get("open") and value.get("close")
        }
        universe: dict[date, frozenset[int]] = {}
        for as_of in dates:
            members: set[int] = set()
            for index_id in inputs.index_ids.values():
                members.update(members_on(inputs.intervals.get(index_id, []), as_of))
            universe[as_of] = frozenset(members)

        signals: dict[tuple[int, date], tuple[SignalView, ...]] = {}
        for as_of in dates:
            for instrument_id in sorted(universe[as_of]):
                if (instrument_id, as_of) not in inputs.technical:
                    continue
                ctx = self._context(
                    inputs, instrument_id, as_of, scoring_rows, regime_rows, sector_states
                )
                if ctx is None:
                    continue
                views = self._views(self._decisions(ctx, strategies))
                if views:
                    signals[(instrument_id, as_of)] = views

        symbols = self._symbols(
            sorted({i for members in universe.values() for i in members})
        )
        return BacktestDataset(
            dates=dates,
            bars=bars,
            signals=signals,
            universe=universe,
            symbols=symbols,
            benchmark=dict(benchmark_prices(self.session, list(index_codes)[0])),
            exit_evaluator=self._exit_evaluator(
                inputs, strategies, scoring_rows, regime_rows, sector_states
            ),
        )

    def _exit_evaluator(
        self,
        inputs: AnalysisInputs,
        strategies: Sequence[tuple[str, StrategyMeta, StrategyRules]],
        scoring_rows: dict,
        regime_rows: dict,
        sector_states: dict,
    ):
        """Bind the Phase-5 exit engine to an open position.

        The baseline is the snapshot taken on the position's own entry day, so a
        backtest judges "has the thesis I bought on changed?" exactly the way the
        live tracker does, and the only exit the engine acts on here is a real
        ``EXIT`` condition: a review-only condition leaves the position open for
        the stop and target mechanics to deal with.
        """
        session = self.session
        holding_max = {code: rules.risk.holding_days_max for code, _m, rules in strategies}

        def evaluate(held: HeldPosition, as_of: date) -> SignalView | None:
            entry_ctx = self._context(
                inputs,
                held.instrument_id,
                held.entry_signal_date,
                scoring_rows,
                regime_rows,
                sector_states,
            )
            current_ctx = self._context(
                inputs, held.instrument_id, as_of, scoring_rows, regime_rows, sector_states
            )
            if entry_ctx is None or current_ctx is None:
                return None
            baseline: ThesisSnapshot = build_snapshot(
                held.entry_signal_date, entry_ctx.values
            )
            current: ThesisSnapshot = build_snapshot(as_of, current_ctx.values)
            decision = next(
                (
                    d
                    for code, d in self._decisions(current_ctx, strategies)
                    if code == held.strategy_code
                ),
                None,
            )
            triggers = evaluate_exits(
                ExitContext(
                    as_of=as_of,
                    price=current_ctx.close,
                    values=current_ctx.values,
                    signal_state=decision.state if decision is not None else None,
                    signal_reasons=list(decision.reasons) if decision is not None else [],
                    signal_rules_result=list(decision.rules_result)
                    if decision is not None
                    else [],
                    invalidation_price=held.stop_price,
                    entry_low=held.entry_low,
                    entry_high=held.entry_high,
                    target_low=held.target_price,
                    target_high=held.target_high,
                    entry_price=held.entry_price,
                    sessions_since_active=held.sessions_held,
                    expected_holding_days_max=holding_max.get(held.strategy_code),
                    baseline_composite=baseline.number("composite_score"),
                    current_composite=current.number("composite_score"),
                    baseline_factors=baseline.factors,
                    corporate_actions=corporate_actions_between(
                        session, held.instrument_id, held.entry_date, as_of
                    ),
                )
            )
            exits = [t for t in triggers if t.action is ExitAction.EXIT]
            if not exits:
                return None
            primary = exits[0]
            return SignalView(
                action=SignalAction.EXIT,
                strategy_code=held.strategy_code,
                reasons=tuple(t.reason for t in triggers),
                exit_reason=MECHANISM_EXITS.get(
                    primary.mechanism.value, BacktestExitReason.STRATEGY_EXIT
                ),
                exit_detail="; ".join(t.reason for t in triggers),
            )

        return evaluate

    def _symbols(self, instrument_ids: Sequence[int]) -> dict[int, str]:
        if not instrument_ids:
            return {}
        rows = self.session.query(Instrument).filter(
            Instrument.id.in_(list(instrument_ids))
        ).all()
        return {row.id: row.symbol for row in rows}

    # ------------------------------------------------------------ persistence
    def _persist(
        self,
        result: BacktestResult,
        *,
        name: str,
        index_codes: Sequence[str],
        strategy_codes: Sequence[str],
        start: date,
        end: date,
        kind: BacktestKind = BacktestKind.SINGLE,
        parent_run_id: int | None = None,
        window_index: int | None = None,
    ) -> BacktestRun:
        """Store a simulation and everything it produced in one transaction."""
        backtest = store.create_backtest_run(
            self.session,
            {
                "name": name,
                "kind": kind,
                "parent_run_id": parent_run_id,
                "window_index": window_index,
                "strategy_codes": list(strategy_codes),
                "index_codes": list(index_codes),
                "start_date": start,
                "end_date": end,
                "initial_capital": result.config.initial_capital,
                "config": result.config.as_dict(),
                "metrics": result.metrics.as_dict(),
                "trades_count": len(result.trades),
                "calc_version": self.calc_version,
                "status": RunStatus.SUCCEEDED,
            },
        )
        store.insert_backtest_trades(self.session, result.trade_rows(backtest.id))
        store.insert_backtest_equity_points(self.session, result.point_rows(backtest.id))
        store.insert_backtest_rejections(
            self.session, [r.as_row(backtest.id) for r in result.rejections]
        )
        self.session.commit()
        self.session.refresh(backtest)
        return backtest

    # ----------------------------------------------------------------- runs
    def run_backtest(
        self,
        index_codes: Sequence[str],
        start: date,
        end: date,
        *,
        name: str | None = None,
        strategy_codes: Sequence[str] | None = None,
        config: BacktestConfig | None = None,
    ) -> tuple[BacktestRun, JobResult]:
        """Simulate one window end to end and store the result."""
        run = self._start_run(
            "run_backtest",
            {
                "index_codes": list(index_codes),
                "strategy_codes": list(strategy_codes or []),
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        result = JobResult(run_id=run.id, job_name="run_backtest", status=RunStatus.RUNNING)
        try:
            config = config or BacktestConfig()
            dataset = self.build_dataset(index_codes, start, end, strategy_codes)
            if not dataset.dates:
                raise ValueError(
                    f"no metric rows between {start} and {end}; run the EOD chain first"
                )
            simulation = run_backtest(dataset, config)
            backtest = self._persist(
                simulation,
                name=name or f"{','.join(index_codes)} {start}..{end}",
                index_codes=index_codes,
                strategy_codes=self._codes_used(dataset),
                start=start,
                end=end,
            )
            result.items_processed = len(simulation.trades)
            result.per_symbol.append(
                {
                    "backtest_run_id": backtest.id,
                    "trades": len(simulation.trades),
                    "rejections": len(simulation.rejections),
                    "final_equity": round(simulation.final_equity, 2),
                    "metrics": simulation.metrics.as_dict(),
                }
            )
            self._finish_run(run, result)
            return backtest, result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, exc)
            raise

    def run_walk_forward(
        self,
        index_codes: Sequence[str],
        start: date,
        end: date,
        *,
        folds: int = 4,
        train_ratio: float = 0.5,
        name: str | None = None,
        strategy_codes: Sequence[str] | None = None,
        config: BacktestConfig | None = None,
    ) -> tuple[BacktestRun, JobResult]:
        """Score the strategy out of sample, one window at a time.

        Every window is a stored, individually auditable backtest whose parent is
        the summary run, so one lucky window can never hide behind a good
        average.
        """
        run = self._start_run(
            "run_walk_forward",
            {
                "index_codes": list(index_codes),
                "strategy_codes": list(strategy_codes or []),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "folds": folds,
                "train_ratio": train_ratio,
            },
        )
        result = JobResult(
            run_id=run.id, job_name="run_walk_forward", status=RunStatus.RUNNING
        )
        try:
            config = config or BacktestConfig()
            windows = walk_forward_windows(start, end, folds, train_ratio)
            parent = store.create_backtest_run(
                self.session,
                {
                    "name": name or f"walk-forward {','.join(index_codes)} {start}..{end}",
                    "kind": BacktestKind.WALK_FORWARD,
                    "index_codes": list(index_codes),
                    "strategy_codes": list(strategy_codes or []),
                    "start_date": start,
                    "end_date": end,
                    "initial_capital": config.initial_capital,
                    "config": {**config.as_dict(), "folds": folds, "train_ratio": train_ratio},
                    "calc_version": self.calc_version,
                    "status": RunStatus.RUNNING,
                },
            )
            # Commit the summary row before the windows run: a window that fails
            # rolls its transaction back, and the summary must survive that.
            self.session.commit()
            parent_id = parent.id
            outcomes: list[WindowOutcome] = []
            for window in windows:
                try:
                    dataset = self.build_dataset(
                        index_codes, window.test_start, window.test_end, strategy_codes
                    )
                    if not dataset.dates:
                        continue
                    simulation = run_backtest(dataset, config)
                    self._persist(
                        simulation,
                        name=f"{parent.name} window {window.index}",
                        index_codes=index_codes,
                        strategy_codes=self._codes_used(dataset),
                        start=window.test_start,
                        end=window.test_end,
                        parent_run_id=parent.id,
                        window_index=window.index,
                    )
                    outcomes.append(WindowOutcome(window=window, metrics=simulation.metrics))
                    result.items_processed += len(simulation.trades)
                    result.per_symbol.append(
                        {
                            "window": window.index,
                            "test_start": window.test_start.isoformat(),
                            "test_end": window.test_end.isoformat(),
                            "trades": len(simulation.trades),
                            "total_return": simulation.metrics.total_return,
                            "sharpe": simulation.metrics.sharpe,
                            "max_drawdown": simulation.metrics.max_drawdown,
                        }
                    )
                    self.session.commit()
                except Exception as exc:  # noqa: BLE001
                    self.session.rollback()
                    result.items_failed += 1
                    self.session.add(
                        IngestionError(
                            run_id=run.id,
                            stage="backtest_window",
                            severity=Severity.ERROR,
                            code="WINDOW_FAILED",
                            message=f"window {window.index}: {type(exc).__name__}: {exc}",
                        )
                    )
                    self.session.commit()
            parent = store.get_backtest_run(self.session, parent_id)
            parent.metrics = {
                "windows": [o.as_dict() for o in outcomes],
                "summary": summarize_windows(outcomes),
            }
            parent.status = RunStatus.SUCCEEDED
            self.session.commit()
            self.session.refresh(parent)
            result.per_symbol.append(
                {"walk_forward_run_id": parent.id, "summary": parent.metrics["summary"]}
            )
            self._finish_run(run, result)
            return parent, result
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self._fail_run(run, exc)
            raise

    def _codes_used(self, dataset: BacktestDataset) -> list[str]:
        return sorted({v.strategy_code for views in dataset.signals.values() for v in views})

    # ------------------------------------------------------------- reporting
    def get_run(self, backtest_run_id: int) -> BacktestRun | None:
        return store.get_backtest_run(self.session, backtest_run_id)

    def list_runs(self, limit: int = 20) -> list[BacktestRun]:
        return store.list_backtest_runs(self.session, limit)

    def run_summary(self, backtest_run_id: int) -> dict[str, Any]:
        """Stored metrics plus the headline numbers, for the CLI and API."""
        backtest = self.get_run(backtest_run_id)
        if backtest is None:
            return {}
        trades = store.get_backtest_trades(self.session, backtest_run_id)
        metrics = backtest.metrics or {}
        return {
            "backtest_run_id": backtest.id,
            "name": backtest.name,
            "kind": backtest.kind.value,
            "window_index": backtest.window_index,
            "parent_run_id": backtest.parent_run_id,
            "strategies": backtest.strategy_codes,
            "indexes": backtest.index_codes,
            "start": backtest.start_date.isoformat(),
            "end": backtest.end_date.isoformat(),
            "initial_capital": as_float(backtest.initial_capital),
            "final_equity": metrics.get("end_equity"),
            "total_return": metrics.get("total_return"),
            "cagr": metrics.get("cagr"),
            "benchmark_return": metrics.get("benchmark_return"),
            "excess_return": metrics.get("excess_return"),
            "beta": metrics.get("beta"),
            "alpha": metrics.get("alpha"),
            "volatility": metrics.get("volatility"),
            "sharpe": metrics.get("sharpe"),
            "sortino": metrics.get("sortino"),
            "calmar": metrics.get("calmar"),
            "max_drawdown": metrics.get("max_drawdown"),
            "max_drawdown_days": metrics.get("max_drawdown_days"),
            "trades": len(trades),
            "win_rate": metrics.get("win_rate"),
            "profit_factor": metrics.get("profit_factor"),
            "expectancy": metrics.get("expectancy"),
            "turnover": metrics.get("turnover"),
            "days_in_market_pct": metrics.get("days_in_market_pct"),
            "exit_reasons": metrics.get("exit_reasons", {}),
            "windows": metrics.get("windows"),
            "summary": metrics.get("summary"),
            "config": backtest.config,
            "calc_version": backtest.calc_version,
        }
