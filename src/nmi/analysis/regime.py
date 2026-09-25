"""Market-regime engine (Phase 3).

Scores the market's cross-sectional condition for one index on every as-of day
from five explainable components:

* breadth          — % of members above their own SMA20 / SMA50 / SMA200
* momentum         — trailing index returns over 1m / 3m / 6m
* volatility       — annualised realised volatility of the index
* drawdown         — index close versus its running peak
* participation    — share of sectors with a majority of members above SMA50

Every component is expressed on a 0-100 scale before blending, so the resulting
``regime_score`` and its label can be explained back to the user.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from nmi.analysis.common import (
    as_float,
    blend,
    mean_score,
    pct,
    round_or_none,
    scale,
)
from nmi.core.models import MarketRegimeLabel

REGIME_WINDOWS = {
    "index_return_1m": 21,
    "index_return_3m": 63,
    "index_return_6m": 126,
}

REGIME_WEIGHTS = {
    "breadth": 0.35,
    "momentum": 0.25,
    "volatility": 0.15,
    "drawdown": 0.15,
    "participation": 0.10,
}

RISK_ON_SCORE = 65.0
CAUTIOUS_SCORE = 50.0
RISK_OFF_SCORE = 35.0

_MOMENTUM_BOUNDS = {
    "index_return_1m": (-10.0, 10.0),
    "index_return_3m": (-20.0, 20.0),
    "index_return_6m": (-35.0, 35.0),
}

_BREADTH_FIELDS = ("sma20", "sma50", "sma200")


def regime_label(score: float | None) -> MarketRegimeLabel:
    """Map a 0-100 regime score onto an interpretable market state."""
    if score is None:
        return MarketRegimeLabel.STRESSED
    if score >= RISK_ON_SCORE:
        return MarketRegimeLabel.RISK_ON
    if score >= CAUTIOUS_SCORE:
        return MarketRegimeLabel.CAUTIOUS
    if score >= RISK_OFF_SCORE:
        return MarketRegimeLabel.RISK_OFF
    return MarketRegimeLabel.STRESSED


def _returns(closes: Sequence[tuple[date, float]], idx: int) -> dict:
    out = {}
    current = closes[idx][1]
    for field, window in REGIME_WINDOWS.items():
        if idx - window < 0:
            out[field] = None
            continue
        base = closes[idx - window][1]
        out[field] = None if base <= 0 else (current / base - 1) * 100
    return out


def _hist_vol(closes: Sequence[tuple[date, float]], idx: int, window: int) -> float | None:
    """Annualised realised volatility (%) over ``window`` sessions up to ``idx``."""
    if idx < 2:
        return None
    lo = max(0, idx - window)
    sample = [c for _, c in closes[lo : idx + 1] if c and c > 0]
    if len(sample) < 3:
        return None
    rets = []
    for prev, cur in zip(sample, sample[1:], strict=False):
        rets.append(cur / prev - 1)
    if len(rets) < 2:
        return None
    mean_ret = sum(rets) / len(rets)
    variance = sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)
    return variance**0.5 * (252**0.5) * 100


def _drawdown(closes: Sequence[tuple[date, float]], idx: int) -> float | None:
    peak = max(c for _, c in closes[: idx + 1])
    if peak <= 0:
        return None
    return (closes[idx][1] / peak - 1) * 100


def index_return(closes: Sequence[tuple[date, float]], as_of: date, window: int) -> float | None:
    """Trailing ``window``-session return (%) of the series as of ``as_of``.

    Uses the latest close at or before ``as_of``; never looks forward.
    """
    series = [(d, c) for d, c in closes if d is not None and c is not None and c > 0]
    idx = None
    for i, (trade_date, _close) in enumerate(series):
        if trade_date > as_of:
            break
        idx = i
    if idx is None or idx - window < 0:
        return None
    base = series[idx - window][1]
    if base <= 0:
        return None
    return (series[idx][1] / base - 1) * 100


def _breadth(members: Sequence[Mapping]) -> tuple[dict, dict]:
    counts = {f: 0 for f in _BREADTH_FIELDS}
    eligible = {f: 0 for f in _BREADTH_FIELDS}
    member_count = 0
    for member in members:
        close = as_float(member.get("close"))
        if close is None:
            continue
        member_count += 1
        for field in _BREADTH_FIELDS:
            sma = as_float(member.get(field))
            if sma is None:
                continue
            eligible[field] += 1
            if close > sma:
                counts[field] += 1
    breadth_pct = {f: pct(counts[f], eligible[f]) for f in _BREADTH_FIELDS}
    return (
        {
            "member_count": member_count,
            "members_above_sma20": counts["sma20"],
            "members_above_sma50": counts["sma50"],
            "members_above_sma200": counts["sma200"],
        },
        breadth_pct,
    )


def compute_market_regime(
    index_closes: Sequence[tuple[date, float]],
    member_technicals: Mapping[date, Sequence[Mapping]] | None = None,
    sector_participation: Mapping[date, float] | None = None,
) -> list[dict]:
    """One regime row per index trading day, keyed on ``as_of``.

    ``member_technicals`` maps an as-of date to that day's member snapshots
    (``close``/``sma20``/``sma50``/``sma200``), and ``sector_participation``
    maps an as-of date to the share of participating sectors (0-100). Both are
    optional: missing data simply drops the affected components.
    """
    series = sorted(((d, as_float(c)) for d, c in index_closes), key=lambda x: x[0])
    series = [(d, c) for d, c in series if d is not None and c is not None and c > 0]
    if not series:
        return []

    member_technicals = member_technicals or {}
    sector_participation = sector_participation or {}

    rows: list[dict] = []
    for idx, (as_of, _close) in enumerate(series):
        counts, breadth = _breadth(member_technicals.get(as_of, ()))
        returns = _returns(series, idx)
        vol20 = _hist_vol(series, idx, 20)
        vol60 = _hist_vol(series, idx, 60)
        drawdown = _drawdown(series, idx)
        participation = as_float(sector_participation.get(as_of))

        components = {
            "breadth": mean_score(breadth[f] for f in _BREADTH_FIELDS),
            "momentum": mean_score(
                scale(returns[field], *bounds) for field, bounds in _MOMENTUM_BOUNDS.items()
            ),
            "volatility": scale(vol60 if vol60 is not None else vol20, 30.0, 8.0),
            "drawdown": scale(drawdown, -25.0, 0.0),
            "participation": participation,
        }
        score = blend(components, REGIME_WEIGHTS)

        row = {
            "as_of": as_of,
            "member_count": counts["member_count"],
            "members_above_sma20": counts["members_above_sma20"],
            "members_above_sma50": counts["members_above_sma50"],
            "members_above_sma200": counts["members_above_sma200"],
            "breadth_above_sma20_pct": round_or_none(breadth["sma20"]),
            "breadth_above_sma50_pct": round_or_none(breadth["sma50"]),
            "breadth_above_sma200_pct": round_or_none(breadth["sma200"]),
            "index_return_1m": round_or_none(returns["index_return_1m"]),
            "index_return_3m": round_or_none(returns["index_return_3m"]),
            "index_return_6m": round_or_none(returns["index_return_6m"]),
            "index_hist_vol_20": round_or_none(vol20, 6),
            "index_hist_vol_60": round_or_none(vol60, 6),
            "index_drawdown_pct": round_or_none(drawdown),
            "sector_participation_pct": round_or_none(participation),
            "regime_score": round_or_none(score),
            "regime_label": regime_label(score).value,
        }
        rows.append(row)
    return rows
