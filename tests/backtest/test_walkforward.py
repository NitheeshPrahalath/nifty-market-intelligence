"""Walk-forward window construction and cross-window consistency."""

from __future__ import annotations

from datetime import date

import pytest

from nmi.backtest.metrics import EquityPoint, PerformanceMetrics, TradeResult, summarize
from nmi.backtest.walkforward import (
    WindowOutcome,
    summarize_windows,
    walk_forward_windows,
)


def test_windows_are_consecutive_and_cover_the_whole_period():
    windows = walk_forward_windows(date(2024, 1, 1), date(2025, 1, 1), folds=4)
    assert [w.index for w in windows] == [0, 1, 2, 3]
    assert windows[0].test_start == date(2024, 1, 1)
    assert windows[-1].test_end == date(2025, 1, 1)
    # No gaps and no overlaps between out-of-sample windows.
    for earlier, later in zip(windows, windows[1:], strict=False):
        assert earlier.test_end == later.test_start


def test_the_last_window_always_ends_on_the_requested_end_date():
    windows = walk_forward_windows(date(2024, 1, 1), date(2024, 7, 17), folds=3)
    assert windows[-1].test_end == date(2024, 7, 17)


def test_train_windows_precede_their_test_window_and_stay_inside_history():
    windows = walk_forward_windows(date(2024, 1, 1), date(2024, 12, 31), folds=4, train_ratio=0.5)
    # The first window has no history behind it to train on.
    assert windows[0].train_start is None
    for window in windows[1:]:
        assert window.train_start is not None
        assert window.train_start < window.test_start
        assert window.train_end == date.fromordinal(window.test_start.toordinal() - 1)
        assert window.train_start >= date(2024, 1, 1)


def test_a_bad_window_request_is_refused():
    with pytest.raises(ValueError, match="at least one fold"):
        walk_forward_windows(date(2024, 1, 1), date(2024, 6, 1), folds=0)
    with pytest.raises(ValueError, match="end after it starts"):
        walk_forward_windows(date(2024, 6, 1), date(2024, 1, 1), folds=2)
    with pytest.raises(ValueError, match="train_ratio"):
        walk_forward_windows(date(2024, 1, 1), date(2024, 6, 1), folds=2, train_ratio=1.5)


def _metrics(curve, pnls=()) -> PerformanceMetrics:
    points = [
        EquityPoint(
            as_of=date(2024, 1, 1),
            equity=value,
            cash=value,
            positions=0,
            exposure_pct=0.0,
            drawdown_pct=0.0,
        )
        for value in curve
    ]
    trades = [
        TradeResult(net_pnl=pnl, return_pct=0.0, holding_days=1, exit_reason="TARGET")
        for pnl in pnls
    ]
    return summarize(points, trades)


def test_consistency_counts_windows_not_average_returns():
    windows = walk_forward_windows(date(2024, 1, 1), date(2024, 5, 1), folds=4)
    outcomes = [
        # One big winner and three flat windows: profitable, but not consistent.
        WindowOutcome(window=windows[0], metrics=_metrics([100.0, 300.0])),
        WindowOutcome(window=windows[1], metrics=_metrics([100.0, 100.0])),
        WindowOutcome(window=windows[2], metrics=_metrics([100.0, 100.0])),
        WindowOutcome(window=windows[3], metrics=_metrics([100.0, 100.0])),
    ]
    summary = summarize_windows(outcomes)
    assert summary["windows"] == 4
    assert summary["profitable_windows"] == 1
    assert summary["consistency"] == pytest.approx(0.25)
    assert summary["best_window_return"] == pytest.approx(2.0)
    assert summary["worst_window_return"] == pytest.approx(0.0)
    assert summary["mean_window_return"] == pytest.approx(0.5)
    assert summary["median_window_return"] == pytest.approx(0.0)
    assert summary["stdev_window_return"] > 0


def test_an_empty_walk_forward_reports_nothing_rather_than_guessing():
    summary = summarize_windows([])
    assert summary["windows"] == 0
    assert summary["consistency"] is None
    assert summary["mean_window_return"] is None


def test_a_window_outcome_carries_its_own_window_and_metrics():
    windows = walk_forward_windows(date(2024, 1, 1), date(2024, 3, 1), folds=2)
    outcome = WindowOutcome(window=windows[0], metrics=_metrics([100.0, 110.0]))
    payload = outcome.as_dict()
    assert payload["index"] == 0
    assert payload["test_start"] == "2024-01-01"
    assert payload["metrics"]["total_return"] == pytest.approx(0.10)
