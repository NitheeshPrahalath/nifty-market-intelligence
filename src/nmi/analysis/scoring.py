"""Scoring engine (Phase 3).

Produces eight explainable component scores (0-100) per instrument-day and a
weighted composite:

* ``trend``             — moving-average stack, ADX, MACD, trend state
* ``momentum``          — trailing returns, RSI positioning, ROC
* ``relative_strength`` — excess return vs benchmark and its trend
* ``quality``           — fundamental quality score from the Phase-2 engine
* ``growth``            — revenue/EPS growth and growth consistency
* ``valuation``         — valuation label plus inverted historical percentiles
* ``risk``              — volatility, drawdown, leverage, liquidity ratios
* ``liquidity``         — volume ratio (the available liquidity proxy)

Component weights come from the ``strategy_parameters`` table so they can be
audited and re-tuned without code changes. The instrument's preferred horizon
(from the horizon engine) multiplies those base weights, which is how the
short/medium/long view changes component emphasis.
"""

from __future__ import annotations

from collections.abc import Mapping

from nmi.analysis.common import (
    as_float,
    blend,
    clamp,
    mean_score,
    round_or_none,
    scale,
)
from nmi.analysis.horizon import RS_TREND_SCORES, TREND_STATE_SCORES, macd_score
from nmi.core.models import HorizonType, ScoreLabel

COMPONENTS = (
    "trend",
    "momentum",
    "relative_strength",
    "quality",
    "growth",
    "valuation",
    "risk",
    "liquidity",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.15,
    "momentum": 0.15,
    "relative_strength": 0.15,
    "quality": 0.15,
    "growth": 0.10,
    "valuation": 0.10,
    "risk": 0.10,
    "liquidity": 0.10,
}

HORIZON_EMPHASIS: dict[str, dict[str, float]] = {
    HorizonType.SHORT_TERM.value: {
        "trend": 1.0,
        "momentum": 1.3,
        "relative_strength": 1.3,
        "quality": 0.8,
        "growth": 0.8,
        "valuation": 0.7,
        "risk": 1.0,
        "liquidity": 1.2,
    },
    HorizonType.MEDIUM_TERM.value: {
        "trend": 1.2,
        "momentum": 1.0,
        "relative_strength": 1.0,
        "quality": 1.0,
        "growth": 1.0,
        "valuation": 1.0,
        "risk": 1.0,
        "liquidity": 1.0,
    },
    HorizonType.LONG_TERM.value: {
        "trend": 1.1,
        "momentum": 0.8,
        "relative_strength": 0.9,
        "quality": 1.3,
        "growth": 1.3,
        "valuation": 1.3,
        "risk": 1.0,
        "liquidity": 0.8,
    },
}

VALUATION_LABEL_SCORES = {
    "CHEAP": 85.0,
    "FAIRLY_VALUED": 65.0,
    "MODERATELY_EXPENSIVE": 45.0,
    "EXPENSIVE": 25.0,
}

_QUALITY_FALLBACKS = (
    "cash_conversion_quality",
    "margin_stability_quality",
    "profit_consistency_quality",
    "growth_consistency_quality",
    "debt_trend_quality",
    "interest_safety_score",
)

STRONG_SCORE = 75.0
GOOD_SCORE = 60.0
NEUTRAL_SCORE = 45.0
WEAK_SCORE = 30.0


def score_label(composite: float | None) -> ScoreLabel:
    if composite is None:
        return ScoreLabel.POOR
    if composite >= STRONG_SCORE:
        return ScoreLabel.STRONG
    if composite >= GOOD_SCORE:
        return ScoreLabel.GOOD
    if composite >= NEUTRAL_SCORE:
        return ScoreLabel.NEUTRAL
    if composite >= WEAK_SCORE:
        return ScoreLabel.WEAK
    return ScoreLabel.POOR


def effective_weights(
    weights: Mapping[str, float], preferred_horizon: str | None
) -> dict[str, float]:
    """Base weights scaled by the horizon emphasis multipliers."""
    base = {name: as_float(weights.get(name)) or 0.0 for name in COMPONENTS}
    emphasis = HORIZON_EMPHASIS.get(preferred_horizon or HorizonType.MEDIUM_TERM.value)
    if not emphasis:
        return base
    return {name: base[name] * emphasis.get(name, 1.0) for name in COMPONENTS}


def _inverted_percentile(value) -> float | None:
    pctile = as_float(value)
    if pctile is None:
        return None
    return 100.0 - pctile


