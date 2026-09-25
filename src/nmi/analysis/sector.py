"""Sector analysis engine (Phase 3).

Aggregates the cross-section of one sector's members (breadth, trailing
returns, relative strength, RSI) into a single sector score and state, so the
platform can answer "which sectors are leading or lagging right now, and by how
much relative to the index".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from nmi.analysis.common import as_float, blend, mean, mean_score, pct, round_or_none, scale
from nmi.core.models import SectorState

SECTOR_WEIGHTS = {
    "breadth": 0.35,
    "return": 0.25,
    "relative_strength": 0.20,
    "rsi": 0.20,
}

LEADING_SCORE = 65.0
NEUTRAL_SCORE = 50.0
LAGGING_SCORE = 35.0


def sector_state(score: float | None) -> SectorState:
    if score is None:
        return SectorState.DEFENSIVE
    if score >= LEADING_SCORE:
        return SectorState.LEADING
    if score >= NEUTRAL_SCORE:
        return SectorState.NEUTRAL
    if score >= LAGGING_SCORE:
        return SectorState.LAGGING
    return SectorState.DEFENSIVE


def _breadth_pct(members: Sequence[Mapping], field: str) -> float | None:
    above = 0
    eligible = 0
    for member in members:
        close = as_float(member.get("close"))
        sma = as_float(member.get(field))
        if close is None or sma is None:
            continue
        eligible += 1
        if close > sma:
            above += 1
    return pct(above, eligible)


def compute_sector_metrics(
    as_of: date,
    members_by_sector: Mapping[object, Sequence[Mapping]],
    index_return_3m: float | None = None,
) -> list[dict]:
    """One row per sector for ``as_of``.

    ``members_by_sector`` maps a sector id to that day's member metric rows.
    Sectors without any usable member data are skipped, while partially covered
    sectors keep whatever components they have.
    """
    rows: list[dict] = []
    for sector_id, members in members_by_sector.items():
        if not members:
            continue
        breadth50 = _breadth_pct(members, "sma50")
        breadth200 = _breadth_pct(members, "sma200")
        avg_return_1m = mean(m.get("return_1m") for m in members)
        avg_return_3m = mean(m.get("return_3m") for m in members)
        avg_return_6m = mean(m.get("return_6m") for m in members)
        avg_rs_3m = mean(m.get("rs_3m") for m in members)
        avg_rsi14 = mean(m.get("rsi14") for m in members)

        components = {
            "breadth": mean_score(v for v in (breadth50, breadth200) if v is not None),
            "return": scale(avg_return_3m, -25.0, 25.0),
            "relative_strength": scale(avg_rs_3m, -20.0, 20.0),
            "rsi": scale(avg_rsi14, 35.0, 65.0),
        }
        score = blend(components, SECTOR_WEIGHTS)
        relative = None
        index_3m = as_float(index_return_3m)
        if index_3m is not None and avg_return_3m is not None:
            relative = avg_return_3m - index_3m

        rows.append(
            {
                "as_of": as_of,
                "sector_id": sector_id,
                "member_count": len(members),
                "breadth_above_sma50_pct": round_or_none(breadth50),
                "breadth_above_sma200_pct": round_or_none(breadth200),
                "avg_return_1m": round_or_none(avg_return_1m),
                "avg_return_3m": round_or_none(avg_return_3m),
                "avg_return_6m": round_or_none(avg_return_6m),
                "avg_rs_3m": round_or_none(avg_rs_3m),
                "avg_rsi14": round_or_none(avg_rsi14),
                "relative_to_index_pct": round_or_none(relative),
                "sector_score": round_or_none(score),
                "sector_state": sector_state(score).value,
            }
        )
    return rows
