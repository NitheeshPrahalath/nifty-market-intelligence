"""Hand-checked statistics for the Phase-6 metric suite."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from nmi.backtest.metrics import (
    EquityPoint,
    TradeResult,
    annualised_turnover,
    annualized_volatility,
    benchmark_comparison,
    cagr,
    daily_returns,
    downside_deviation,
    drawdowns,
    max_consecutive_losses,
    max_drawdown,
    max_drawdown_duration,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    summarize,
    win_rate,
)


def test_daily_returns_are_simple_day_over_day():
    assert daily_returns([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])
    # A flat or degenerate curve must not divide by zero.
    # A non-positive start is undefined, not a crash: the series stays aligned.
    assert daily_returns([0.0, 10.0]) == [0.0]


def test_cagr_compounds_calendar_days():
    # 100 -> 121 is exactly +21% over 365.25 days.
    assert cagr(100.0, 121.0, 365) == pytest.approx(0.21, abs=0.002)
    # A flat curve is 0% growth, and a non-positive start is undefined.
    assert cagr(100.0, 100.0, 365) == pytest.approx(0.0)
    assert cagr(0.0, 100.0, 365) is None
    assert cagr(100.0, 100.0, 0) is None


def test_annualized_volatility_uses_252_sessions():
    returns = [0.01, -0.01] * 10
    # 20 points, sample stdev of the repeating +1%/-1% series.
    expected = math.sqrt(20 * 0.0001 / 19) * math.sqrt(252)
    assert annualized_volatility(returns) == pytest.approx(expected, rel=1e-9)
    assert annualized_volatility([0.01]) is None


def test_sortino_risk_denominator_ignores_upside_but_sharpe_does_not():
    # Same losses, same length, very different upside: total volatility moves,
    # the downside deviation does not.
    calm = [0.01, -0.02, 0.03, 0.04]
    lucky = [0.50, -0.02, 0.03, 0.04]
    assert annualized_volatility(lucky) > annualized_volatility(calm)
    assert downside_deviation(lucky) == pytest.approx(downside_deviation(calm))
    # A series with no variation has no defined ratio rather than an infinity.
    assert sharpe_ratio([0.01, 0.01, 0.01]) is None
    assert sortino_ratio([0.01, 0.01, 0.01]) is None


def test_downside_deviation_only_counts_shortfalls():
    # Never short of the target: zero downside, so Sortino is undefined.
    assert downside_deviation([0.01, 0.02]) == 0.0
    assert sortino_ratio([0.01, 0.02]) is None
    # One -1% day in two, nothing on the other day.
    assert downside_deviation([-0.01, 0.01]) == pytest.approx(
        math.sqrt(0.0001 / 2) * math.sqrt(252)
    )


def test_drawdown_is_measured_from_the_running_peak():
    curve = [100.0, 120.0, 90.0, 110.0]
    assert drawdowns(curve) == pytest.approx([0.0, 0.0, -0.25, -0.08333333])
    assert max_drawdown(curve) == pytest.approx(-0.25)
    # Days 2, 3 and 4 are spent below the 120 peak; day 4 makes a new one.
    assert max_drawdown_duration(curve) == 2
    assert max_drawdown([]) is None


def test_risk_free_rate_lowers_both_ratios():
    returns = [0.03, -0.02, 0.015, -0.01] * 5
    assert sharpe_ratio(returns, 0.0) > sharpe_ratio(returns, 0.10)
    assert sortino_ratio(returns, 0.0) > sortino_ratio(returns, 0.10)


def test_trade_statistics():
    pnls = [100.0, -50.0, 25.0, -25.0, 200.0]
    assert win_rate(pnls) == pytest.approx(0.6)
    assert profit_factor(pnls) == pytest.approx(325 / 75)
    # No losing trade at all means there is no loss to offset, not infinity.
    assert profit_factor([1.0, 2.0]) is None
    assert max_consecutive_losses(pnls) == 1
    assert max_consecutive_losses([-1.0, -1.0, 1.0, -1.0]) == 2


def test_turnover_is_notional_over_average_equity_per_year():
    # 252 days, 100% of a 1,000,000 average book traded = one turn a year.
    assert annualised_turnover(1_000_000, 1_000_000, 365) == pytest.approx(
        365.25 / 365, rel=1e-6
    )
    assert annualised_turnover(100.0, 0.0, 365) is None


def test_benchmark_comparison_reports_excess_return_beta_and_alpha():
    # The strategy returns exactly twice the benchmark every day, so its beta
    # against that benchmark is 2 by construction.
    benchmark_returns = [0.05, -0.05, 0.02, 0.01]
    benchmark = [100.0]
    strategy = [100.0]
    for r in benchmark_returns:
        benchmark.append(benchmark[-1] * (1 + r))
        strategy.append(strategy[-1] * (1 + 2 * r))
    out = benchmark_comparison(strategy, benchmark)
    assert out["benchmark_return"] == pytest.approx(benchmark[-1] / 100 - 1)
    assert out["excess_return"] == pytest.approx(strategy[-1] / 100 - 1 - out["benchmark_return"])
    assert out["beta"] == pytest.approx(2.0, rel=1e-9)
    # Exactly twice the benchmark's daily move means no daily intercept, so
    # alpha is zero even though the total return is far higher.
    assert out["alpha"] == pytest.approx(0.0, abs=1e-9)
    # A flat benchmark has no variance to scale against, and a mismatched curve
    # is refused instead of being silently compared.
    assert benchmark_comparison(strategy, [1.0])["beta"] is None
    assert benchmark_comparison([100.0, 101.0, 102.0], [1.0, 2.0])["beta"] is None


def _points(curve, start=date(2024, 1, 1), step=1):
    return [
        EquityPoint(
            as_of=start + timedelta(days=i * step),
            equity=value,
            cash=value,
            positions=0,
            exposure_pct=0.0,
            drawdown_pct=0.0,
        )
        for i, value in enumerate(curve)
    ]


def test_summarize_reports_the_whole_suite():
    # Quarterly points, so the run spans a real year and CAGR is meaningful.
    points = _points(
        [1_000_000.0, 1_050_000.0, 1_020_000.0, 1_100_000.0], step=91
    )
    trades = [
        TradeResult(net_pnl=60_000.0, return_pct=6.0, holding_days=10, exit_reason="TARGET"),
        TradeResult(net_pnl=-20_000.0, return_pct=-2.0, holding_days=5, exit_reason="STOP_LOSS"),
        TradeResult(net_pnl=20_000.0, return_pct=2.0, holding_days=20, exit_reason="END_OF_TEST"),
    ]
    metrics = summarize(points, trades, traded_notional=1_000_000.0)
    assert metrics.trades == 3
    assert metrics.wins == 2 and metrics.losses == 1
    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.profit_factor == pytest.approx(80_000 / 20_000)
    assert metrics.average_holding_days == pytest.approx((10 + 5 + 20) / 3)
    assert metrics.total_return == pytest.approx(0.10)
    assert metrics.max_drawdown == pytest.approx(1_020_000 / 1_050_000 - 1)
    assert metrics.max_drawdown_days == 1
    assert metrics.exit_reasons == {"TARGET": 1, "STOP_LOSS": 1, "END_OF_TEST": 1}
    assert metrics.best_trade == 60_000.0
    assert metrics.worst_trade == -20_000.0
    assert metrics.turnover is not None
    # 91 days per step over 3 steps = 273 days, +10% total.
    assert metrics.days == 273
    assert metrics.cagr == pytest.approx(1.1 ** (365.25 / 273) - 1)
    # Every value must survive a JSON round trip without NaN leaking out.
    as_dict = metrics.as_dict()
    assert as_dict["sharpe"] is None or math.isfinite(as_dict["sharpe"])
    assert as_dict["start"] == "2024-01-01"


def test_summarize_needs_an_equity_curve():
    with pytest.raises(ValueError, match="at least one point"):
        summarize([], [])


def test_a_single_point_run_is_still_reported():
    metrics = summarize(_points([1_000_000.0]), [])
    assert metrics.trades == 0
    assert metrics.total_return == pytest.approx(0.0)
    assert metrics.win_rate is None
    assert metrics.sharpe is None
    assert metrics.cagr is None
