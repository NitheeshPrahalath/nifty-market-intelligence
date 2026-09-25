from __future__ import annotations

from datetime import date

import pytest

from nmi.analysis.sector import compute_sector_metrics, sector_state
from nmi.core.models import SectorState

AS_OF = date(2024, 6, 28)


def test_sector_states_map_score_bands():
    assert sector_state(80) == SectorState.LEADING
    assert sector_state(55) == SectorState.NEUTRAL
    assert sector_state(40) == SectorState.LAGGING
    assert sector_state(10) == SectorState.DEFENSIVE
    assert sector_state(None) == SectorState.DEFENSIVE


def test_sector_aggregates_members():
    members = {
        1: [
            {
                "close": 120.0,
                "sma50": 100.0,
                "sma200": 90.0,
                "return_1m": 4.0,
                "return_3m": 12.0,
                "return_6m": 20.0,
                "rs_3m": 6.0,
                "rsi14": 60.0,
            },
            {
                "close": 90.0,
                "sma50": 100.0,
                "sma200": 95.0,
                "return_1m": -2.0,
                "return_3m": 4.0,
                "return_6m": 10.0,
                "rs_3m": 2.0,
                "rsi14": 50.0,
            },
        ],
        2: [
            {
                "close": 80.0,
                "sma50": 100.0,
                "sma200": 100.0,
                "return_1m": -5.0,
                "return_3m": -8.0,
                "return_6m": -12.0,
                "rs_3m": -10.0,
                "rsi14": 35.0,
            }
        ],
    }

    rows = compute_sector_metrics(AS_OF, members, index_return_3m=8.0)

    assert [r["sector_id"] for r in rows] == [1, 2]
    strong, weak = rows
    assert strong["as_of"] == AS_OF
    assert strong["member_count"] == 2
    assert strong["breadth_above_sma50_pct"] == pytest.approx(50.0)
    assert strong["breadth_above_sma200_pct"] == pytest.approx(50.0)
    assert strong["avg_return_3m"] == pytest.approx(8.0)
    assert strong["avg_rs_3m"] == pytest.approx(4.0)
    assert strong["avg_rsi14"] == pytest.approx(55.0)
    assert strong["relative_to_index_pct"] == pytest.approx(0.0)
    assert strong["sector_score"] > 50
    assert strong["sector_state"] == "NEUTRAL"

    assert weak["breadth_above_sma50_pct"] == pytest.approx(0.0)
    assert weak["relative_to_index_pct"] == pytest.approx(-16.0)
    assert weak["sector_state"] in {"LAGGING", "DEFENSIVE"}
    assert weak["sector_score"] < strong["sector_score"]


def test_sector_skips_empty_and_keeps_partial_members():
    members = {
        1: [{"close": 110.0, "sma50": 100.0}],
        2: [],
    }

    rows = compute_sector_metrics(AS_OF, members)

    assert len(rows) == 1
    row = rows[0]
    assert row["sector_id"] == 1
    assert row["breadth_above_sma50_pct"] == pytest.approx(100.0)
    assert row["breadth_above_sma200_pct"] is None
    assert row["avg_return_3m"] is None
    assert row["relative_to_index_pct"] is None
    assert row["sector_score"] is not None
