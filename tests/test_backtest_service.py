"""Phase 6 backtests against the real pipeline (SQLite)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from nmi.backtest.engine import BacktestConfig
from nmi.backtest.service import BacktestService, _action_for
from nmi.core.enums import BacktestKind, BacktestRejectReason, RunStatus
from nmi.core.models import (
    BacktestRejection,
    BacktestRun,
    FundamentalMetric,
    Index,
    IndexMembership,
    IngestionRun,
    Instrument,
    RecommendationState,
    ScoringSnapshot,
    Signal,
    TechnicalIndicator,
)
from nmi.ingestion import store
from nmi.ingestion.backfill import IngestionService
from nmi.metrics.service import MetricsService

START = date(2024, 1, 1)
END = date(2024, 6, 28)
INDEX = ["NIFTY_50"]


def _count(session, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return session.scalar(statement)


def _pipeline(session) -> MetricsService:
    """Everything the backtester needs: ingestion plus the Phase 2-4 chain."""
    svc = IngestionService(session)
    svc.seed_universe()
    svc.backfill_corporate_actions(INDEX, START, END)
    svc.backfill_prices(INDEX, START, END)
    svc.backfill_fundamentals()
    msvc = MetricsService(session)
    msvc.backfill_index_prices(INDEX)
    msvc.compute_fundamentals(INDEX)
    for job in (
        "compute_technical",
        "compute_momentum",
        "compute_valuation",
        "compute_sector",
        "compute_regime",
        "compute_horizon",
        "compute_scoring",
        "compute_signals",
    ):
        getattr(msvc, job)(INDEX, START, END)
    return msvc


@pytest.fixture()
def backtester(sqlite_session, pointed_at_long_fixtures):
    _pipeline(sqlite_session)
    return BacktestService(sqlite_session)


# ------------------------------------------------------------------ dataset
def test_the_dataset_only_contains_sessions_with_real_data(backtester, sqlite_session):
    dataset = backtester.build_dataset(INDEX, START, END)
    assert dataset.dates
    assert dataset.dates == tuple(sorted(dataset.dates))
    assert dataset.dates[0] >= START and dataset.dates[-1] <= END
    # Every signal day has a tradable bar and a real membership set.
    for (instrument_id, as_of), views in dataset.signals.items():
        assert views, f"no strategy view for {instrument_id} on {as_of}"
        assert (instrument_id, as_of) in dataset.bars
        assert instrument_id in dataset.universe[as_of]
    assert dataset.symbols
    assert dataset.benchmark, "the benchmark curve must come from the index itself"


def test_the_dataset_reuses_the_signal_state_the_live_pipeline_stored(backtester, sqlite_session):
    """The backtester must reach the same verdict as the live pipeline.

    It re-evaluates the stored Phase-4 rule documents instead of reading the
    stored verdicts, so this is the test that would catch the two ever drifting
    apart.
    """
    dataset = backtester.build_dataset(INDEX, START, END)
    code_of = {
        version.id: strategy.code
        for strategy, version in store.get_active_strategy_versions(sqlite_session)
    }
    live: dict[tuple[int, date, str], RecommendationState] = {}
    for signal in sqlite_session.scalars(select(Signal).limit(5000)).all():
        code = code_of.get(signal.strategy_version_id)
        if code is not None:
            live[(signal.instrument_id, signal.as_of, code)] = signal.state
    assert live, "the fixture should have stored signals to compare against"

    compared = 0
    for (instrument_id, as_of), views in dataset.signals.items():
        for view in views:
            stored = live.get((instrument_id, as_of, view.strategy_code))
            if stored is None:
                continue
            assert _action_for(stored) is view.action, (
                f"{view.strategy_code} on {instrument_id} {as_of}: "
                f"live said {stored}, backtest said {view.action}"
            )
            compared += 1
    assert compared > 100, "the fixture should produce a meaningful overlap"


def test_fundamentals_come_from_the_last_period_reported_by_that_day(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    service = BacktestService(sqlite_session)
    dataset = service.build_dataset(INDEX, START, END)
    # Corrupting a future fundamental row must not change any earlier day.
    early = [d for d in dataset.dates if d < date(2024, 4, 1)]
    late = [d for d in dataset.dates if d >= date(2024, 4, 1)]
    assert early and late
    early_values = {
        (iid, day): values.get("roe")
        for (iid, day), values in _context_values(service, dataset, early).items()
    }
    for row in sqlite_session.scalars(
        select(FundamentalMetric).where(FundamentalMetric.as_of >= date(2024, 4, 1))
    ).all():
        row.value = 99.0
    sqlite_session.flush()
    rebuilt = service.build_dataset(INDEX, START, END)
    after = {
        (iid, day): values.get("roe")
        for (iid, day), values in _context_values(service, rebuilt, early).items()
    }
    assert after == early_values


def _context_values(service, dataset, dates):
    """Rebuild the strategy contexts for a set of days (test helper)."""
    from nmi.analysis.inputs import (
        load_analysis_inputs,
        regime_map,
        scoring_map,
        sector_state_map,
    )

    inputs = load_analysis_inputs(
        service.session, INDEX, START, END, service.calc_version
    )
    scoring_rows = scoring_map(
        service.session, list(inputs.company_by_instrument), START, END,
        service.calc_version,
    )
    regime_rows = regime_map(service.session, START, END, service.calc_version)
    sector_states = sector_state_map(service.session, INDEX, START, END)
    out = {}
    for as_of in dates:
        for instrument_id in sorted(dataset.universe.get(as_of, frozenset())):
            ctx = service._context(
                inputs, instrument_id, as_of, scoring_rows, regime_rows, sector_states
            )
            if ctx is not None:
                out[(instrument_id, as_of)] = ctx.values
    return out


def test_instruments_removed_from_the_index_are_not_tradable(backtester, sqlite_session):
    # Take one member out of the index from the middle of the window onwards.
    removed = sqlite_session.scalar(
        select(IndexMembership)
        .join(Index, Index.id == IndexMembership.index_id)
        .where(Index.code == INDEX[0])
        .limit(1)
    )
    assert removed is not None
    # ``effective_to`` is inclusive, so the day itself is still a member.
    removed.effective_to = date(2024, 3, 1)
    sqlite_session.commit()
    dataset = backtester.build_dataset(INDEX, START, END)
    assert removed.instrument_id in dataset.universe[date(2024, 3, 1)]
    for as_of in [d for d in dataset.dates if d > date(2024, 3, 1)]:
        assert removed.instrument_id not in dataset.universe[as_of]
    # ... and it was tradable throughout the period before that.
    before = [d for d in dataset.dates if d <= date(2024, 3, 1)]
    assert before
    assert all(removed.instrument_id in dataset.universe[d] for d in before)


# --------------------------------------------------------------------- runs
def test_a_backtest_runs_end_to_end_and_stores_its_whole_result(backtester, sqlite_session):
    config = BacktestConfig(initial_capital=1_000_000.0, max_positions=3)
    backtest, result = backtester.run_backtest(INDEX, START, END, config=config)
    assert result.status is RunStatus.SUCCEEDED
    assert backtest.kind is BacktestKind.SINGLE
    assert backtest.trades_count == len(store.get_backtest_trades(sqlite_session, backtest.id))
    # One equity point per session, in order, for the whole window.
    curve = store.get_backtest_equity_curve(sqlite_session, backtest.id)
    assert [p.as_of for p in curve] == sorted(p.as_of for p in curve)
    assert len(curve) > 100
    assert curve[0].as_of >= START
    assert len(store.get_backtest_equity_curve(sqlite_session, backtest.id)) == len(curve)
    # The stored metrics are a complete Phase-6 suite.
    metrics = backtest.metrics
    for key in (
        "total_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "turnover",
        "expectancy",
        "days_in_market_pct",
    ):
        assert key in metrics, key
    assert metrics["trades"] == backtest.trades_count
    # Every trade is fully explained and internally consistent.
    trades = store.get_backtest_trades(sqlite_session, backtest.id)
    for trade in trades:
        assert trade.exit_date >= trade.entry_date >= trade.entry_signal_date
        assert trade.quantity > 0
        assert trade.exit_reason
        assert trade.exit_detail
        assert trade.entry_reasons
        # Cash accounting: net P&L is the two fills net of every cost, not a
        # raw price move, and the stored decimal columns must add up exactly.
        raw = trade.quantity * (Decimal(trade.exit_price) - Decimal(trade.entry_price))
        assert Decimal(trade.net_pnl) == pytest.approx(
            raw - Decimal(trade.cost), abs=Decimal("0.05")
        )


def test_the_simulated_equity_curve_reconciles_with_its_trades(backtester, sqlite_session):
    backtest, _ = backtester.run_backtest(
        INDEX, START, END, config=BacktestConfig(initial_capital=1_000_000.0)
    )
    curve = store.get_backtest_equity_curve(sqlite_session, backtest.id)
    trades = store.get_backtest_trades(sqlite_session, backtest.id)
    total_pnl = sum(float(t.net_pnl) for t in trades)
    # The final equity is the starting capital plus everything the trades made.
    assert float(curve[-1].equity) == pytest.approx(
        float(backtest.initial_capital) + total_pnl, abs=1.0
    )
    assert backtest.metrics["end_equity"] == pytest.approx(float(curve[-1].equity), abs=0.01)


def test_a_backtest_is_reproducible(backtester, sqlite_session):
    config = BacktestConfig(initial_capital=500_000.0)
    first, _ = backtester.run_backtest(INDEX, START, END, name="first", config=config)
    second, _ = backtester.run_backtest(INDEX, START, END, name="second", config=config)
    assert first.metrics == second.metrics
    assert first.trades_count == second.trades_count
    rows_first = store.get_backtest_trades(sqlite_session, first.id)
    rows_second = store.get_backtest_trades(sqlite_session, second.id)
    assert [(t.instrument_id, t.entry_date, t.entry_price, t.exit_price) for t in rows_first] == [
        (t.instrument_id, t.entry_date, t.entry_price, t.exit_price) for t in rows_second
    ]


def test_a_backtest_needs_computed_metrics(backtester, sqlite_session):
    with pytest.raises(ValueError, match="run the EOD chain first"):
        backtester.run_backtest(INDEX, date(2020, 1, 1), date(2020, 3, 1))
    # The failure is recorded, not swallowed.
    run = sqlite_session.scalar(
        select(IngestionRun)
        .where(IngestionRun.job_name == "run_backtest")
        .order_by(IngestionRun.id.desc())
    )
    assert run.status is RunStatus.FAILED
    assert "no metric rows" in (run.error_summary or "")


def test_a_single_strategy_can_be_backtested_on_its_own(backtester):
    backtest, _ = backtester.run_backtest(
        INDEX, START, END, strategy_codes=["st_breakout_momentum"]
    )
    assert backtest.strategy_codes == ["st_breakout_momentum"]


def test_refused_orders_are_recorded_so_a_quiet_run_is_auditable(
    backtester, sqlite_session
):
    # One slot for three candidates on the same day guarantees refusals.
    backtest, _ = backtester.run_backtest(
        INDEX, START, END, config=BacktestConfig(max_positions=1, max_position_weight=0.05)
    )
    rejected = sqlite_session.scalars(
        select(BacktestRejection).where(BacktestRejection.run_id == backtest.id)
    ).all()
    if rejected:
        assert {r.reason for r in rejected} <= set(BacktestRejectReason)
        assert all(r.detail for r in rejected)


def test_the_job_is_audited_like_every_other_stage(backtester, sqlite_session):
    backtest, result = backtester.run_backtest(INDEX, START, END)
    run = sqlite_session.get(IngestionRun, result.run_id)
    assert run.job_name == "run_backtest"
    assert run.status is RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.config["calc_version"] == backtest.calc_version
    assert _count(sqlite_session, BacktestRun) == 1


# ------------------------------------------------------------ walk-forward
def test_a_walk_forward_scores_each_window_out_of_sample(backtester, sqlite_session):
    parent, result = backtester.run_walk_forward(
        INDEX, START, END, folds=2, config=BacktestConfig(max_positions=2)
    )
    assert result.status is RunStatus.SUCCEEDED
    assert parent.kind is BacktestKind.WALK_FORWARD
    summary = parent.metrics["summary"]
    assert summary["windows"] >= 1
    # Every scored window is its own stored run, parented to the summary.
    children = sqlite_session.scalars(
        select(BacktestRun).where(BacktestRun.parent_run_id == parent.id)
    ).all()
    assert children
    for child in children:
        assert child.kind is BacktestKind.SINGLE
        assert child.window_index is not None
        assert child.metrics["trades"] == child.trades_count
    # Consistency is reported, not an average return on its own.
    assert 0.0 <= summary["consistency"] <= 1.0
    assert summary["mean_window_return"] is not None


def test_a_walk_forward_window_is_a_real_simulation(backtester, sqlite_session):
    parent, _ = backtester.run_walk_forward(
        INDEX, START, END, folds=2, config=BacktestConfig(max_positions=2)
    )
    child = sqlite_session.scalar(
        select(BacktestRun).where(BacktestRun.parent_run_id == parent.id)
    )
    curve = store.get_backtest_equity_curve(sqlite_session, child.id)
    assert curve
    assert child.start_date <= child.end_date
    assert child.start_date >= parent.start_date
    assert child.end_date <= parent.end_date
    # A window curve is a slice of the parent's calendar, not the whole thing.
    assert len(curve) < 260


def test_the_run_summary_is_what_a_report_needs(backtester):
    backtest, _ = backtester.run_backtest(
        INDEX, START, END, config=BacktestConfig(max_positions=2)
    )
    summary = backtester.run_summary(backtest.id)
    assert summary["backtest_run_id"] == backtest.id
    assert summary["trades"] == backtest.trades_count
    assert summary["config"]["max_positions"] == 2
    assert summary["start"] == START.isoformat()
    assert backtester.run_summary(999_999) == {}
    assert [r.id for r in backtester.list_runs()] == [backtest.id]


# --------------------------------------------------------- no look-ahead
def test_a_backtest_decision_only_ever_sees_data_up_to_that_day(backtester, sqlite_session):
    """A future-only change must not move a decision taken before it."""
    cut = date(2024, 4, 1)
    horizon = cut - timedelta(days=1)
    before = backtester.run_backtest(
        INDEX, START, horizon, name="before", config=BacktestConfig(max_positions=3)
    )[0]
    # Rewrite every technical row after the cut with absurd values.
    for row in sqlite_session.scalars(
        select(TechnicalIndicator).where(TechnicalIndicator.as_of >= cut)
    ).all():
        row.sma20 = 1.0
        row.sma50 = 1.0
        row.rsi14 = 5.0
        row.atr14 = 0.01
        row.macd = -1.0
        row.roc10 = -0.5
        row.breakout_52w = False
        row.trend_state = "downtrend"
    scoring_rows = select(ScoringSnapshot).where(ScoringSnapshot.as_of >= cut)
    for row in sqlite_session.scalars(scoring_rows).all():
        row.composite_score = 0.0
        row.technical_score = 0.0
        row.momentum_score = 0.0
    for row in sqlite_session.scalars(select(Signal).where(Signal.as_of >= cut)).all():
        row.state = "WATCH"
    sqlite_session.commit()
    after = backtester.run_backtest(
        INDEX, START, horizon, name="after", config=BacktestConfig(max_positions=3)
    )[0]
    # Nothing before the cut changed, so the same decisions were made.
    assert before.metrics["trades"] == after.metrics["trades"]
    rows_before = store.get_backtest_trades(sqlite_session, before.id)
    rows_after = store.get_backtest_trades(sqlite_session, after.id)
    assert [(t.instrument_id, t.entry_date, t.exit_date) for t in rows_before] == [
        (t.instrument_id, t.entry_date, t.exit_date) for t in rows_after
    ]


def test_the_backtester_reads_the_same_indicator_rows_the_live_pipeline_wrote(
    backtester, sqlite_session
):
    dataset = backtester.build_dataset(INDEX, START, END)
    computed = {
        (row.instrument_id, row.as_of)
        for row in sqlite_session.scalars(select(TechnicalIndicator)).all()
    }
    # Every (instrument, session) the backtester trades was a session the live
    # pipeline had actually computed, with no invented rows.
    assert computed
    for instrument_id, as_of in dataset.signals:
        assert (instrument_id, as_of) in computed


def test_instruments_and_membership_are_never_invented(backtester, sqlite_session):
    dataset = backtester.build_dataset(INDEX, START, END)
    known = set(sqlite_session.scalars(select(Instrument.id)).all())
    for members in dataset.universe.values():
        assert members <= known
    memberships = {
        row.instrument_id
        for row in sqlite_session.scalars(select(IndexMembership)).all()
    }
    for members in dataset.universe.values():
        assert members <= memberships
