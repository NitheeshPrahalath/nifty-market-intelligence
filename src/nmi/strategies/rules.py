"""Declarative strategy rules (Phase 4).

A strategy's behaviour is data, not code: every threshold lives in a
``StrategyRules`` document that is versioned in ``strategy_versions.rules`` and
evaluated by the functions in this module. The Phase-6 backtester imports the
very same evaluator, so live and backtest behaviour cannot drift apart.

A rule document is a mapping of named condition groups (``entry``, ``watch``,
``hold``, ``exit``, ``invalidation``) plus risk parameters for the price
levels. Conditions compare a named context value against a threshold:

    {"key": "composite_score", "op": ">=", "value": 60.0, "label": "Composite score"}

Supported operators: ``>=``, ``>``, ``<=``, ``<``, ``==``, ``!=``, ``in`` and
``not_in``. A condition whose value is missing evaluates to ``unknown`` and
never counts as a pass, so missing data can never manufacture a signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nmi.analysis.common import as_float, round_or_none

NUMERIC_OPS = {">=", ">", "<=", "<"}
EQUALITY_OPS = {"==", "!="}
MEMBERSHIP_OPS = {"in", "not_in"}
OPERATORS = NUMERIC_OPS | EQUALITY_OPS | MEMBERSHIP_OPS
GROUP_NAMES = ("entry", "watch", "hold", "exit", "invalidation")

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"


def _jsonable(value) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round_or_none(value)
    return str(value)


def _compare(actual, op: str, threshold) -> bool | None:
    """Three-way comparison returning ``None`` when it cannot be made."""
    if op in NUMERIC_OPS:
        left, right = as_float(actual), as_float(threshold)
        if left is None or right is None:
            return None
        if op == ">=":
            return left >= right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        return left < right
    if op in EQUALITY_OPS:
        left, right = as_float(actual), as_float(threshold)
        if left is not None and right is not None:
            result = left == right
        else:
            result = actual == threshold
        return result if op == "==" else not result
    if op in MEMBERSHIP_OPS:
        options = threshold if isinstance(threshold, (list, tuple, set)) else [threshold]
        present = actual in options
        return present if op == "in" else not present
    return None


@dataclass(frozen=True, slots=True)
class Condition:
    key: str
    op: str
    value: Any
    label: str

    def to_dict(self) -> dict:
        return {"key": self.key, "op": self.op, "value": self.value, "label": self.label}

    @classmethod
    def from_dict(cls, data: Mapping) -> Condition:
        op = str(data["op"])
        if op not in OPERATORS:
            raise ValueError(f"unsupported operator: {op}")
        return cls(
            key=str(data["key"]),
            op=op,
            value=data.get("value"),
            label=str(data.get("label") or data["key"]),
        )


@dataclass(frozen=True, slots=True)
class ConditionOutcome:
    key: str
    label: str
    op: str
    threshold: Any
    actual: Any
    status: str

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def unknown(self) -> bool:
        return self.status == UNKNOWN

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "op": self.op,
            "threshold": _jsonable(self.threshold),
            "actual": _jsonable(self.actual),
            "status": self.status,
        }

    def describe(self) -> str:
        actual = "n/a" if self.actual is None else self.actual
        threshold = _jsonable(self.threshold)
        return f"{self.label}: {actual} {self.op} {threshold} -> {self.status}"


def evaluate_condition(condition: Condition, values: Mapping) -> ConditionOutcome:
    actual = values.get(condition.key)
    outcome = _compare(actual, condition.op, condition.value)
    if actual is None or outcome is None:
        status = UNKNOWN
    else:
        status = PASS if outcome else FAIL
    return ConditionOutcome(
        key=condition.key,
        label=condition.label,
        op=condition.op,
        threshold=condition.value,
        actual=actual,
        status=status,
    )


@dataclass(frozen=True, slots=True)
class RuleGroup:
    name: str
    conditions: tuple[Condition, ...]
    mode: str = "all"
    min_passes: int | None = None

    @property
    def required_passes(self) -> int:
        if self.min_passes is not None:
            return self.min_passes
        return len(self.conditions) if self.mode == "all" else 1

    def evaluate(self, values: Mapping) -> GroupResult:
        outcomes = tuple(evaluate_condition(c, values) for c in self.conditions)
        passes = sum(1 for o in outcomes if o.passed)
        return GroupResult(
            name=self.name,
            outcomes=outcomes,
            passed=passes >= self.required_passes,
            passes=passes,
            required=self.required_passes,
        )

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {
            "conditions": [c.to_dict() for c in self.conditions],
            "mode": self.mode,
        }
        if self.min_passes is not None:
            payload["min_passes"] = self.min_passes
        return payload

    @classmethod
    def from_dict(cls, name: str, data: Mapping) -> RuleGroup:
        return cls(
            name=name,
            conditions=tuple(Condition.from_dict(c) for c in data.get("conditions", [])),
            mode=str(data.get("mode", "all")),
            min_passes=data.get("min_passes"),
        )


@dataclass(frozen=True, slots=True)
class GroupResult:
    name: str
    outcomes: tuple[ConditionOutcome, ...]
    passed: bool
    passes: int
    required: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "passes": self.passes,
            "required": self.required,
            "conditions": [o.as_dict() for o in self.outcomes],
        }

    def reasons(self) -> list[str]:
        return [f"{self.name}: {o.describe()}" for o in self.outcomes]


def condition(key: str, op: str, value: Any, label: str) -> dict:
    """Terse constructor for one condition document."""
    return {"key": key, "op": op, "value": value, "label": label}


@dataclass(frozen=True, slots=True)
class RiskParams:
    """Price-level and holding-period parameters for one strategy version.

    The entry zone is anchored to a structural level named by ``anchor``
    (``close``, ``sma20``, ``sma50``, ``high_52w``), then offset in ATR14
    units: positive offsets place the zone below the anchor (pullback
    entries), negative offsets above it (breakout entries). Anchoring to a
    level other than the close is what makes "the entry zone was reached" a
    real, data-driven event rather than a tautology.
    """

    entry_low_atr: float = 0.0
    entry_high_atr: float = 0.5
    stop_atr: float = 2.0
    target_r: float = 2.5
    holding_days_min: int = 20
    holding_days_max: int = 60
    anchor: str = "close"
    risk_bands: tuple[tuple[float, str], ...] = (
        (70.0, "LOW"),
        (55.0, "MODERATE"),
        (40.0, "HIGH"),
    )

    def risk_level(self, risk_score: float | None) -> str:
        score = as_float(risk_score)
        if score is None:
            return "MODERATE"
        for threshold, level in sorted(self.risk_bands, key=lambda band: -band[0]):
            if score >= threshold:
                return level
        return "VERY_HIGH"

    def to_dict(self) -> dict:
        return {
            "anchor": self.anchor,
            "entry_low_atr": self.entry_low_atr,
            "entry_high_atr": self.entry_high_atr,
            "stop_atr": self.stop_atr,
            "target_r": self.target_r,
            "holding_days_min": self.holding_days_min,
            "holding_days_max": self.holding_days_max,
            "risk_bands": [list(band) for band in self.risk_bands],
        }

    @classmethod
    def from_dict(cls, data: Mapping | None) -> RiskParams:
        data = data or {}
        bands = data.get("risk_bands")
        kwargs: dict[str, Any] = {
            key: data[key]
            for key in (
                "anchor",
                "entry_low_atr",
                "entry_high_atr",
                "stop_atr",
                "target_r",
                "holding_days_min",
                "holding_days_max",
            )
            if data.get(key) is not None
        }
        if bands:
            kwargs["risk_bands"] = tuple((float(lo), str(level)) for lo, level in bands)
        return cls(**kwargs)


LEVEL_KEYS = ("entry_low", "entry_high", "target_low", "target_high", "invalidation_price")


def price_levels(anchor: float | None, atr: float | None, risk: RiskParams) -> dict:
    """Entry zone, stop (invalidation) and review/target levels.

    The stop sits ``stop_atr`` ATR below the bottom of the entry zone; the
    review level is 1R above the top of the zone and the target is
    ``target_r`` R. Missing anchor or volatility yields ``None`` levels rather
    than invented numbers.
    """
    level, volatility = as_float(anchor), as_float(atr)
    if level is None or volatility is None or volatility <= 0:
        return dict.fromkeys(LEVEL_KEYS)
    low = level - risk.entry_low_atr * volatility
    high = level - risk.entry_high_atr * volatility
    stop = low - risk.stop_atr * volatility
    reward = high - stop
    if reward > 0:
        target_low = high + reward
        target_high = high + reward * risk.target_r
    else:
        target_low = target_high = None
    return {
        "entry_low": round_or_none(low, 4),
        "entry_high": round_or_none(high, 4),
        "target_low": round_or_none(target_low, 4),
        "target_high": round_or_none(target_high, 4),
        "invalidation_price": round_or_none(stop, 4),
    }


@dataclass(frozen=True, slots=True)
class StrategyRules:
    entry: RuleGroup
    watch: RuleGroup | None = None
    hold: RuleGroup | None = None
    exit: RuleGroup | None = None
    invalidation: RuleGroup | None = None
    risk: RiskParams = field(default_factory=RiskParams)

    def group(self, name: str) -> RuleGroup | None:
        return getattr(self, name, None) if name in GROUP_NAMES else None

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {"entry": self.entry.to_dict(), "risk": self.risk.to_dict()}
        for name in GROUP_NAMES[1:]:
            group = self.group(name)
            if group is not None:
                payload[name] = group.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping) -> StrategyRules:
        if "entry" not in data:
            raise ValueError("rule document must define an entry group")
        groups = {
            name: RuleGroup.from_dict(name, data[name]) for name in GROUP_NAMES if data.get(name)
        }
        return cls(
            entry=groups["entry"],
            watch=groups.get("watch"),
            hold=groups.get("hold"),
            exit=groups.get("exit"),
            invalidation=groups.get("invalidation"),
            risk=RiskParams.from_dict(data.get("risk")),
        )


def group(
    *conditions: Mapping,
    mode: str = "all",
    min_passes: int | None = None,
) -> dict:
    """Terse helper used by the strategy catalog to build rule documents."""
    return {
        "conditions": list(conditions),
        "mode": mode,
        "min_passes": min_passes,
    }
