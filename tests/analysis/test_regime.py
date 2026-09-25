from __future__ import annotations

from datetime import date, timedelta

import pytest

from nmi.analysis.regime import compute_market_regime, index_return, regime_label
from nmi.core.models import MarketRegimeLabel


def _series(closes: list[float], start: date = date(2024, 1, 1)):
    return [(start + timedelta(days=i), float(c)) for i, c in enumerate(closes)]


def test_regime_labels_map_score_bands():
    assert regime_label(80) == MarketRegimeLabel.RISK_ON
    assert regime_label(65) == MarketRegimeLabel.RISK_ON
    assert regime_label(55) == MarketRegimeLabel.CAUTIOUS
    assert regime_label(40) == MarketRegimeLabel.RISK_OFF
    assert regime_label(10) == MarketRegimeLabel.STRESSED
    assert regime_label(None) == MarketRegimeLabel.STRESSED


def test_rising_market_with_healthy_breadth_is_risk_on():
    closes = [100.0 + i for i in range(130)]
    as_of = _series(closes)[-1][0]
    members = [{"close": 120.0, "sma20": 110.0, "sma50": 100.0, "sma200": 90.0}]

    rows = compute_market_regime(
        _series(closes), {as_of: members}, {as_of: 100.0}
    )

    last = rows[-1]
    assert last["as_of"] == as_of
    assert last["member_count"] == 1
    assert last["members_above_sma20"] == 1
    assert last["breadth_above_sma50_pct"] == pytest.approx(100.0)
    assert last["index_return_1m"] is not None
    assert last["index_hist_vol_60"] is not None
    assert last["index_drawdown_pct"] == pytest.approx(0.0)
    assert last["sector_participation_pct"] == pytest.approx(100.0)
    assert last["regime_score"] > 65
    assert last["regime_label"] == "RISK_ON"


def test_falling_market_with_weak_breadth_is_risk_off_or_worse():
    closes = [300.0 - i for i in range(130)]
    as_of = _series(closes)[-1][0]
    members = [{"close": 80.0, "sma20": 100.0, "sma50": 110.0, "sma200": 120.0}]

    rows = compute_market_regime(
        _series(closes), {as_of: members}, {as_of: 0.0}
    )

    last = rows[-1]
    assert last["breadth_above_sma20_pct"] == pytest.approx(0.0)
    assert last["index_drawdown_pct"] < 0
    assert last["regime_label"] in {"RISK_OFF", "STRESSED"}


def test_partial_breadth_excludes_members_without_an_sma():
    series = _series([100.0] * 10)
    as_of = series[-1][0]
    members = [
        {"close": 110.0, "sma20": 100.0, "sma50": 100.0, "sma200": 100.0},
        {"close": 90.0, "sma20": 100.0, "sma200": 100.0},  # no sma50
        {"close": 50.0},  # no averages at all
    ]

    rows = compute_market_regime(series, {as_of: members})

    last = rows[-1]
    assert last["member_count"] == 3
    assert last["breadth_above_sma20_pct"] == pytest.approx(50.0)
    assert last["breadth_above_sma50_pct"] == pytest.approx(100.0)
    assert last["breadth_above_sma200_pct"] == pytest.approx(50.0)


def test_no_member_data_still_scores_available_components():
    rows = compute_market_regime(_series([100.0 + i for i in range(10)]))

    last = rows[-1]
    assert last["member_count"] == 0
    assert last["breadth_above_sma20_pct"] is None
    assert last["sector_participation_pct"] is None
    assert last["regime_score"] is not None
    assert last["index_return_1m"] is None  # only 10 sessions available


def test_index_return_uses_latest_close_at_or_before_as_of():
    closes = _series([100.0, 101.0, 110.0], start=date(2024, 1, 1))
    as_of = date(2024, 1, 3)
    later = date(2024, 1, 4)

    assert index_return(closes, as_of, 2) == pytest.approx(10.0)
    assert index_return(closes, later, 2) == pytest.approx(10.0)
    assert index_return(closes, as_of, 5) is None
    assert index_return(closes, date(2023, 12, 31), 1) is None
