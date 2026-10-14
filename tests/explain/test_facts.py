"""The fact sheet is the closed world an explanation may draw on."""

from __future__ import annotations

from nmi.explain.facts import build_fact_sheet
from tests.explain.conftest import AS_OF


def test_unknown_recommendation_is_none(sqlite_session):
    assert build_fact_sheet(sqlite_session, 999999) is None


def test_fact_sheet_covers_the_recommendation(recommendation, sqlite_session):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)
    assert sheet is not None
    assert sheet.symbol == "INFY"
    assert sheet.as_of == AS_OF
    keys = sheet.keys
    for expected in (
        "state",
        "horizon",
        "risk_level",
        "current_price",
        "invalidation_price",
        "thesis.composite_score",
        "version.composite_score",
        "factor_count",
        "events.count",
        "baseline.composite_score",
    ):
        assert expected in keys, expected


def test_facts_render_numbers_with_a_quotable_text(recommendation, sqlite_session):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)
    price = sheet.by_key("current_price")
    assert price is not None
    assert price.text == "1650.00"
    assert price.group == "price"


def test_the_source_hash_changes_when_a_fact_changes(recommendation, sqlite_session):
    first = build_fact_sheet(sqlite_session, recommendation.id)
    assert first is not None
    baseline = first.source_hash()

    recommendation.current_price = 1700.0
    sqlite_session.flush()
    second = build_fact_sheet(sqlite_session, recommendation.id)
    assert second is not None
    assert second.source_hash() != baseline


def test_change_facts_appear_between_the_baseline_and_current(recommendation, sqlite_session):
    sheet = build_fact_sheet(sqlite_session, recommendation.id, "change")
    assert sheet is not None
    keys = sheet.keys
    # The composite score fell 75.0 -> 72.4 in the fixture.
    assert "change.composite_score" in keys
    assert "change.price_vs_sma200" in keys
    change = sheet.by_key("change.composite_score")
    assert change is not None
    assert "WEAKENED" in change.text
