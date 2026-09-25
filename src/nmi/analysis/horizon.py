"""Horizon engine (Phase 3).

Scores how well an instrument currently fits a short-term, medium-term or
long-term holding period. The three scores are always on the same 0-100 scale
so the dominant one can be named explicitly; downstream scoring uses that to
re-weight components rather than to re-define them.
"""

from __future__ import annotations

from collections.abc import Mapping

from nmi.analysis.common import as_float, clamp, mean_score, round_or_none, scale
from nmi.core.models import HorizonType

TREND_STATE_SCORES = {"UPTREND": 85.0, "CONSOLIDATION": 50.0, "DOWNTREND": 15.0}
RS_TREND_SCORES = {"IMPROVING": 80.0, "STABLE": 50.0, "DETERIORATING": 20.0}


def macd_score(hist: float | None) -> float | None:
    value = as_float(hist)
    if value is None:
        return None
    return 65.0 if value > 0 else 35.0


def _alignment(close: float | None, sma: float | None) -> float | None:
    c = as_float(close)
    s = as_float(sma)
    if c is None or s is None:
        return None
    return 65.0 if c > s else 35.0


def compute_horizon_metrics(
    technical: Mapping | None,
    momentum: Mapping | None = None,
    relative_strength: Mapping | None = None,
) -> dict | None:
    """One horizon row for a single instrument-day, or ``None`` without data."""
    if not technical or technical.get("as_of") is None:
        return None
    momentum = momentum or {}
    relative_strength = relative_strength or {}

    close = technical.get("close")
    short_term = mean_score(
        (
            macd_score(technical.get("macd_hist")),
            scale(technical.get("rsi14"), 30.0, 65.0),
            scale(momentum.get("return_1m"), -10.0, 10.0),
            scale(technical.get("dist_from_high_52w_pct"), -25.0, 0.0),
            scale(technical.get("volume_ratio"), 0.7, 1.5),
        )
    )
    medium_term = mean_score(
        (
            _alignment(close, technical.get("sma50")),
            scale(technical.get("adx14"), 15.0, 40.0),
            scale(momentum.get("return_3m"), -20.0, 20.0),
            scale(momentum.get("return_6m"), -30.0, 30.0),
            RS_TREND_SCORES.get(relative_strength.get("rs_trend")),
        )
    )
    long_term = mean_score(
        (
            _alignment(close, technical.get("sma200")),
            scale(momentum.get("return_12m"), -30.0, 40.0),
            scale(technical.get("dist_from_low_52w_pct"), 0.0, 60.0),
            TREND_STATE_SCORES.get(technical.get("trend_state")),
        )
    )

    candidates = {
        HorizonType.SHORT_TERM: short_term,
        HorizonType.MEDIUM_TERM: medium_term,
        HorizonType.LONG_TERM: long_term,
    }
    scored = {h: v for h, v in candidates.items() if v is not None}
    if not scored:
        return {
            "as_of": technical["as_of"],
            "short_term_score": None,
            "medium_term_score": None,
            "long_term_score": None,
            "preferred_horizon": HorizonType.MEDIUM_TERM.value,
            "horizon_confidence": None,
        }

    best_horizon, best_score = max(scored.items(), key=lambda kv: kv[1])
    others = [v for h, v in scored.items() if h is not best_horizon]
    runner_up = max(others) if others else None
    confidence = None
    if runner_up is not None:
        confidence = clamp((best_score - runner_up) / best_score * 100) if best_score else None

    return {
        "as_of": technical["as_of"],
        "short_term_score": round_or_none(short_term),
        "medium_term_score": round_or_none(medium_term),
        "long_term_score": round_or_none(long_term),
        "preferred_horizon": best_horizon.value,
        "horizon_confidence": round_or_none(confidence),
    }
