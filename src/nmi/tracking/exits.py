"""Exit engine (Phase 5).

Several *independent* mechanisms can end or flag a tracked recommendation.
Each one is evaluated on its own evidence and returns a reason that cites the
numbers which fired it, so an exit is never an unexplained "SELL".

The strategy's own declarative rules (Phase 4) participate as the ``STRATEGY``
mechanism; everything else here is computed from the metric tables, which is
what allows a recommendation to exit for a reason its own rule set never
mentioned.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from nmi.analysis.common import as_float, round_or_none
from nmi.core.models import ExitMechanism, RecommendationState

#: Corporate actions that can change the investment case itself.
DEFAULT_EVENT_EXIT_TYPES = ("MERGER", "DEMERGER", "SPLIT", "BONUS", "RIGHTS")


class ExitAction(StrEnum):
    """How strongly a mechanism fires."""

    EXIT = "EXIT"
    REVIEW = "REVIEW"


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    """Thresholds of the exit engine.

    Kept in one auditable object rather than scattered in code so a strategy
    version, a risk mandate and the backtester can share them.
    """

    risk_score_floor: float = 35.0
    max_drawdown_pct: float = -15.0
    growth_score_drop: float = 15.0
    quality_score_drop: float = 15.0
    eps_growth_drop_pct: float = 10.0
    valuation_review_percentile: float = 90.0
    valuation_exit_percentile: float = 99.0
    valuation_extreme_labels: tuple[str, ...] = ("EXPENSIVE",)
    event_exit_types: tuple[str, ...] = DEFAULT_EVENT_EXIT_TYPES
    time_exit_requires_no_development: bool = True
    weaken_score_factors: int = 2

    def as_dict(self) -> dict:
        return {
            "risk_score_floor": self.risk_score_floor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "growth_score_drop": self.growth_score_drop,
            "quality_score_drop": self.quality_score_drop,
            "eps_growth_drop_pct": self.eps_growth_drop_pct,
            "valuation_review_percentile": self.valuation_review_percentile,
            "valuation_exit_percentile": self.valuation_exit_percentile,
            "valuation_extreme_labels": list(self.valuation_extreme_labels),
            "event_exit_types": list(self.event_exit_types),
            "weaken_score_factors": self.weaken_score_factors,
        }


DEFAULT_EXIT_POLICY = ExitPolicy()


@dataclass(frozen=True, slots=True)
class ExitTrigger:
    """One mechanism that fired, with the evidence that fired it."""

    mechanism: ExitMechanism
    action: ExitAction
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_exit(self) -> bool:
        return self.action is ExitAction.EXIT

    def as_dict(self) -> dict:
        return {
            "mechanism": self.mechanism.value,
            "action": self.action.value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(slots=True)
class ExitContext:
    """Everything the exit engine may look at for one recommendation-day."""

    as_of: date
    price: float | None = None
    values: dict[str, Any] = field(default_factory=dict)
    signal_state: RecommendationState | None = None
    signal_reasons: list[str] = field(default_factory=list)
    signal_rules_result: list[dict] = field(default_factory=list)
    invalidation_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    entry_price: float | None = None
    sessions_since_active: int | None = None
    expected_holding_days_max: int | None = None
    baseline_composite: float | None = None
    current_composite: float | None = None
    baseline_factors: Mapping[str, Any] = field(default_factory=dict)
    corporate_actions: Sequence[Mapping] = ()

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def number(self, key: str) -> float | None:
        return as_float(self.values.get(key))

    def baseline_number(self, key: str) -> float | None:
        return as_float(self.baseline_factors.get(key))


def _drop(old: float | None, new: float | None, threshold: float) -> float | None:
    """Score drop between the original thesis and today, if material."""
    if old is None or new is None:
        return None
    drop = old - new
    return drop if drop >= threshold else None


def _risk_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    out: list[ExitTrigger] = []
    stop = as_float(ctx.invalidation_price)
    price = as_float(ctx.price)
    if stop is not None and price is not None and price <= stop:
        loss = (price / stop - 1) * 100 if stop else None
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.RISK,
                action=ExitAction.EXIT,
                reason=(
                    f"Price {price:.2f} breached the recommendation's invalidation level "
                    f"{stop:.2f}"
                    + (f" ({loss:.1f}% below the stop)." if loss is not None else ".")
                ),
                detail={"price": round_or_none(price), "invalidation_price": round_or_none(stop)},
            )
        )
    risk_score = ctx.number("risk_score")
    if risk_score is not None and risk_score < policy.risk_score_floor:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.RISK,
                action=ExitAction.EXIT,
                reason=(
                    f"Risk score fell to {risk_score:.1f}, below the exit floor of "
                    f"{policy.risk_score_floor:.1f}."
                ),
                detail={"risk_score": risk_score, "floor": policy.risk_score_floor},
            )
        )
    drawdown = ctx.number("drawdown_pct")
    if drawdown is not None and drawdown <= policy.max_drawdown_pct:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.RISK,
                action=ExitAction.EXIT,
                reason=(
                    f"Drawdown from the 52-week high reached {drawdown:.1f}%, at or beyond the "
                    f"{policy.max_drawdown_pct:.1f}% risk limit."
                ),
                detail={"drawdown_pct": drawdown, "limit": policy.max_drawdown_pct},
            )
        )
    return out


def _technical_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    out: list[ExitTrigger] = []
    sma50 = ctx.number("sma50")
    sma200 = ctx.number("sma200")
    trend = ctx.get("trend_state")
    # A death cross is structural: the medium-term average has fallen through
    # the long-term one, so the trend basis of the thesis is gone.
    if sma50 is not None and sma200 is not None and sma50 < sma200:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.TECHNICAL,
                action=ExitAction.EXIT,
                reason=(
                    f"Death cross: the 50 DMA ({sma50:.2f}) has fallen below the 200 DMA "
                    f"({sma200:.2f}), so the technical basis of the thesis has broken."
                ),
                detail={"sma50": round_or_none(sma50), "sma200": round_or_none(sma200)},
            )
        )
    if trend != "DOWNTREND":
        return out
    close = as_float(ctx.price)
    detail: dict[str, Any] = {"trend_state": trend, "sma200": round_or_none(sma200)}
    basis = "Trend state is DOWNTREND"
    if sma200 and close is not None:
        detail["close"] = round_or_none(close)
        detail["price_vs_sma200"] = "BELOW_SMA200" if close < sma200 else "ABOVE_SMA200"
        side = "below" if close < sma200 else "above"
        basis += f" with price {close:.2f} {side} the 200 DMA {sma200:.2f}"
    out.append(
        ExitTrigger(
            mechanism=ExitMechanism.TECHNICAL,
            action=ExitAction.EXIT,
            reason=f"{basis}: the technical trend that justified the thesis no longer holds.",
            detail=detail,
        )
    )
    return out


def _fundamental_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    out: list[ExitTrigger] = []
    score_rules = (
        ("growth_score", policy.growth_score_drop, "Growth score"),
        ("quality_score", policy.quality_score_drop, "Quality score"),
    )
    for key, threshold, label in score_rules:
        before = ctx.baseline_number(key)
        after = ctx.number(key)
        drop = _drop(before, after, threshold)
        if drop is None:
            continue
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.FUNDAMENTAL,
                action=ExitAction.EXIT,
                reason=(
                    f"{label} fell {drop:.1f} points since the recommendation "
                    f"({before:.1f} -> {after:.1f}): a material fundamental deterioration."
                ),
                detail={
                    "factor": key,
                    "before": round_or_none(before),
                    "after": round_or_none(after),
                },
            )
        )

    eps_before = ctx.baseline_number("eps_growth_pct")
    eps_after = ctx.number("eps_growth_pct")
    eps_drop = _drop(eps_before, eps_after, policy.eps_growth_drop_pct)
    if eps_drop is not None:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.FUNDAMENTAL,
                action=ExitAction.EXIT,
                reason=(
                    f"EPS growth fell {eps_drop:.1f} points to {eps_after:.1f}% since the "
                    "recommendation was made."
                ),
                detail={
                    "factor": "eps_growth_pct",
                    "before": round_or_none(eps_before),
                    "after": round_or_none(eps_after),
                },
            )
        )
    return out


def _valuation_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    out: list[ExitTrigger] = []
    percentile = ctx.number("pe_percentile_3y")
    label = ctx.get("valuation_label")
    if percentile is not None and percentile >= policy.valuation_exit_percentile:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.VALUATION,
                action=ExitAction.EXIT,
                reason=(
                    f"Valuation reached the {percentile:.1f}th percentile of its own 3-year P/E "
                    f"range, at or above the {policy.valuation_exit_percentile:.0f}th percentile "
                    "exit level."
                ),
                detail={"pe_percentile_3y": percentile, "label": label},
            )
        )
    elif percentile is not None and percentile >= policy.valuation_review_percentile:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.VALUATION,
                action=ExitAction.REVIEW,
                reason=(
                    f"Valuation reached the {percentile:.1f}th percentile of its own 3-year P/E "
                    f"range, above the {policy.valuation_review_percentile:.0f}th percentile "
                    "review level."
                ),
                detail={"pe_percentile_3y": percentile, "label": label},
            )
        )
    elif label in policy.valuation_extreme_labels and percentile is None:
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.VALUATION,
                action=ExitAction.REVIEW,
                reason=f"Valuation is classified {label} against the strategy's own history.",
                detail={"valuation_label": label},
            )
        )
    return out


def _target_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    price = as_float(ctx.price)
    target_low = as_float(ctx.target_low)
    target_high = as_float(ctx.target_high)
    if price is None or target_low is None or price < target_low:
        return []
    detail = {"price": round_or_none(price), "target_low": round_or_none(target_low)}
    if target_high is not None and price >= target_high:
        detail["target_high"] = round_or_none(target_high)
        reason = (
            f"Price {price:.2f} reached the target zone up to {target_high:.2f}; review whether "
            "the thesis has played out."
        )
    else:
        reason = (
            f"Price {price:.2f} entered the 1R review zone at {target_low:.2f}; review risk/reward."
        )
    return [
        ExitTrigger(
            mechanism=ExitMechanism.TARGET,
            action=ExitAction.REVIEW,
            reason=reason,
            detail=detail,
        )
    ]


def _time_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    held = ctx.sessions_since_active
    limit = ctx.expected_holding_days_max
    if held is None or limit is None or held <= limit:
        return []
    baseline, current = ctx.baseline_composite, ctx.current_composite
    developed = (
        baseline is not None and current is not None and current > baseline
    )
    if policy.time_exit_requires_no_development and developed:
        return []
    detail: dict[str, Any] = {"sessions_since_entry": held, "max_sessions": limit}
    if baseline is not None and current is not None:
        detail["composite_score"] = round_or_none(current)
        detail["baseline_composite_score"] = round_or_none(baseline)
    return [
        ExitTrigger(
            mechanism=ExitMechanism.TIME,
            action=ExitAction.EXIT,
            reason=(
                f"The expected holding period of {limit} sessions expired after {held} sessions "
                "without the composite score improving on the recommendation day."
            ),
            detail=detail,
        )
    ]


def _event_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    out: list[ExitTrigger] = []
    for action in ctx.corporate_actions:
        kind = str(action.get("action_type") or "").upper()
        if kind not in policy.event_exit_types:
            continue
        ex_date = action.get("ex_date")
        description = action.get("description") or kind.title()
        out.append(
            ExitTrigger(
                mechanism=ExitMechanism.EVENT,
                action=ExitAction.EXIT,
                reason=(
                    f"Corporate action {kind} on "
                    f"{ex_date.isoformat() if hasattr(ex_date, 'isoformat') else ex_date} "
                    f"materially changes the investment case: {description}"
                ),
                detail={
                    "action_type": kind,
                    "ex_date": ex_date.isoformat() if hasattr(ex_date, "isoformat") else ex_date,
                    "description": description,
                },
            )
        )
    return out


def _strategy_triggers(ctx: ExitContext, policy: ExitPolicy) -> list[ExitTrigger]:
    """Exits raised by the strategy's own rule groups (Phase 4)."""
    if ctx.signal_state not in (RecommendationState.EXIT, RecommendationState.EXIT_REVIEW):
        return []
    action = (
        ExitAction.EXIT
        if ctx.signal_state is RecommendationState.EXIT
        else ExitAction.REVIEW
    )
    passing = [
        group.get("name")
        for group in ctx.signal_rules_result
        if isinstance(group, Mapping) and group.get("passed")
    ]
    mechanism = ExitMechanism.RISK if "invalidation" in passing else ExitMechanism.STRATEGY
    reason = (
        ctx.signal_reasons[0]
        if ctx.signal_reasons
        else f"Strategy rules returned {ctx.signal_state.value}"
    )
    return [
        ExitTrigger(
            mechanism=mechanism,
            action=action,
            reason=f"Strategy rule groups {passing or ['n/a']} fired: {reason}",
            detail={"groups": passing, "signal_state": ctx.signal_state.value},
        )
    ]


def evaluate_exits(
    ctx: ExitContext, policy: ExitPolicy = DEFAULT_EXIT_POLICY
) -> list[ExitTrigger]:
    """Evaluate every independent mechanism and return the ones that fired.

    Exit-level triggers come first (most severe first), reviews after; within
    each group the order is deterministic so notifications never flap.
    """
    triggers = [
        *_strategy_triggers(ctx, policy),
        *_risk_triggers(ctx, policy),
        *_technical_triggers(ctx, policy),
        *_fundamental_triggers(ctx, policy),
        *_valuation_triggers(ctx, policy),
        *_target_triggers(ctx, policy),
        *_time_triggers(ctx, policy),
        *_event_triggers(ctx, policy),
    ]
    severity = {ExitAction.EXIT: 0, ExitAction.REVIEW: 1}
    return sorted(
        triggers, key=lambda t: (severity[t.action], t.mechanism.value, t.reason)
    )