def _quality(fundamentals: Mapping) -> float | None:
    overall = as_float(fundamentals.get("overall_quality_score"))
    if overall is not None:
        return clamp(overall)
    return mean_score(fundamentals.get(name) for name in _QUALITY_FALLBACKS)


def compute_component_scores(
    technical: Mapping,
    momentum: Mapping | None = None,
    relative_strength: Mapping | None = None,
    valuation: Mapping | None = None,
    fundamentals: Mapping | None = None,
) -> dict:
    """The eight component scores for one instrument-day."""
    momentum = momentum or {}
    relative_strength = relative_strength or {}
    valuation = valuation or {}
    fundamentals = fundamentals or {}
    close = technical.get("close")

    alignment = [
        65.0 if (as_float(close) or 0) > (as_float(technical.get(field)) or float("inf")) else 35.0
        for field in ("sma20", "sma50", "sma200")
        if as_float(close) is not None and as_float(technical.get(field)) is not None
    ]

    return {
        "trend": mean_score(
            alignment
            + [
                scale(technical.get("adx14"), 15.0, 40.0),
                macd_score(technical.get("macd_hist")),
                TREND_STATE_SCORES.get(technical.get("trend_state")),
            ]
        ),
        "momentum": mean_score(
            (
                scale(momentum.get("return_1m"), -10.0, 10.0),
                scale(momentum.get("return_3m"), -20.0, 20.0),
                scale(momentum.get("return_6m"), -35.0, 35.0),
                scale(technical.get("rsi14"), 35.0, 70.0),
                scale(technical.get("roc10"), -15.0, 15.0),
            )
        ),
        "relative_strength": mean_score(
            (
                scale(relative_strength.get("rs_1m"), -15.0, 15.0),
                scale(relative_strength.get("rs_3m"), -25.0, 25.0),
                scale(relative_strength.get("rs_6m"), -40.0, 40.0),
                RS_TREND_SCORES.get(relative_strength.get("rs_trend")),
            )
        ),
        "quality": _quality(fundamentals),
        "growth": mean_score(
            (
                scale(fundamentals.get("revenue_growth_pct"), 0.0, 30.0),
                scale(fundamentals.get("eps_growth_pct"), 0.0, 35.0),
                as_float(fundamentals.get("growth_consistency_quality")),
            )
        ),
        "valuation": mean_score(
            (
                VALUATION_LABEL_SCORES.get(valuation.get("valuation_label")),
                _inverted_percentile(valuation.get("pe_percentile_3y")),
                _inverted_percentile(valuation.get("pb_percentile_3y")),
                _inverted_percentile(valuation.get("ev_ebitda_percentile_3y")),
            )
        ),
        "risk": mean_score(
            (
                scale(technical.get("hist_vol_60"), 45.0, 12.0),
                scale(technical.get("drawdown_pct"), -35.0, 0.0),
                scale(fundamentals.get("debt_to_equity"), 2.0, 0.0),
                scale(fundamentals.get("current_ratio"), 0.8, 2.0),
            )
        ),
        "liquidity": mean_score((scale(technical.get("volume_ratio"), 0.5, 2.0),)),
    }


def compute_scoring(
    as_of,
    technical: Mapping,
    momentum: Mapping | None = None,
    relative_strength: Mapping | None = None,
    valuation: Mapping | None = None,
    fundamentals: Mapping | None = None,
    horizon: Mapping | None = None,
    sector_score: float | None = None,
    weights: Mapping[str, float] | None = None,
    parameter_set: str = "default",
) -> dict:
    """Component scores, weighted composite and label for one instrument-day."""
    components = compute_component_scores(
        technical, momentum, relative_strength, valuation, fundamentals
    )
    horizon = horizon or {}
    preferred_horizon = horizon.get("preferred_horizon") or HorizonType.MEDIUM_TERM.value
    composite = blend(components, effective_weights(weights or DEFAULT_WEIGHTS, preferred_horizon))

    horizon_score = None
    key = {
        HorizonType.SHORT_TERM.value: "short_term_score",
        HorizonType.MEDIUM_TERM.value: "medium_term_score",
        HorizonType.LONG_TERM.value: "long_term_score",
    }.get(preferred_horizon)
    if key:
        horizon_score = as_float(horizon.get(key))

    row = {
        "as_of": as_of,
        "parameter_set": parameter_set,
        "preferred_horizon": preferred_horizon,
        "horizon_score": round_or_none(horizon_score),
        "sector_score": round_or_none(sector_score),
        "composite_score": round_or_none(composite),
        "score_label": score_label(composite).value,
    }
    for name in COMPONENTS:
        row[f"{name}_score"] = round_or_none(components[name])
    return row
