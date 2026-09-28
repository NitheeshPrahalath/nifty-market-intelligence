"""Thesis snapshots and change detection (Phase 5)."""

from __future__ import annotations

from datetime import date

import pytest

from nmi.core.models import ChangeDirection
from nmi.tracking import (
    THESIS_FACTORS,
    ThesisSnapshot,
    assessment_reason,
    build_snapshot,
    compare,
    summary_lines,
)

DAY_ONE = date(2024, 1, 2)
DAY_TWO = date(2024, 3, 15)

ORIGINAL = {
    "composite_score": 72.5,
    "growth_score": 60.0,
    "quality_score": 65.0,
    "eps_growth_pct": 18.0,
    "relative_strength_score": 70.0,
    "risk_score": 72.0,
    "valuation_score": 55.0,
    "pe_percentile_3y": 35.0,
    "rs_trend": "IMPROVING",
    "trend_state": "UPTREND",
    "close": 1000.0,
    "sma200": 900.0,
    "valuation_label": "FAIRLY_VALUED",
    "sector_state": "LEADING",
    "regime_label": "RISK_ON",
}


def _values(**overrides) -> dict:
    return {**ORIGINAL, **overrides}


def test_snapshot_keeps_a_fixed_factor_order():
    snapshot = build_snapshot(DAY_ONE, _values())
    assert snapshot.as_of == DAY_ONE
    assert tuple(snapshot.as_dict()) == tuple(spec.key for spec in THESIS_FACTORS)
    for key in snapshot.factors:
        assert key in {spec.key for spec in THESIS_FACTORS}
    assert snapshot.factors["composite_score"] == 72.5
    assert snapshot.as_dict()["regime_label"] == "RISK_ON"


def test_price_structure_is_derived_from_close_and_sma200():
    above = build_snapshot(DAY_ONE, _values())
    assert above.factors["price_vs_sma200"] == "ABOVE_SMA200"

    below = build_snapshot(DAY_ONE, _values(close=800.0))
    assert below.factors["price_vs_sma200"] == "BELOW_SMA200"

    missing = build_snapshot(DAY_ONE, {k: v for k, v in _values().items() if k != "sma200"})
    assert missing.factors["price_vs_sma200"] is None


def test_identical_thesis_produces_no_changes():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()), build_snapshot(DAY_TWO, _values())
    )
    assert assessment.changes == ()
    assert assessment.weakened_count == 0
    assert assessment.improved_count == 0
    reason = assessment_reason(assessment)
    assert "0 of 13 tracked factors weakened" in reason
    assert "0 improved" in reason
    assert DAY_ONE.isoformat() in reason and DAY_TWO.isoformat() in reason


def test_score_drops_beyond_tolerance_weaken_the_thesis():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(DAY_TWO, _values(growth_score=35.0, risk_score=40.0)),
    )
    assert assessment.weakened_keys() == ("growth_score", "risk_score")
    assert all(c.direction is ChangeDirection.WEAKENED for c in assessment.changes)
    assert assessment.changes[0].before == 60.0
    assert assessment.changes[0].after == 35.0


def test_tolerance_absorbs_ordinary_noise():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(DAY_TWO, _values(composite_score=71.0, risk_score=70.5)),
    )
    assert assessment.changes == ()


def test_ordinal_factors_grade_in_the_right_direction():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(
            DAY_TWO,
            _values(
                rs_trend="DETERIORATING",
                trend_state="DOWNTREND",
                close=800.0,
                valuation_label="EXPENSIVE",
                sector_state="LAGGING",
                regime_label="STRESSED",
            ),
        ),
    )
    assert [c.key for c in assessment.weakened] == [
        "rs_trend",
        "trend_state",
        "price_vs_sma200",
        "valuation_label",
        "sector_state",
        "regime_label",
    ]
    assert not assessment.improved
    reason = assessment_reason(assessment, "THESIS_WEAKENING")
    assert "THESIS_WEAKENING" in reason
    assert "6 of 13" in reason


