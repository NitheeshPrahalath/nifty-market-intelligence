from __future__ import annotations

from nmi.indicators.technical import compute_technical_indicators
from tests.conftest import make_candles


def test_warmup_values_are_none_until_enough_history():
    closes = [100.0 + 0.2 * i for i in range(30)]
    rows = compute_technical_indicators(make_candles("X", closes))
    assert len(rows) == 30
    assert rows[0]["sma20"] is None
    assert rows[0]["sma50"] is None
    assert rows[19]["sma20"] is not None  # warm-up is 20
    assert rows[0]["macd"] is None
    assert rows[5]["rsi14"] is None
    assert rows[14]["rsi14"] is not None  # 14-period wake-up
    assert rows[0]["high_52w"] is not None  # partial window allowed


def test_flat_series_sma_equals_price_and_bollinger_percent_b():
    rows = compute_technical_indicators(make_candles("X", [100.0] * 40))
    last = rows[-1]
    assert last["sma20"] == 100.0
    assert last["sma50"] is None  # only 40 rows of history
    assert last["bollinger_width"] == 0.0
    assert last["bollinger_percent_b"] == 0.5
    assert last["trend_state"] == "CONSOLIDATION"
    assert last["drawdown_pct"] == 0.0


def test_monotonic_uptrend_reports_uptrend_and_rsi_high():
    closes = [100 + 0.5 * i for i in range(260)]
    rows = compute_technical_indicators(make_candles("X", closes))
    last = rows[-1]
    assert last["trend_state"] == "UPTREND"
    assert last["rsi14"] > 50
    assert last["macd"] > 0
    assert last["dist_from_low_52w_pct"] > 0
    assert last["breakout_52w"] is True  # keeps printing new highs


def test_volume_spike_flagged_when_ratio_above_multiple():
    volumes = [100_000] * 30 + [400_000]
    rows = compute_technical_indicators(
        make_candles("X", [100.0] * 31, volumes=volumes)
    )
    assert rows[-1]["volume_spike"] is True
    assert rows[-1]["volume_ratio"] >= 3.0
    assert rows[-2]["volume_spike"] is False


def test_adx_macd_rsi_all_populated_on_long_series():
    closes = [100 + 0.3 * i for i in range(300)]
    rows = compute_technical_indicators(make_candles("X", closes))
    last = rows[-1]
    for field in (
        "sma20",
        "sma50",
        "sma100",
        "sma200",
        "ema20",
        "ema50",
        "ema200",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_hist",
        "roc10",
        "stoch_k",
        "stoch_d",
        "williams_r",
        "cci20",
        "adx14",
        "plus_di14",
        "minus_di14",
        "atr14",
        "hist_vol_20",
        "hist_vol_60",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "bollinger_width",
        "bollinger_percent_b",
        "volume_sma20",
        "volume_ratio",
        "obv",
        "high_52w",
        "low_52w",
        "dist_from_high_52w_pct",
        "dist_from_low_52w_pct",
        "drawdown_pct",
        "recovery_pct",
    ):
        assert last[field] is not None, field


def test_drawdown_and_recovery_reflect_a_dip():
    closes = [100 + 2 * i for i in range(60)]
    closes[30:40] = [close - 20 for close in closes[30:40]]
    candles = make_candles(
        "X",
        closes,
        highs=[c * 1.01 for c in closes],
        lows=[c * 0.99 for c in closes],
        volumes=[150_000] * 60,
    )
    rows = compute_technical_indicators(candles)
    during = min(r["drawdown_pct"] for r in rows[35:40])
    assert during < 0
    assert rows[-1]["recovery_pct"] is not None
    assert rows[-1]["recovery_pct"] > 90
