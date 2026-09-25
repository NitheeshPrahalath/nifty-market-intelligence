from __future__ import annotations

from datetime import date

from nmi.analysis.horizon import compute_horizon_metrics

AS_OF = date(2024, 6, 28)


def _technical(**overrides):
    base = {
        "as_of": AS_OF,
        "close": 120.0,
        "sma20": 115.0,
        "sma50": 110.0,
        "sma200": 100.0,
        "rsi14": 58.0,
        "macd_hist": 1.5,
        "adx14": 28.0,
        "roc10": 4.0,
        "dist_from_high_52w_pct": -5.0,
        "dist_from_low_52w_pct": 35.0,
        "volume_ratio": 1.2,
        "trend_state": "UPTREND",
    }
    base.update(overrides)
    return base


def test_no_technical_row_returns_none():
    assert compute_horizon_metrics(None) is None
    assert compute_horizon_metrics({}) is None


def test_technical_without_any_usable_metrics_falls_back_to_medium_term():
    row = compute_horizon_metrics({"as_of": AS_OF})

    assert row["as_of"] == AS_OF
    assert row["short_term_score"] is None
    assert row["medium_term_score"] is None
    assert row["long_term_score"] is None
    assert row["preferred_horizon"] == "MEDIUM_TERM"
    assert row["horizon_confidence"] is None


def test_uptrend_prefers_long_or_medium_horizon():
    row = compute_horizon_metrics(
        _technical(),
        {"return_1m": 2.0, "return_3m": 8.0, "return_6m": 20.0, "return_12m": 45.0},
        {"rs_trend": "IMPROVING"},
    )

    assert row["short_term_score"] is not None
    assert row["medium_term_score"] is not None
    assert row["long_term_score"] is not None
    assert row["preferred_horizon"] in {"MEDIUM_TERM", "LONG_TERM"}
    assert 0 <= row["horizon_confidence"] <= 100


def test_reversal_setup_prefers_short_term():
    technical = _technical(
        close=95.0,
        sma20=100.0,
        sma50=105.0,
        sma200=110.0,
        rsi14=34.0,
        macd_hist=-1.0,
        adx14=12.0,
        dist_from_high_52w_pct=-28.0,
        dist_from_low_52w_pct=5.0,
        volume_ratio=1.8,
        trend_state="DOWNTREND",
    )

    row = compute_horizon_metrics(
        technical,
        {"return_1m": 9.0, "return_3m": -6.0, "return_6m": -15.0, "return_12m": -25.0},
        {"rs_trend": "IMPROVING"},
    )

    assert row["short_term_score"] > row["medium_term_score"]
    assert row["short_term_score"] > row["long_term_score"]
    assert row["preferred_horizon"] == "SHORT_TERM"
    assert row["horizon_confidence"] > 0


def test_confidence_is_zero_when_the_top_two_horizons_tie():
    row = compute_horizon_metrics({"as_of": AS_OF, "close": 120.0, "sma50": 100.0, "sma200": 100.0})

    assert row["short_term_score"] is None
    assert row["medium_term_score"] == row["long_term_score"]
    assert row["preferred_horizon"] == "MEDIUM_TERM"
    assert row["horizon_confidence"] == 0.0
