"""Strategy evaluation engine (Phase 4).

Evaluates one versioned rule document against one instrument-day context and
returns a fully explainable decision: the signal type, the lifecycle state the
recommendation should take, the price levels implied by ATR14 and every
condition that passed, failed or was unknown.

The evaluator is a pure function of (rule document, context) — the Phase-6
backtester calls the same function with historical contexts, which is what
keeps backtest and live results comparable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nmi.analysis.common import as_float, mean_score, round_or_none
from nmi.core.models import RecommendationState, SignalType
from nmi.strategies.rules import (
    GROUP_NAMES,
    GroupResult,
    StrategyRules,
    price_levels,
)

HORIZON_LABELS = {
    "SHORT_TERM": "short-term",
    "MEDIUM_TERM": "medium-term",
    "LONG_TERM": "long-term",
}


@dataclass(slots=True)
class StrategyContext:
    """Flat key/value context assembled from the Phase-2/3 metric tables.

    Conditions address keys like ``composite_score``, ``rs_trend`` or
    ``preferred_horizon``; missing keys simply evaluate as ``unknown``.
    """

    instrument_id: int
    as_of: date
    values: dict[str, Any] = field(default_factory=dict)
    close: float | None = None
    atr14: float | None = None

    def get(self, key: str):
        return self.values.get(key)


@dataclass(frozen=True, slots=True)
class StrategyMeta:
    code: str
    name: str
    horizon: str
    version: int


@dataclass(slots=True)
class StrategyDecision:
    signal_type: SignalType
    state: RecommendationState
    horizon: str
    risk_level: str
    confidence: float | None
    composite_score: float | None
    current_price: float | None
    entry_low: float | None
    entry_high: float | None
    target_low: float | None
    target_high: float | None
    invalidation_price: float | None
    expected_holding_days_min: int
    expected_holding_days_max: int
    rules_result: list[dict]
    reasons: list[str]
    thesis: str
    trigger: str | None

    def as_signal_columns(self) -> dict:
        return {
            "signal_type": self.signal_type.value,
            "state": self.state.value,
            "horizon": self.horizon,
            "confidence": self.confidence,
            "composite_score": self.composite_score,
            "price": self.current_price,
            "risk_level": self.risk_level,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "target_low": self.target_low,
            "target_high": self.target_high,
            "invalidation_price": self.invalidation_price,
            "expected_holding_days_min": self.expected_holding_days_min,
            "expected_holding_days_max": self.expected_holding_days_max,
            "rules_result": self.rules_result,
            "reasons": self.reasons,
            "thesis": self.thesis,
        }


def _zone_text(low, high, label: str) -> str:
    if low is None or high is None:
        return ""
    return f"{label} {low:.2f}-{high:.2f}"


def _decide(
    results: Mapping[str, GroupResult], entry_low, entry_high, close
) -> tuple[SignalType, RecommendationState, str | None]:
    """Signal precedence: invalidation, then exit, entry, watch, hold."""
    invalidation = results.get("invalidation")
    if invalidation is not None and invalidation.passed:
        return SignalType.EXIT, RecommendationState.EXIT, "invalidation"
    exit_group = results.get("exit")
    if exit_group is not None and exit_group.passed:
        return SignalType.EXIT_WARNING, RecommendationState.EXIT_REVIEW, "exit"
    entry = results.get("entry")
    if entry is not None and entry.passed:
        price = as_float(close)
        in_zone = (
            price is not None
            and entry_low is not None
            and entry_high is not None
            and entry_low <= price <= entry_high
        )
        state = RecommendationState.ENTRY if in_zone else RecommendationState.POTENTIAL_ENTRY
        return SignalType.BUY_SETUP, state, "entry"
    watch = results.get("watch")
    if watch is not None and watch.passed:
        return SignalType.WATCH, RecommendationState.WATCH, "watch"
    hold = results.get("hold")
    if hold is not None and hold.passed:
        return SignalType.HOLD, RecommendationState.HOLD, "hold"
    return SignalType.REDUCE, RecommendationState.THESIS_WEAKENING, None


def _context_reasons(ctx: StrategyContext) -> list[str]:
    out: list[str] = []
    values = ctx.values
    composite = as_float(values.get("composite_score"))
    label = values.get("score_label")
    if composite is not None:
        out.append(f"Composite score {composite:.1f}" + (f" ({label})" if label else ""))
    regime_score = as_float(values.get("regime_score"))
    regime_label = values.get("regime_label")
    if regime_score is not None or regime_label:
        score = f" ({regime_score:.1f})" if regime_score is not None else ""
        out.append(f"Market regime {regime_label or 'n/a'}{score}")
    sector_score = as_float(values.get("sector_score"))
    sector_state = values.get("sector_state")
    if sector_score is not None or sector_state:
        score = f" ({sector_score:.1f})" if sector_score is not None else ""
        out.append(f"Sector {sector_state or 'n/a'}{score}")
    preferred = values.get("preferred_horizon")
    if preferred:
        out.append(f"Preferred horizon {preferred}")
    return out


def _thesis_text(
    meta: StrategyMeta,
    ctx: StrategyContext,
    signal_type: SignalType,
    trigger: str | None,
    context_reasons: list[str],
    levels: dict,
    holding: tuple[int, int],
    risk_level: str,
    anchor: str,
) -> str:
    horizon_label = HORIZON_LABELS.get(meta.horizon, meta.horizon.lower())
    trigger_text = (
        f"Triggered by the {trigger} rule group." if trigger else "No rule group matched."
    )
    parts = [
        f"{meta.name} ({horizon_label} horizon, strategy {meta.code} v{meta.version}) "
        f"evaluated on {ctx.as_of.isoformat()}: {signal_type.value}.",
        trigger_text,
    ]
    if context_reasons:
        parts.append("Context: " + "; ".join(context_reasons) + ".")
    zone = _zone_text(levels.get("entry_low"), levels.get("entry_high"), "Entry zone")
    if zone:
        stop = levels.get("invalidation_price")
        review = _zone_text(levels.get("target_low"), levels.get("target_high"), "Review")
        stop_text = f" Invalidation {stop:.2f}." if stop is not None else ""
        parts.append(f"{zone} anchored to {anchor}.{stop_text}")
        if review:
            parts.append(f"{review}.")
    parts.append(
        f"Risk level {risk_level}; expected holding {holding[0]}-{holding[1]} days."
    )
    return " ".join(parts)


def evaluate_strategy(
    meta: StrategyMeta, rules: StrategyRules, ctx: StrategyContext
) -> StrategyDecision:
    """Evaluate one rule document against one instrument-day context."""
    results: dict[str, GroupResult] = {}
    for name in GROUP_NAMES:
        group = rules.group(name)
        if group is not None:
            results[name] = group.evaluate(ctx.values)

    levels = price_levels(ctx.get(rules.risk.anchor), ctx.atr14, rules.risk)
    signal_type, state, trigger = _decide(
        results, levels["entry_low"], levels["entry_high"], ctx.close
    )
    low, high = levels["entry_low"], levels["entry_high"]
    stop = levels["invalidation_price"]
    target_low, target_high = levels["target_low"], levels["target_high"]
    risk_score = ctx.get("risk_score")
    risk_level = rules.risk.risk_level(risk_score)
    confidence = mean_score(
        (ctx.get("composite_score"), ctx.get("regime_score"), ctx.get("sector_score"))
    )
    holding = (rules.risk.holding_days_min, rules.risk.holding_days_max)
    context_reasons = _context_reasons(ctx)

    reasons: list[str] = []
    if trigger is not None:
        result = results[trigger]
        reasons.append(
            f"{trigger.capitalize()} conditions met "
            f"({result.passes}/{result.required}): " + "; ".join(result.reasons())
        )
    else:
        reasons.append(
            "No rule group matched; entry conditions not satisfied and no "
            "exit or invalidation condition triggered."
        )
    if risk_score is not None:
        reasons.append(f"Risk level {risk_level} (risk score {as_float(risk_score):.1f}).")
    reasons.extend(context_reasons)

    return StrategyDecision(
        signal_type=signal_type,
        state=state,
        horizon=meta.horizon,
        risk_level=risk_level,
        confidence=round_or_none(confidence),
        composite_score=round_or_none(ctx.get("composite_score")),
        current_price=round_or_none(ctx.close),
        entry_low=low,
        entry_high=high,
        target_low=target_low,
        target_high=target_high,
        invalidation_price=stop,
        expected_holding_days_min=holding[0],
        expected_holding_days_max=holding[1],
        rules_result=[results[name].as_dict() for name in GROUP_NAMES if name in results],
        reasons=reasons,
        thesis=_thesis_text(
            meta,
            ctx,
            signal_type,
            trigger,
            context_reasons,
            levels,
            holding,
            risk_level,
            rules.risk.anchor,
        ),
        trigger=trigger,
    )
