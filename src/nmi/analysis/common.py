"""Shared numeric helpers for the Phase-3 analysis engines.

The engines are pure functions: they take already-computed metric rows and
return plain dictionaries of 0-100 scores, leaving persistence and audit
trails to ``MetricsService``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def as_float(value) -> float | None:
    """Coerce to a finite float, mapping NaN/Inf/None to ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def clamp(value, lo: float = 0.0, hi: float = 100.0) -> float | None:
    v = as_float(value)
    if v is None:
        return None
    return max(lo, min(hi, v))


def scale(value, lo: float, hi: float) -> float | None:
    """Map ``value`` linearly from ``[lo, hi]`` onto ``[0, 100]``, clamped.

    Bounds may be supplied in either order to express an inverse mapping
    (e.g. ``scale(volatility, 30, 8)`` scores low volatility highly).
    """
    v = as_float(value)
    if v is None or hi == lo:
        return None
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100.0))


def mean(values: Iterable) -> float | None:
    vals = [v for v in (as_float(x) for x in values) if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def mean_score(values: Iterable) -> float | None:
    """Mean of the available 0-100 component scores, or ``None`` if empty."""
    return clamp(mean(values))


def pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator * 100.0


def round_or_none(value, digits: int = 4) -> float | None:
    v = as_float(value)
    return None if v is None else round(v, digits)


def blend(components: dict, weights: dict) -> float | None:
    """Weighted mean over the components that carry a value.

    Weights of the missing components are excluded and the remainder
    renormalised, so partial data degrades gracefully instead of biasing the
    score toward zero.
    """
    total = 0.0
    weight_sum = 0.0
    for name, weight in weights.items():
        value = clamp(components.get(name))
        if value is None:
            continue
        total += value * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return total / weight_sum
