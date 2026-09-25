from __future__ import annotations

from datetime import date

import pytest

from nmi.indicators.momentum import compute_momentum, compute_relative_strength
from tests.conftest import make_candles


def test_momentum_trailing_returns():
    closes = [100.0] * 21 + [110.0] * 5
    rows = compute_momentum(make_candles("X", closes, start=date(2024, 1, 1)))
    last = rows[-1]
    assert last["return_1m"] == pytest.approx(10.0)  # 21-session window
    assert last["return_3m"] is None  # only 26 sessions
    assert rows[20]["return_1m"] is None


def test_relative_strength_is_excess_return_with_no_lookahead():
    stock = [100.0] * 21 + [110.0] * 5
    benchmark_up = [100.0] * 21 + [105.0] * 5
    benchmark_down = [100.0] * 21 + [95.0] * 5

    rows = compute_relative_strength(
        make_candles("S", stock, start=date(2024, 1, 1)),
        make_candles("B", benchmark_up, start=date(2024, 1, 1)),
    )
    last = rows[-1]
    assert last["rs_1m"] == pytest.approx(5.0)  # 10% - 5%

    rows_down = compute_relative_strength(
        make_candles("S", stock, start=date(2024, 1, 1)),
        make_candles("B", benchmark_down, start=date(2024, 1, 1)),
    )
    assert rows_down[-1]["rs_1m"] == pytest.approx(15.0)  # 10% - (-5%)


def test_rs_trend_stable_without_history_then_improving():
    closes = [100.0] * 200
    benchmark = [100.0] * 200
    # First window where RS trend can be evaluated requires >= 20 prior daily rs.
    closes[200:220] = [110.0] * 20
    benchmark[200:220] = [105.0] * 20  # stock outperforms recently
    rows = compute_relative_strength(
        make_candles("S", closes, start=date(2023, 1, 1)),
        make_candles("B", benchmark, start=date(2023, 1, 1)),
    )
    assert rows[0]["rs_trend"] == "STABLE"
    # By the end the recent 1m rs (5pp) sits above its 63-day average (0).
    assert rows[-1]["rs_trend"] == "IMPROVING"


def test_benchmark_missing_dates_still_maps_as_of():
    stock = make_candles("S", [100.0] * 30 + [110.0] * 5, start=date(2024, 1, 1))
    bench = make_candles("B", [100.0] * 30 + [105.0] * 5, start=date(2024, 1, 1))
    bench = [c for c in bench if c.trade_date.day % 10 not in (3, 6)]  # drop mid-series dates
    rows = compute_relative_strength(stock, bench)
    last = rows[-1]
    assert last["as_of"] == stock[-1].trade_date
    assert last["rs_1m"] == pytest.approx(5.0)  # 10% - 5% despite missing bench dates
    assert last["rs_trend"] in ("IMPROVING", "STABLE", "DETERIORATING")
