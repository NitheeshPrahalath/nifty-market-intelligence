"""Thesis snapshotting and change detection (Phase 5).

A recommendation is only useful if the system keeps watching the *factors that
justified it*. This module turns a strategy context into a structured
:class:`ThesisSnapshot` and compares a fresh snapshot with the original one,
producing the "what changed and in which direction" list that powers thesis
weakening, thesis improvement and every alert derived from them.

Pure functions only: the same comparison runs in live tracking and in the
Phase-6 backtester.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nmi.analysis.common import as_float, round_or_none
from nmi.core.models import ChangeDirection

#: A score must move by at least this many points to count as a real change.
NUMERIC_TOLERANCE = 2.0


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """One tracked thesis factor and how its direction is judged."""

    key: str
    label: str
    kind: str = "number"  # "number" | "label"
    order: tuple[str, ...] = ()  # worst -> best, for label factors
    tolerance: float = NUMERIC_TOLERANCE
    group: str = "score"

    def rank(self, value: Any) -> int | None:
        """Position of ``value`` in the factor's ordering (higher is better)."""
        if not self.order or value is None:
            return None
        text = str(value)
        return self.order.index(text) if text in self.order else None

    def text(self, value: Any) -> str:
        if value is None:
            return "n/a"
        number = as_float(value)
        if number is not None and self.kind == "number":
            return f"{number:.1f}"
        return str(value)


#: Factors compared against the original thesis, worst-to-best for labels.
THESIS_FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("composite_score", "Composite score", group="score"),
    FactorSpec("growth_score", "Growth score", group="fundamental"),
    FactorSpec("quality_score", "Quality score", group="fundamental"),
    FactorSpec("eps_growth_pct", "EPS growth", group="fundamental"),
    FactorSpec("relative_strength_score", "Relative strength score", group="technical"),
    FactorSpec("risk_score", "Risk score", group="score"),
    FactorSpec("valuation_score", "Valuation score", group="valuation"),
    FactorSpec(
        "rs_trend",
        "Relative strength",
        kind="label",
        order=("DETERIORATING", "STABLE", "IMPROVING"),
        group="technical",
    ),
    FactorSpec(
        "trend_state",
        "Trend vs moving averages",
        kind="label",
        order=("DOWNTREND", "CONSOLIDATION", "UPTREND"),
        group="technical",
    ),
    FactorSpec(
        "price_vs_sma200",
        "Price vs 200 DMA",
        kind="label",
        order=("BELOW_SMA200", "AT_SMA200", "ABOVE_SMA200"),
        group="technical",
    ),
    FactorSpec(
        "valuation_label",
        "Valuation",
        kind="label",
        order=("EXPENSIVE", "MODERATELY_EXPENSIVE", "FAIRLY_VALUED", "CHEAP"),
        group="valuation",
    ),
    FactorSpec(
        "sector_state",
        "Sector",
        kind="label",
        order=("DEFENSIVE", "LAGGING", "NEUTRAL", "LEADING"),
        group="technical",
    ),
    FactorSpec(
        "regime_label",
        "Market regime",
        kind="label",
        order=("STRESSED", "RISK_OFF", "CAUTIOUS", "RISK_ON"),
        group="context",
    ),
)

FACTORS_BY_KEY = {spec.key: spec for spec in THESIS_FACTORS}


@dataclass(frozen=True, slots=True)
class ThesisChange:
    """One factor that moved since the original thesis."""

    key: str
    label: str
    group: str
    before: Any
    after: Any
    direction: ChangeDirection
    before_text: str
    after_text: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "before": self.before,
            "after": self.after,
            "before_text": self.before_text,
            "after_text": self.after_text,
            "direction": self.direction.value,
            "text": (
                f"{self.label}: {self.before_text} -> {self.after_text} "
                f"({self.direction.value})"
            ),
        }


@dataclass(frozen=True, slots=True)
class ThesisAssessment:
    """Result of comparing today's snapshot with the original thesis."""

    baseline_as_of: date | None
    current_as_of: date | None
    changes: tuple[ThesisChange, ...] = ()

    @property
    def weakened(self) -> tuple[ThesisChange, ...]:
        return tuple(c for c in self.changes if c.direction is ChangeDirection.WEAKENED)

    @property
    def improved(self) -> tuple[ThesisChange, ...]:
        return tuple(c for c in self.changes if c.direction is ChangeDirection.IMPROVED)

    @property
    def weakened_count(self) -> int:
        return len(self.weakened)

    @property
    def improved_count(self) -> int:
        return len(self.improved)

    @property
    def changed_count(self) -> int:
        return len(self.changes)

    def weakened_keys(self, groups: tuple[str, ...] = ()) -> tuple[str, ...]:
        return tuple(
            change.key
            for change in self.weakened
            if not groups or change.group in groups
        )

    def as_dict(self) -> dict:
        return {
            "baseline_as_of": self.baseline_as_of.isoformat() if self.baseline_as_of else None,
            "current_as_of": self.current_as_of.isoformat() if self.current_as_of else None,
            "weakened": self.weakened_count,
            "improved": self.improved_count,
            "changed": self.changed_count,
            "changes": [change.as_dict() for change in self.changes],
        }


