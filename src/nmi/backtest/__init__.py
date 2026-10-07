"""Event-driven backtesting engine (Phase 6).

Layers
------
``engine``      the pure simulator: bars in, trades and an equity curve out
``metrics``     performance statistics for a curve and a set of trades
``walkforward`` rolling train/test windows and cross-window consistency
``service``     the only module that touches the database

The engine is deliberately free of database access and of any knowledge of
``nmi.metrics`` or ``nmi.tracking``: it is handed a :class:`BacktestDataset`
and an optional exit callback. :mod:`nmi.backtest.service` is what binds the
Phase-4 strategy engine and the Phase-5 exit engine into that callback, which
is what makes a backtest comparable to what the live pipeline would have done.

Two rules the engine never breaks:

* a decision is taken on session D's close and can only be filled at session
  D+1's open, so no simulated trade can use a price it could not have had;
* every order that is *not* filled is recorded with a reason, so a quiet run
  is still auditable.
"""

from nmi.backtest.engine import (
    BacktestConfig,
    BacktestDataset,
    BacktestResult,
    Bar,
    HeldPosition,
    Rejection,
    SignalAction,
    SignalView,
    Trade,
    run_backtest,
)
from nmi.backtest.metrics import (
    EquityPoint,
    PerformanceMetrics,
    TradeResult,
    annualised_turnover,
    annualized_volatility,
    average_holding_days,
    benchmark_comparison,
    cagr,
    drawdowns,
    expectancy,
    max_consecutive_losses,
    max_drawdown,
    max_drawdown_duration,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    summarize,
    win_rate,
)
from nmi.backtest.walkforward import (
    Window,
    WindowOutcome,
    summarize_windows,
    walk_forward_windows,
)

#: Bump when simulation semantics change (fills, sizing, costs, exits).
BACKTEST_VERSION = "v1"

__all__ = [
    "BACKTEST_VERSION",
    "BacktestConfig",
    "BacktestDataset",
    "BacktestResult",
    "Bar",
    "EquityPoint",
    "HeldPosition",
    "PerformanceMetrics",
    "Rejection",
    "SignalAction",
    "SignalView",
    "Trade",
    "TradeResult",
    "Window",
    "WindowOutcome",
    "annualised_turnover",
    "annualized_volatility",
    "average_holding_days",
    "benchmark_comparison",
    "cagr",
    "drawdowns",
    "expectancy",
    "max_consecutive_losses",
    "max_drawdown",
    "max_drawdown_duration",
    "profit_factor",
    "run_backtest",
    "sharpe_ratio",
    "sortino_ratio",
    "summarize",
    "summarize_windows",
    "walk_forward_windows",
    "win_rate",
]
