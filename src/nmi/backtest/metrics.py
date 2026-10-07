"""Performance and risk statistics for a simulated portfolio (Phase 6).

Every function here is pure and works on plain numbers so the metric suite can
be unit-tested against hand-computed examples. Definitions are stated
explicitly because "Sharpe ratio" means different things in different tools:

* returns are **simple daily** returns taken from the equity curve;
* ``cagr``  = ``(end/start) ** (365.25 / calendar_days) - 1``
* ``volatility`` = sample stdev of daily returns x ``sqrt(252)``
* ``sharpe`` = mean(excess daily) / stdev(daily) x ``sqrt(252)``
* ``sortino``= mean(daily) / downside deviation x ``sqrt(252)``
* ``max_drawdown`` = worst peak-to-trough decline of the equity curve
* ``profit_factor`` = gross profit / gross loss (absolute value)
* ``turnover`` = traded notional / average equity, annualised
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

TRADING_DAYS = 252
DAYS_PER_YEAR = 365.25


def _safe(value: float | None) -> float | None:
    """``None`` instead of NaN/inf, so results survive a JSON round trip."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def daily_returns(curve: Sequence[float]) -> list[float]:
    """Simple day-over-day returns, always one shorter than the curve.

    A return from a non-positive starting value is undefined; it is reported as
    0.0 so the series stays aligned with its curve (a short series would quietly
    shift every downstream zipped statistic).
    """
    out: list[float] = []
    for previous, current in zip(curve, curve[1:], strict=False):
        out.append((current / previous) - 1.0 if previous else 0.0)
    return out


def cagr(start: float, end: float, days: int) -> float | None:
    """Compound annual growth rate over a calendar span."""
    if start <= 0 or end <= 0 or days <= 0:
        return None
    return (end / start) ** (DAYS_PER_YEAR / days) - 1.0


def annualized_volatility(returns: Sequence[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def downside_deviation(returns: Sequence[float], mar_daily: float = 0.0) -> float | None:
    """Root mean square of the shortfall below the minimum acceptable return."""
    if not returns:
        return None
    shortfalls = [min(r - mar_daily, 0.0) for r in returns]
    return math.sqrt(sum(s * s for s in shortfalls) / len(shortfalls)) * math.sqrt(TRADING_DAYS)


def sharpe_ratio(returns: Sequence[float], risk_free_rate: float = 0.0) -> float | None:
    if len(returns) < 2:
        return None
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / TRADING_DAYS) - 1.0
    excess = [r - rf_daily for r in returns]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    if variance <= 0:
        return None
    return mean / math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def sortino_ratio(returns: Sequence[float], risk_free_rate: float = 0.0) -> float | None:
    if len(returns) < 2:
        return None
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / TRADING_DAYS) - 1.0
    mean = sum(r - rf_daily for r in returns) / len(returns)
    denominator = downside_deviation(returns, rf_daily)
    if not denominator:
        return None
    return mean / denominator


def drawdowns(curve: Sequence[float]) -> list[float]:
    """Per-point drawdown from the running peak (0 at a new high)."""
    out: list[float] = []
    peak = None
    for equity in curve:
        peak = equity if peak is None else max(peak, equity)
        out.append(equity / peak - 1.0 if peak else 0.0)
    return out


def max_drawdown(curve: Sequence[float]) -> float | None:
    series = drawdowns(curve)
    return min(series) if series else None


def max_drawdown_duration(curve: Sequence[float]) -> int:
    """Longest run of consecutive days spent below a previous peak."""
    longest = current = 0
    peak = None
    for equity in curve:
        if peak is None or equity >= peak:
            peak = equity
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def calmar_ratio(curve: Sequence[float], days: int) -> float | None:
    if len(curve) < 2 or days <= 0:
        return None
    growth = cagr(curve[0], curve[-1], days)
    drawdown = max_drawdown(curve)
    if growth is None or not drawdown:
        return None
    return growth / abs(drawdown)


def win_rate(pnls: Sequence[float]) -> float | None:
    if not pnls:
        return None
    wins = sum(1 for p in pnls if p > 0)
    return wins / len(pnls)


