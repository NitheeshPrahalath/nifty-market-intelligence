"""Walk-forward analysis (Phase 6).

A single backtest over a long window can be flattered by one lucky regime. This
module splits the history into consecutive out-of-sample windows and reports how
the strategy behaved in *each* of them, so a caller can see the consistency of a
result instead of one flattering headline number.

Only the out-of-sample ("test") windows are scored. The preceding in-sample
("train") window is exposed as well, because that is where parameters would be
chosen; nothing in this module fits or selects anything, it just carves the
history honestly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median, pstdev

from nmi.backtest.metrics import PerformanceMetrics


@dataclass(frozen=True, slots=True)
class Window:
    """One walk-forward step: an optional train window and its test window."""

    index: int
    test_start: date
    test_end: date
    train_start: date | None = None
    train_end: date | None = None

    @property
    def days(self) -> int:
        return (self.test_end - self.test_start).days

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "train_start": self.train_start.isoformat() if self.train_start else None,
            "train_end": self.train_end.isoformat() if self.train_end else None,
            "days": self.days,
        }


def walk_forward_windows(
    start: date, end: date, folds: int = 4, train_ratio: float = 0.5
) -> tuple[Window, ...]:
    """Split ``[start, end]`` into ``folds`` consecutive out-of-sample windows."""
    if folds < 1:
        raise ValueError("a walk-forward needs at least one fold")
    if end <= start:
        raise ValueError("the walk-forward window must end after it starts")
    if not 0.0 <= train_ratio < 1.0:
        raise ValueError("train_ratio must be between 0 and 1")
    total_days = (end - start).days
    test_days = total_days / folds
    train_days = int(test_days * train_ratio)
    windows: list[Window] = []
    for index in range(folds):
        test_start = start + timedelta(days=int(round(index * test_days)))
        test_end = (
            end
            if index == folds - 1
            else start + timedelta(days=int(round((index + 1) * test_days)))
        )
        train_end = test_start - timedelta(days=1)
        train_start = train_end - timedelta(days=train_days) if train_days else None
        if train_start is not None and train_start < start:
            train_start = None
            train_end = None
        windows.append(
            Window(
                index=index,
                test_start=test_start,
                test_end=test_end,
                train_start=train_start,
                train_end=train_end,
            )
        )
    return tuple(windows)


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """What the engine did in one out-of-sample window."""

    window: Window
    metrics: PerformanceMetrics

    def as_dict(self) -> dict:
        return {**self.window.as_dict(), "metrics": self.metrics.as_dict()}


def summarize_windows(outcomes: Sequence[WindowOutcome]) -> dict:
    """Cross-window consistency: the number that actually matters.

    A strategy that made money in 3 of 4 windows is a different claim from one
    that made all its money in a single window, and only this summary shows the
    difference.
    """
    if not outcomes:
        return {
            "windows": 0,
            "profitable_windows": 0,
            "consistency": None,
            "mean_window_return": None,
            "median_window_return": None,
            "stdev_window_return": None,
            "mean_window_sharpe": None,
            "best_window_return": None,
            "worst_window_return": None,
            "total_trades": 0,
        }
    returns = [o.metrics.total_return for o in outcomes]
    sharpes = [o.metrics.sharpe for o in outcomes if o.metrics.sharpe is not None]
    profitable = sum(1 for r in returns if r > 0)
    return {
        "windows": len(outcomes),
        "profitable_windows": profitable,
        "consistency": profitable / len(returns),
        "mean_window_return": sum(returns) / len(returns),
        "median_window_return": median(returns),
        "stdev_window_return": pstdev(returns) if len(returns) > 1 else 0.0,
        "mean_window_sharpe": (sum(sharpes) / len(sharpes)) if sharpes else None,
        "best_window_return": max(returns),
        "worst_window_return": min(returns),
        "total_trades": sum(o.metrics.trades for o in outcomes),
    }
