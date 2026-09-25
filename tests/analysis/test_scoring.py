from __future__ import annotations

from datetime import date

import pytest

from nmi.analysis.scoring import (
    COMPONENTS,
    DEFAULT_WEIGHTS,
    compute_component_scores,
    compute_scoring,
    effective_weights,
    score_label,
)
from nmi.core.models import ScoreLabel

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
        "hist_vol_60": 18.0,
        "drawdown_pct": -4.0,
        "volume_ratio": 1.3,
        "trend_state": "UPTREND",
    }
    base.update(overrides)
    return base


def _momentum(**overrides):
    base = {"return_1m": 2.0, "return_3m": 8.0, "return_6m": 18.0, "return_12m": 30.0}
    base.update(overrides)
    return base


def test_score_labels_map_bands():
    assert score_label(90) == ScoreLabel.STRONG
    assert score_label(70) == ScoreLabel.GOOD
    assert score_label(50) == ScoreLabel.NEUTRAL
    assert score_label(35) == ScoreLabel.WEAK
    assert score_label(5) == ScoreLabel.POOR
    assert score_label(None) == ScoreLabel.POOR


def test_component_scores_stay_in_range_and_default_weights_sum_to_one():
    scores = compute_component_scores(
        _technical(),
        _momentum(),
        {"rs_1m": 3.0, "rs_3m": 5.0, "rs_6m": 9.0, "rs_trend": "IMPROVING"},
        {"valuation_label": "CHEAP", "pe_percentile_3y": 10.0},
        {"overall_quality_score": 80.0, "revenue_growth_pct": 15.0, "eps_growth_pct": 18.0},
    )

    assert set(scores) == set(COMPONENTS)
    for value in scores.values():
        assert value is not None
        assert 0.0 <= value <= 100.0
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_strong_everything_beats_weak_everything():
    strong = compute_scoring(
        AS_OF,
        _technical(),
        _momentum(),
        {"rs_1m": 5.0, "rs_3m": 8.0, "rs_6m": 15.0, "rs_trend": "IMPROVING"},
        {
            "valuation_label": "CHEAP",
            "pe_percentile_3y": 5.0,
            "pb_percentile_3y": 8.0,
            "ev_ebitda_percentile_3y": 12.0,
        },
        {
            "overall_quality_score": 90.0,
            "revenue_growth_pct": 22.0,
            "eps_growth_pct": 25.0,
            "growth_consistency_quality": 85.0,
            "debt_to_equity": 0.2,
            "current_ratio": 2.5,
        },
    )
    weak = compute_scoring(
        AS_OF,
        _technical(
            close=80.0,
            sma20=100.0,
            sma50=105.0,
            sma200=110.0,
            rsi14=30.0,
            macd_hist=-2.0,
            adx14=40.0,
            roc10=-12.0,
            hist_vol_60=55.0,
            drawdown_pct=-30.0,
            volume_ratio=0.5,
            trend_state="DOWNTREND",
        ),
        _momentum(return_1m=-12.0, return_3m=-22.0, return_6m=-30.0, return_12m=-40.0),
        {"rs_1m": -10.0, "rs_3m": -18.0, "rs_6m": -30.0, "rs_trend": "DETERIORATING"},
        {"valuation_label": "EXPENSIVE", "pe_percentile_3y": 98.0},
        {"overall_quality_score": 20.0, "debt_to_equity": 3.0, "current_ratio": 0.6},
    )

    assert strong["composite_score"] > 70
    assert strong["score_label"] in {"GOOD", "STRONG"}
    assert weak["composite_score"] < 30
    assert weak["score_label"] in {"POOR", "WEAK"}
    assert weak["valuation_score"] < strong["valuation_score"]
    assert weak["risk_score"] < strong["risk_score"]


def test_missing_inputs_leave_components_none_and_do_not_crash():
    row = compute_scoring(AS_OF, _technical())

    assert row["as_of"] == AS_OF
    assert row["parameter_set"] == "default"
    assert row["preferred_horizon"] == "MEDIUM_TERM"
    assert row["quality_score"] is None
    assert row["growth_score"] is None
    assert row["valuation_score"] is None
    assert row["trend_score"] is not None
    assert row["composite_score"] is not None
    assert row["score_label"] in {"POOR", "WEAK", "NEUTRAL", "GOOD", "STRONG"}


def test_horizon_changes_weight_emphasis():
    short = effective_weights(DEFAULT_WEIGHTS, "SHORT_TERM")
    long = effective_weights(DEFAULT_WEIGHTS, "LONG_TERM")

    assert short["momentum"] > DEFAULT_WEIGHTS["momentum"]
    assert short["momentum"] > long["momentum"]
    assert long["quality"] > DEFAULT_WEIGHTS["quality"]
    assert long["quality"] > short["quality"]
    assert long["valuation"] > short["valuation"]


def test_horizon_context_is_recorded_and_drives_the_composite():
    horizon = {
        "preferred_horizon": "SHORT_TERM",
        "short_term_score": 80.0,
        "medium_term_score": 50.0,
        "long_term_score": 20.0,
    }

    row = compute_scoring(
        AS_OF,
        _technical(),
        _momentum(),
        {"rs_1m": 3.0, "rs_3m": 5.0, "rs_6m": 9.0, "rs_trend": "IMPROVING"},
        {"valuation_label": "FAIRLY_VALUED", "pe_percentile_3y": 50.0},
        {"overall_quality_score": 70.0},
        horizon,
        sector_score=64.0,
    )

    assert row["preferred_horizon"] == "SHORT_TERM"
    assert row["horizon_score"] == 80.0
    assert row["sector_score"] == 64.0
    assert row["composite_score"] is not None
    assert row["score_label"] in {"POOR", "WEAK", "NEUTRAL", "GOOD", "STRONG"}


def test_custom_weights_from_strategy_parameters_are_applied():
    momentum_heavy = {name: 0.0 for name in COMPONENTS}
    momentum_heavy["momentum"] = 1.0
    quality_heavy = {name: 0.0 for name in COMPONENTS}
    quality_heavy["quality"] = 1.0

    kwargs = dict(
        technical=_technical(),
        momentum=_momentum(),
        relative_strength={"rs_1m": 0.0, "rs_3m": 0.0, "rs_6m": 0.0, "rs_trend": "STABLE"},
        valuation={"valuation_label": "EXPENSIVE"},
        fundamentals={"overall_quality_score": 95.0},
    )

    by_momentum = compute_scoring(
        AS_OF, weights=momentum_heavy, parameter_set="momentum_test", **kwargs
    )
    by_quality = compute_scoring(
        AS_OF, weights=quality_heavy, parameter_set="quality_test", **kwargs
    )

    assert by_momentum["composite_score"] == by_momentum["momentum_score"]
    assert by_quality["composite_score"] == by_quality["quality_score"]
    assert by_quality["composite_score"] > by_momentum["composite_score"]
    assert by_momentum["parameter_set"] == "momentum_test"
    assert by_quality["parameter_set"] == "quality_test"