def profit_factor(pnls: Sequence[float]) -> float | None:
    """Gross profit over gross loss; ``None`` when there is no loss to offset."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss <= 0:
        return None
    return gross_profit / gross_loss


def expectancy(pnls: Sequence[float]) -> float | None:
    return sum(pnls) / len(pnls) if pnls else None


def max_consecutive_losses(pnls: Sequence[float]) -> int:
    longest = current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def average_holding_days(holding_days: Sequence[int]) -> float | None:
    return sum(holding_days) / len(holding_days) if holding_days else None


def annualised_turnover(traded_notional: float, average_equity: float, days: int) -> float | None:
    """Traded notional per average equity, scaled to a year."""
    if average_equity <= 0 or days <= 0:
        return None
    return traded_notional / average_equity * (DAYS_PER_YEAR / days)


def benchmark_comparison(
    curve: Sequence[float], benchmark_curve: Sequence[float]
) -> dict[str, float | None]:
    """Total and annualised excess return, beta and alpha versus a benchmark."""
    if len(curve) < 2 or len(benchmark_curve) != len(curve):
        return {"excess_return": None, "beta": None, "alpha": None, "benchmark_return": None}
    strategy_return = curve[-1] / curve[0] - 1.0
    benchmark_return = benchmark_curve[-1] / benchmark_curve[0] - 1.0
    strategy_daily = daily_returns(curve)
    benchmark_daily = daily_returns(benchmark_curve)
    beta = alpha = None
    if len(strategy_daily) >= 2:
        mean_s = sum(strategy_daily) / len(strategy_daily)
        mean_b = sum(benchmark_daily) / len(benchmark_daily)
        covariance = sum(
            (s - mean_s) * (b - mean_b)
            for s, b in zip(strategy_daily, benchmark_daily, strict=False)
        )
        variance = sum((b - mean_b) ** 2 for b in benchmark_daily)
        if variance > 0:
            beta = covariance / variance
            alpha = (mean_s - beta * mean_b) * TRADING_DAYS
    return {
        "excess_return": strategy_return - benchmark_return,
        "benchmark_return": benchmark_return,
        "beta": beta,
        "alpha": alpha,
    }


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One day of a simulated portfolio, as stored by the backtester."""

    as_of: date
    equity: float
    cash: float
    positions: int
    exposure_pct: float
    drawdown_pct: float


@dataclass(frozen=True, slots=True)
class TradeResult:
    """A closed round trip, reduced to what the metric suite needs."""

    net_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str
    entry_date: date | None = None
    exit_date: date | None = None
    mae_pct: float | None = None
    mfe_pct: float | None = None


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """The full Phase-6 metric suite for one run."""

    start: date
    end: date
    days: int
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float | None
    volatility: float | None
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float | None
    max_drawdown_days: int
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    average_win: float | None
    average_loss: float | None
    best_trade: float | None
    worst_trade: float | None
    max_consecutive_losses: int
    average_holding_days: float | None
    turnover: float | None
    average_exposure_pct: float | None
    days_in_market_pct: float | None
    exit_reasons: dict[str, int] = field(default_factory=dict)
    benchmark_return: float | None = None
    excess_return: float | None = None
    beta: float | None = None
    alpha: float | None = None

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "start_equity": _safe(self.start_equity),
            "end_equity": _safe(self.end_equity),
            "total_return": _safe(self.total_return),
            "cagr": _safe(self.cagr),
            "volatility": _safe(self.volatility),
            "sharpe": _safe(self.sharpe),
            "sortino": _safe(self.sortino),
            "calmar": _safe(self.calmar),
            "max_drawdown": _safe(self.max_drawdown),
            "max_drawdown_days": self.max_drawdown_days,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": _safe(self.win_rate),
            "profit_factor": _safe(self.profit_factor),
            "expectancy": _safe(self.expectancy),
            "average_win": _safe(self.average_win),
            "average_loss": _safe(self.average_loss),
            "best_trade": _safe(self.best_trade),
            "worst_trade": _safe(self.worst_trade),
            "max_consecutive_losses": self.max_consecutive_losses,
            "average_holding_days": _safe(self.average_holding_days),
            "turnover": _safe(self.turnover),
            "average_exposure_pct": _safe(self.average_exposure_pct),
            "days_in_market_pct": _safe(self.days_in_market_pct),
            "exit_reasons": self.exit_reasons,
            "benchmark_return": _safe(self.benchmark_return),
            "excess_return": _safe(self.excess_return),
            "beta": _safe(self.beta),
            "alpha": _safe(self.alpha),
        }


