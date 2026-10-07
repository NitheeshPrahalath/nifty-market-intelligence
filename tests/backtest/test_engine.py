"""Event-driven engine behaviour: fills, exits, costs and honesty rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from nmi.backtest.engine import (
    BacktestConfig,
    BacktestDataset,
    Bar,
    HeldPosition,
    SignalAction,
    SignalView,
    run_backtest,
)
from nmi.core.enums import BacktestExitReason, BacktestRejectReason

START = date(2024, 1, 1)
CONFIG = BacktestConfig(
    initial_capital=1_000_000.0,
    risk_per_trade=0.01,
    max_positions=5,
    max_position_weight=0.25,
    cost_bps=0.0,
    slippage_bps=0.0,
)


def days(count: int, start: date = START) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(count))


def flat(closes, opens=None, highs=None, lows=None, instrument_id=1) -> dict:
    bars = {}
    for i, day in enumerate(days(len(closes))):
        close = float(closes[i])
        open_ = float(opens[i]) if opens else close
        high = float(highs[i]) if highs else max(open_, close)
        low = float(lows[i]) if lows else min(open_, close)
        bars[(instrument_id, day)] = Bar(open=open_, high=high, low=low, close=close)
    return bars


def dataset(bars, signals, universe, benchmark=None, evaluator=None, symbols=None):
    return BacktestDataset(
        dates=tuple(sorted({day for (_iid, day) in bars})),
        bars=bars,
        signals=signals,
        universe=universe,
        symbols=symbols or {1: "AAA"},
        benchmark=benchmark or {},
        exit_evaluator=evaluator,
    )


def everywhere(instruments=(1,), span=None) -> dict:
    return {day: frozenset(instruments) for day in (span if span is not None else days(5))}


def enter(stop=90.0, target=None, strength=70.0, code="s1") -> SignalView:
    return SignalView(
        action=SignalAction.ENTER,
        strategy_code=code,
        entry_low=95.0,
        entry_high=105.0,
        target_low=target,
        stop_price=stop,
        strength=strength,
        reasons=("Entry conditions met (3/3).",),
    )


def test_a_signal_fills_at_the_next_open_not_the_signal_close():
    # Day 0 closes at 100 (signal), but day 1 gaps up to 120.
    wide = enter()
    bars = flat([100, 120, 120], opens=[100, 120, 120])
    signals = {(1, START): (replace(wide, entry_low=90.0, entry_high=130.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), CONFIG)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_signal_date == START
    assert trade.entry_date == START + timedelta(days=1)
    # 120 is the day-1 open; using day 0's close of 100 would be look-ahead.
    assert trade.entry_price == pytest.approx(120.0)
    assert trade.exit_reason is BacktestExitReason.END_OF_TEST
    assert trade.exit_date == START + timedelta(days=2)


def test_slippage_and_costs_are_charged_on_both_sides():
    bars = flat([100, 100, 110], opens=[100, 100, 110])
    signals = {(1, START): (enter(),)}
    config = BacktestConfig(**{**CONFIG.as_dict(), "slippage_bps": 50.0, "cost_bps": 100.0})
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), config)
    trade = result.trades[0]
    # Buy 0.5% above, sell 0.5% below the 110 close.
    assert trade.entry_price == pytest.approx(100.0 * 1.005)
    assert trade.exit_price == pytest.approx(110.0 * 0.995)
    # Cost is 1% of the notional on each side.
    expected_cost = 0.01 * (trade.quantity * trade.entry_price + trade.quantity * trade.exit_price)
    assert trade.cost == pytest.approx(expected_cost)
    # Net P&L must equal the two fills net of costs, not the raw price move.
    assert trade.net_pnl == pytest.approx(
        trade.quantity * (trade.exit_price - trade.entry_price) - trade.cost
    )
    assert trade.gross_pnl == pytest.approx(
        trade.quantity * (trade.exit_price - trade.entry_price)
    )


def test_a_stop_hit_intraday_exits_at_the_stop():
    bars = flat([100, 100, 100], opens=[100, 100, 100], lows=[100, 88, 100])
    signals = {(1, START): (enter(stop=95.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), CONFIG)
    trade = result.trades[0]
    assert trade.exit_reason is BacktestExitReason.STOP_LOSS
    assert trade.exit_date == START + timedelta(days=1)
    assert trade.exit_price == pytest.approx(95.0)
    assert "Stop 95.00 hit" in trade.exit_detail


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    # Filled on day 1 at 100 with a 95 stop; day 2 opens at 80. A real portfolio
    # cannot get out at 95, so the fill is the open.
    bars = flat([100, 100, 100], opens=[100, 100, 80], lows=[100, 100, 78])
    signals = {(1, START): (enter(stop=95.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), CONFIG)
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_reason is BacktestExitReason.STOP_LOSS
    assert trade.exit_date == START + timedelta(days=2)
    assert trade.exit_price == pytest.approx(80.0)


def test_a_target_reached_intraday_exits_at_the_target():
    bars = flat([100, 100, 100], opens=[100, 100, 100], highs=[100, 130, 100])
    signals = {(1, START): (enter(target=125.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), CONFIG)
    trade = result.trades[0]
    assert trade.exit_reason is BacktestExitReason.TARGET
    assert trade.exit_price == pytest.approx(125.0)


def test_the_stop_wins_when_a_bar_hits_both_levels():
    # Ambiguous bars are resolved against the position, not in its favour.
    bars = flat([100, 100, 100], opens=[100, 100, 100], highs=[100, 130, 100], lows=[100, 90, 100])
    signals = {(1, START): (enter(stop=95.0, target=125.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), CONFIG)
    assert result.trades[0].exit_reason is BacktestExitReason.STOP_LOSS


def test_a_time_stop_closes_at_the_close_of_the_day_it_expires():
    bars = flat([100, 100, 100, 100, 100], opens=[100, 100, 100, 100, 100])
    signals = {(1, START): (enter(),)}
    config = BacktestConfig(**{**CONFIG.as_dict(), "max_holding_days": 2})
    result = run_backtest(dataset(bars, signals, everywhere(span=days(5))), config)
    trade = result.trades[0]
    assert trade.exit_reason is BacktestExitReason.TIME_STOP
    assert trade.exit_date == START + timedelta(days=3)
    assert trade.holding_days == 2


def test_a_name_removed_from_the_index_is_liquidated():
    span = days(4)
    bars = flat([100, 100, 100, 100])
    signals = {(1, START): (enter(),)}
    universe = {
        span[0]: frozenset({1}),
        span[1]: frozenset({1}),
        span[2]: frozenset(),  # dropped from the index
        span[3]: frozenset(),
    }
    result = run_backtest(dataset(bars, signals, universe), CONFIG)
    trade = result.trades[0]
    assert trade.exit_reason is BacktestExitReason.LIQUIDATION
    assert trade.exit_date == span[2]
    assert "Removed from the index" in trade.exit_detail


def test_an_order_never_fills_after_the_name_left_the_index():
    span = days(3)
    bars = flat([100, 100, 100])
    signals = {(1, START): (enter(),)}
    universe = {span[0]: frozenset({1}), span[1]: frozenset(), span[2]: frozenset()}
    result = run_backtest(dataset(bars, signals, universe), CONFIG)
    assert result.trades == ()
    reasons = {r.reason for r in result.rejections}
    assert BacktestRejectReason.NOT_A_MEMBER in reasons
    rejection = next(r for r in result.rejections if r.reason is BacktestRejectReason.NOT_A_MEMBER)
    assert rejection.as_of == span[1]
    assert rejection.side == "BUY"


def test_a_limit_buy_above_the_zone_never_chases_the_price():
    # The zone tops out at 105, but every open after the signal is above it.
    bars = flat([100, 130, 130, 130], opens=[100, 130, 130, 130])
    signals = {(1, START): (enter(),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(4))), CONFIG)
    assert result.trades == ()
    assert [r.reason for r in result.rejections] == [
        BacktestRejectReason.LIMIT_PRICE_MISSED
    ]
    # The order stayed alive for the configured three sessions.
    assert result.rejections[0].detail.startswith("open 130.00 above the 105.00 limit")


def test_a_limit_buy_fills_when_the_price_comes_back_into_the_zone():
    bars = flat([100, 110, 104, 104], opens=[100, 110, 104, 104])
    signals = {(1, START): (enter(),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(4))), CONFIG)
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == START + timedelta(days=2)
    assert result.trades[0].entry_price == pytest.approx(104.0)


def test_candidate_entries_compete_for_slots_by_strength():
    span = days(4)
    bars = {}
    signals = {}
    universe = {}
    for instrument_id, strength in ((1, 40.0), (2, 90.0), (3, 60.0)):
        for day in span:
            bars[(instrument_id, day)] = Bar(100.0, 100.0, 100.0, 100.0)
        signals[(instrument_id, START)] = (enter(strength=strength, code=f"s{instrument_id}"),)
    universe = {day: frozenset({1, 2, 3}) for day in span}
    config = BacktestConfig(**{**CONFIG.as_dict(), "max_positions": 2})
    result = run_backtest(
        dataset(bars, signals, universe, symbols={1: "AAA", 2: "BBB", 3: "CCC"}), config
    )
    # The two strongest candidates (90 then 60) take the two slots.
    assert [t.instrument_id for t in result.trades] == [2, 3]
    # The weakest is turned away for want of a slot, and the decision is recorded
    # rather than silently dropped.
    rejected = [r for r in result.rejections if r.reason is BacktestRejectReason.NO_FREE_SLOT]
    assert [r.instrument_id for r in rejected] == [1]


def test_position_size_follows_the_risk_budget():
    # 1% of 1,000,000 risked over a 10-point stop => 1,000 shares.
    bars = flat([100, 100, 100, 100])
    signals = {(1, START): (enter(stop=90.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(4))), CONFIG)
    assert result.trades[0].quantity == 1_000


def test_position_size_never_exceeds_the_weight_cap_or_available_cash():
    # A 1-point stop would allow 10,000 shares, but 25% of the book caps it.
    bars = flat([100, 100, 100, 100])
    signals = {(1, START): (enter(stop=99.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(4))), CONFIG)
    trade = result.trades[0]
    assert trade.quantity == 2_500  # 25% of 1,000,000 at 100 a share
    assert trade.quantity * trade.entry_price <= CONFIG.max_position_weight * CONFIG.initial_capital


def test_cash_never_goes_negative_across_many_positions():
    span = days(6)
    bars = {}
    signals = {}
    universe = {}
    for instrument_id in range(1, 6):
        for day in span:
            bars[(instrument_id, day)] = Bar(50.0, 50.0, 50.0, 50.0)
        signals[(instrument_id, START)] = (enter(stop=49.9, code=f"s{instrument_id}"),)
    universe = {day: frozenset(range(1, 6)) for day in span}
    config = BacktestConfig(
        initial_capital=1_000.0,
        risk_per_trade=0.5,
        max_positions=5,
        max_position_weight=0.9,
        cost_bps=0.0,
        slippage_bps=0.0,
    )
    result = run_backtest(dataset(bars, signals, universe), config)
    assert all(point.cash >= -1e-6 for point in result.points)
    # Once the book is spent the engine refuses new orders and says why, rather
    # than quietly buying on margin.
    assert result.rejections
    assert {r.reason for r in result.rejections} <= {
        BacktestRejectReason.NO_CASH,
        BacktestRejectReason.BELOW_LOT,
    }
    assert max(p.cash for p in result.points) <= 1_000.0


def test_a_missing_invalidation_price_falls_back_to_a_default_stop():
    view = SignalView(
        action=SignalAction.ENTER,
        strategy_code="s1",
        entry_low=95.0,
        entry_high=105.0,
        stop_price=None,
    )
    bars = flat([100, 100, 100])
    signals = {(1, START): (view,)}
    config = BacktestConfig(**{**CONFIG.as_dict(), "default_stop_pct": 0.10})
    result = run_backtest(dataset(bars, signals, everywhere(span=days(3))), config)
    trade = result.trades[0]
    assert trade.stop_price == pytest.approx(100.0 * 0.90)
    assert any("assumed a 10% risk stop" in reason for reason in trade.entry_reasons)


def test_the_position_aware_exit_evaluator_drives_exits_and_records_why():
    bars = flat([100, 100, 100, 100, 100])
    signals = {(1, START): (enter(),)}
    seen: list[HeldPosition] = []

    def evaluator(held: HeldPosition, as_of: date) -> SignalView | None:
        seen.append(held)
        if as_of < START + timedelta(days=3):
            return None
        return SignalView(
            action=SignalAction.EXIT,
            strategy_code=held.strategy_code,
            reasons=("Drawdown breached the risk limit.",),
            exit_reason=BacktestExitReason.RISK_EXIT,
            exit_detail="Drawdown breached the risk limit.",
        )

    result = run_backtest(
        dataset(bars, signals, everywhere(span=days(5)), evaluator=evaluator), CONFIG
    )
    trade = result.trades[0]
    assert trade.exit_reason is BacktestExitReason.RISK_EXIT
    assert trade.exit_reasons == ("Drawdown breached the risk limit.",)
    # The evaluator was given the real position, not a guess at it.
    assert seen[0].entry_price == pytest.approx(100.0)
    assert seen[0].strategy_code == "s1"
    assert seen[-1].sessions_held >= 3
    # A decision on day 3 fills on day 4, never on the same day.
    assert trade.exit_date == START + timedelta(days=4)


def test_a_review_only_evaluator_view_does_not_close_the_position():
    bars = flat([100, 100, 100, 100])
    signals = {(1, START): (enter(),)}
    evaluator = lambda held, as_of: SignalView(  # noqa: E731
        action=SignalAction.HOLD, strategy_code=held.strategy_code
    )
    result = run_backtest(
        dataset(bars, signals, everywhere(span=days(4)), evaluator=evaluator), CONFIG
    )
    assert result.trades[0].exit_reason is BacktestExitReason.END_OF_TEST


def test_excursions_are_measured_over_the_whole_holding_period():
    bars = flat(
        [100, 100, 100, 100, 100],
        opens=[100, 100, 100, 100, 100],
        highs=[100, 130, 100, 100, 100],
        lows=[100, 100, 100, 80, 100],
    )
    signals = {(1, START): (enter(stop=70.0),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(5))), CONFIG)
    trade = result.trades[0]
    assert trade.mfe_pct == pytest.approx(30.0)
    assert trade.mae_pct == pytest.approx(-20.0)
    # Neither excursion was acted on, so the trade survived to the end.
    assert trade.exit_reason is BacktestExitReason.END_OF_TEST


def test_future_data_cannot_change_the_past():
    """The definitive no-look-ahead check.

    Everything after the cut is replaced with absurd values. If any decision
    ever peeked forward, the trades up to the cut would move.
    """
    span = days(12)
    cut = span[6]
    closes = [100 + i for i in range(len(span))]
    baseline_bars = flat(closes)
    signals = {}
    for i, day in enumerate(span):
        if i % 2 == 0:
            signals[(1, day)] = (
                SignalView(
                    action=SignalAction.ENTER,
                    strategy_code="s1",
                    entry_low=90.0,
                    entry_high=1_000.0,
                    stop_price=50.0,
                    strength=50.0 + i,
                ),
            )
    baseline = run_backtest(
        dataset(baseline_bars, signals, everywhere(span=span)), CONFIG
    )

    poisoned = dict(baseline_bars)
    for day in span:
        if day >= cut:
            poisoned[(1, day)] = Bar(9_999.0, 9_999.0, 1.0, 9_999.0)
    poisoned_signals = {
        key: views for key, views in signals.items() if key[1] < cut
    }
    after = run_backtest(
        dataset(poisoned, poisoned_signals, everywhere(span=span)), CONFIG
    )

    def opens(result):
        return [
            (t.instrument_id, t.entry_signal_date, t.entry_date, t.entry_price, t.quantity)
            for t in result.trades
            if t.entry_date < cut
        ]

    baseline_opens = opens(baseline)
    assert baseline_opens, "the fixture should open at least one position before the cut"
    # Every fill that happened before the cut is identical, down to the share
    # count, and so is the equity curve. (The cut day itself is excluded on
    # purpose: a fill at its open is legitimately the first thing the poison
    # can touch, because it happens *after* the last decision it could affect.)
    assert opens(after) == baseline_opens
    before_curve = [p.equity for p in baseline.points if p.as_of < cut]
    after_curve = [p.equity for p in after.points if p.as_of < cut]
    assert before_curve == after_curve


def test_the_same_dataset_always_produces_the_same_result():
    bars = flat([100, 101, 99, 103, 98])
    signals = {(1, START): (enter(),)}
    data = dataset(bars, signals, everywhere(span=days(5)))
    first = run_backtest(data, CONFIG)
    second = run_backtest(data, CONFIG)
    assert first.trades == second.trades
    assert first.points == second.points
    assert first.metrics.as_dict() == second.metrics.as_dict()


def test_a_benchmark_curve_is_rebased_to_the_starting_capital():
    bars = flat([100, 100, 100])
    benchmark = {day: 100.0 + 10 * i for i, day in enumerate(days(3))}
    signals = {(1, START): (enter(),)}
    result = run_backtest(
        dataset(bars, signals, everywhere(span=days(3)), benchmark=benchmark), CONFIG
    )
    assert result.benchmark_curve[0] == pytest.approx(CONFIG.initial_capital)
    assert result.benchmark_curve[-1] == pytest.approx(CONFIG.initial_capital * 1.2)
    assert result.metrics.benchmark_return == pytest.approx(0.2)


def test_equity_is_marked_to_market_every_day():
    bars = flat([100, 100, 110, 110])
    signals = {(1, START): (enter(),)}
    result = run_backtest(dataset(bars, signals, everywhere(span=days(4))), CONFIG)
    equities = [p.equity for p in result.points]
    # Day 2 is the first day the 1,000 shares are worth 110.
    assert equities[2] == pytest.approx(CONFIG.initial_capital + 1_000 * 10)
    assert [p.positions for p in result.points] == [0, 1, 1, 0]
    # Exposure is reported as a share of equity, not a fraction.
    assert result.points[1].exposure_pct == pytest.approx(10.0)
    assert result.points[0].drawdown_pct == pytest.approx(0.0)


def test_a_backtest_needs_at_least_one_session():
    with pytest.raises(ValueError, match="at least one session"):
        run_backtest(BacktestDataset(dates=(), bars={}, signals={}, universe={}), CONFIG)