def test_improvements_are_reported_separately():
    assessment = compare(
        build_snapshot(DAY_ONE, _values(growth_score=35.0, risk_score=40.0)),
        build_snapshot(DAY_TWO, _values(growth_score=60.0, risk_score=72.0)),
    )
    assert assessment.weakened_count == 0
    assert assessment.improved_count == 2
    assert [c.key for c in assessment.improved] == ["growth_score", "risk_score"]


def test_group_filters_separate_technical_and_fundamental_changes():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(DAY_TWO, _values(growth_score=35.0, trend_state="DOWNTREND")),
    )
    assert assessment.weakened_keys(("fundamental",)) == ("growth_score",)
    assert assessment.weakened_keys(("technical",)) == ("trend_state",)
    assert set(assessment.weakened_keys()) == {"growth_score", "trend_state"}


def test_comparison_is_against_the_original_not_the_previous_day():
    original = build_snapshot(DAY_ONE, _values(composite_score=72.5))
    day_two = build_snapshot(date(2024, 2, 1), _values(composite_score=71.0))
    day_three = build_snapshot(DAY_TWO, _values(composite_score=70.0))
    assert compare(original, day_two).changes == ()
    assert compare(original, day_three).changed_count == 1
    assert compare(original, day_three).weakened[0].after == 70.0


def test_change_and_summary_payloads_are_serialisable():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(DAY_TWO, _values(growth_score=35.0, rs_trend="DETERIORATING")),
    )
    payload = assessment.as_dict()
    assert payload["baseline_as_of"] == DAY_ONE.isoformat()
    assert payload["current_as_of"] == DAY_TWO.isoformat()
    for change in payload["changes"]:
        assert change["direction"] in {d.value for d in ChangeDirection}
        assert change["text"]
    lines = summary_lines(assessment, limit=1)
    assert len(lines) == 1
    assert "Growth score" in lines[0]


def test_snapshot_round_trips_through_storage():
    snapshot = build_snapshot(DAY_ONE, _values())
    restored = ThesisSnapshot.from_stored(DAY_ONE, snapshot.stored_factors())
    assert restored.factors == snapshot.factors
    assert restored.as_of == DAY_ONE
    assert restored.number("composite_score") == 72.5


def test_snapshots_do_not_need_every_factor():
    partial = build_snapshot(DAY_ONE, {"composite_score": 50.0})
    assert partial.number("composite_score") == 50.0
    assert partial.number("growth_score") is None
    assert tuple(partial.factors) == ("composite_score", "price_vs_sma200")
    # Absent factors simply do not exist in the stored payload.
    assert partial.stored_factors() == {
        "composite_score": {"value": 50.0, "text": "50.0"},
        "price_vs_sma200": {"value": None, "text": "n/a"},
    }


@pytest.mark.parametrize("value", [None, "", "  ", "n/a", "N/A"])
def test_blank_ordinal_values_are_not_tracked(value):
    snapshot = build_snapshot(DAY_ONE, _values(valuation_label=value))
    assert "valuation_label" not in snapshot.factors


@pytest.mark.parametrize("value", [None, "", "  ", "n/a", "N/A"])
def test_blank_ordinal_values_never_count_as_weakening(value):
    current = build_snapshot(DAY_TWO, _values(valuation_label=value))
    assessment = compare(build_snapshot(DAY_ONE, _values()), current)
    assert assessment.weakened_count == 0
    assert assessment.improved_count == 0
    assert [c.direction for c in assessment.changes] == [ChangeDirection.MISSING]


def test_a_factor_that_disappears_is_reported_but_not_counted_as_weakened():
    assessment = compare(
        build_snapshot(DAY_ONE, _values()),
        build_snapshot(DAY_TWO, {k: v for k, v in _values().items() if k != "close"}),
    )
    assert [c.direction for c in assessment.changes] == [ChangeDirection.MISSING]
    assert assessment.weakened_count == 0
    assert assessment.improved_count == 0