@dataclass(slots=True)
class ThesisSnapshot:
    """The tracked factors for one recommendation on one day."""

    as_of: date
    factors: dict[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> Any:
        return self.factors.get(key)

    def number(self, key: str) -> float | None:
        return as_float(self.factors.get(key))

    def as_dict(self) -> dict:
        return {spec.key: self.factors.get(spec.key) for spec in THESIS_FACTORS}

    def stored_factors(self) -> dict:
        """JSON payload: values plus their human-readable rendering."""
        return {
            key: {
                "value": value,
                "text": FACTORS_BY_KEY[key].text(value) if key in FACTORS_BY_KEY else str(value),
            }
            for key, value in self.factors.items()
        }

    @classmethod
    def from_stored(cls, as_of: date, factors: Mapping[str, Any] | None) -> ThesisSnapshot:
        values: dict[str, Any] = {}
        for key, payload in (factors or {}).items():
            if isinstance(payload, Mapping):
                values[key] = payload.get("value")
            else:
                values[key] = payload
        return cls(as_of=as_of, factors=values)


def price_structure(close: float | None, sma200: float | None) -> str | None:
    """Position of the price relative to the 200 DMA, as a thesis factor."""
    price, average = as_float(close), as_float(sma200)
    if price is None or average is None or average <= 0:
        return None
    ratio = price / average
    if abs(ratio - 1) <= 0.01:
        return "AT_SMA200"
    return "ABOVE_SMA200" if ratio > 1 else "BELOW_SMA200"


#: Placeholder strings providers use for "not available".
BLANK_VALUES = {"", "n/a", "na", "none", "null"}


def _present(value: Any) -> bool:
    """Whether a raw context value is worth tracking as a thesis factor."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in BLANK_VALUES
    return True


def build_snapshot(as_of: date, values: Mapping[str, Any]) -> ThesisSnapshot:
    """Project a strategy context onto the tracked thesis factors."""
    factors: dict[str, Any] = {}
    for spec in THESIS_FACTORS:
        if spec.key == "price_vs_sma200":
            factors[spec.key] = price_structure(
                as_float(values.get("close")), as_float(values.get("sma200"))
            )
            continue
        value = values.get(spec.key)
        if not _present(value):
            continue
        if spec.kind == "number":
            number = as_float(value)
            if number is None:
                continue
            factors[spec.key] = round_or_none(number, 4)
        else:
            factors[spec.key] = str(value)
    return ThesisSnapshot(as_of=as_of, factors=factors)


def _direction(spec: FactorSpec, before: Any, after: Any) -> ChangeDirection:
    if before is None and after is not None:
        return ChangeDirection.APPEARED
    if after is None and before is not None:
        return ChangeDirection.MISSING
    if before is None or after is None:
        return ChangeDirection.UNCHANGED

    if spec.kind == "number":
        old, new = as_float(before), as_float(after)
        if old is None or new is None:
            return ChangeDirection.UNCHANGED
        if abs(new - old) < spec.tolerance:
            return ChangeDirection.UNCHANGED
        return ChangeDirection.IMPROVED if new > old else ChangeDirection.WEAKENED

    old_rank, new_rank = spec.rank(before), spec.rank(after)
    if old_rank is None or new_rank is None:
        return (
            ChangeDirection.UNCHANGED
            if str(before) == str(after)
            else ChangeDirection.MISSING
        )
    if new_rank == old_rank:
        return ChangeDirection.UNCHANGED
    return ChangeDirection.IMPROVED if new_rank > old_rank else ChangeDirection.WEAKENED


def compare(
    baseline: ThesisSnapshot | None, current: ThesisSnapshot | None
) -> ThesisAssessment:
    """Compare a snapshot with the original thesis, factor by factor."""
    if current is None:
        return ThesisAssessment(
            baseline_as_of=baseline.as_of if baseline else None, current_as_of=None
        )
    base_factors = baseline.factors if baseline else {}
    changes: list[ThesisChange] = []
    for spec in THESIS_FACTORS:
        before = base_factors.get(spec.key)
        after = current.factors.get(spec.key)
        direction = _direction(spec, before, after)
        if direction is ChangeDirection.UNCHANGED:
            continue
        changes.append(
            ThesisChange(
                key=spec.key,
                label=spec.label,
                group=spec.group,
                before=before,
                after=after,
                direction=direction,
                before_text=spec.text(before),
                after_text=spec.text(after),
            )
        )
    return ThesisAssessment(
        baseline_as_of=baseline.as_of if baseline else None,
        current_as_of=current.as_of,
        changes=tuple(changes),
    )


def summary_lines(assessment: ThesisAssessment, limit: int | None = None) -> list[str]:
    """Human-readable "before -> after" lines, weakened changes first."""
    ordered = sorted(
        assessment.changes,
        key=lambda c: (c.direction is not ChangeDirection.WEAKENED, c.label),
    )
    if limit is not None:
        ordered = ordered[:limit]
    return [f"- {change.label}: {change.before_text} -> {change.after_text}" for change in ordered]


def assessment_reason(assessment: ThesisAssessment, state: str | None = None) -> str:
    """One-line reason summarizing how much of the original thesis still holds."""
    baseline = assessment.baseline_as_of.isoformat() if assessment.baseline_as_of else "n/a"
    current = assessment.current_as_of.isoformat() if assessment.current_as_of else "n/a"
    reason = (
        f"{assessment.weakened_count} of {len(THESIS_FACTORS)} tracked factors weakened and "
        f"{assessment.improved_count} improved between {baseline} and {current}"
    )
    if state:
        reason = f"{state}: {reason}"
    return reason + "."