def _days_in_market(
    points: Sequence[EquityPoint], trades: Sequence[TradeResult]
) -> int:
    """Distinct equity points covered by at least one position.

    Counting end-of-day states undercounts: a point is recorded after the day's
    exits and before the next day's fills, so the fill day and the exit day both
    read as flat, and the end-of-test liquidation zeroes the final point. A
    round trip opened and closed on the last day would report 0.0% exposure
    while its P&L still sat in the curve. The trade's own span says otherwise.
    """
    if not trades or all(t.entry_date is None for t in trades):
        return sum(1 for p in points if p.positions > 0)
    last = points[-1].as_of
    return sum(
        1
        for p in points
        if any(
            t.entry_date is not None
            and t.entry_date <= p.as_of <= (t.exit_date or last)
            for t in trades
        )
    )


def summarize(
    points: Sequence[EquityPoint],
    trades: Sequence[TradeResult],
    *,
    traded_notional: float = 0.0,
    risk_free_rate: float = 0.0,
    benchmark_curve: Sequence[float] | None = None,
) -> PerformanceMetrics:
    """Compute every headline number for one simulated run."""
    if not points:
        raise ValueError("an equity curve needs at least one point")
    curve = [p.equity for p in points]
    returns = daily_returns(curve)
    # A single point has no span, so growth and turnover are undefined.
    calendar_days = (points[-1].as_of - points[0].as_of).days
    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    average_equity = sum(curve) / len(curve)
    exposure = sum(p.exposure_pct for p in points) / len(points)
    days_in_market = _days_in_market(points, trades)
    reasons: dict[str, int] = {}
    for trade in trades:
        reasons[trade.exit_reason] = reasons.get(trade.exit_reason, 0) + 1
    comparison = (
        benchmark_comparison(curve, list(benchmark_curve))
        if benchmark_curve
        else {"excess_return": None, "beta": None, "alpha": None, "benchmark_return": None}
    )
    return PerformanceMetrics(
        start=points[0].as_of,
        end=points[-1].as_of,
        days=calendar_days,
        start_equity=curve[0],
        end_equity=curve[-1],
        total_return=(curve[-1] / curve[0] - 1.0) if curve[0] else 0.0,
        cagr=cagr(curve[0], curve[-1], calendar_days),
        volatility=annualized_volatility(returns),
        sharpe=sharpe_ratio(returns, risk_free_rate),
        sortino=sortino_ratio(returns, risk_free_rate),
        calmar=calmar_ratio(curve, calendar_days),
        max_drawdown=max_drawdown(curve),
        max_drawdown_days=max_drawdown_duration(curve),
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=win_rate(pnls),
        profit_factor=profit_factor(pnls),
        expectancy=expectancy(pnls),
        average_win=(sum(wins) / len(wins)) if wins else None,
        average_loss=(sum(losses) / len(losses)) if losses else None,
        best_trade=max(pnls) if pnls else None,
        worst_trade=min(pnls) if pnls else None,
        max_consecutive_losses=max_consecutive_losses(pnls),
        average_holding_days=average_holding_days([t.holding_days for t in trades]),
        turnover=annualised_turnover(traded_notional, average_equity, calendar_days),
        average_exposure_pct=exposure,
        days_in_market_pct=days_in_market / len(points),
        exit_reasons=reasons,
        benchmark_return=comparison["benchmark_return"],
        excess_return=comparison["excess_return"],
        beta=comparison["beta"],
        alpha=comparison["alpha"],
    )
