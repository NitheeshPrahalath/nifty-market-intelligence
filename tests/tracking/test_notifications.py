"""Notification engine (Phase 5): reason, dedupe key and immutability."""

from __future__ import annotations

from datetime import date

from nmi.core.enums import Severity
from nmi.core.models import EventType, ExitMechanism, NotificationType, RecommendationState
from nmi.tracking import (
    ExitAction,
    ExitTrigger,
    NotificationDraft,
    build_snapshot,
    compare,
    dedupe_key,
    deduplicate,
    from_event,
    new_recommendation_draft,
    regime_change_draft,
)
from nmi.tracking.lifecycle import next_state

DAY_ONE = date(2024, 1, 2)
DAY_TWO = date(2024, 3, 15)

ORIGINAL = {
    "composite_score": 72.5,
    "growth_score": 60.0,
    "quality_score": 65.0,
    "risk_score": 72.0,
    "rs_trend": "IMPROVING",
    "trend_state": "UPTREND",
    "close": 1000.0,
    "sma200": 900.0,
    "regime_label": "RISK_ON",
}


def _event(**overrides) -> dict:
    base = {
        "recommendation_id": 7,
        "as_of": DAY_TWO,
        "event_type": EventType.THESIS_WEAKENED.value,
        "mechanism": None,
        "previous_state": RecommendationState.ENTRY.value,
        "new_state": RecommendationState.THESIS_WEAKENING.value,
        "title": "Thesis weakened for RELIANCE",
        "message": "2 of 13 tracked factors weakened",
        "detail": {},
        "recommendation_version": 2,
        "price": 1010.0,
        "instrument_id": 3,
        "strategy_code": "mt_momentum_quality",
    }
    base.update(overrides)
    return base


def _assessment(**overrides):
    return compare(
        build_snapshot(DAY_ONE, ORIGINAL),
        build_snapshot(DAY_TWO, {**ORIGINAL, **overrides}),
    )


def test_every_event_maps_to_a_notification_with_a_reason():
    drafts = from_event(_event(), symbol="RELIANCE")
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.notification_type is NotificationType.THESIS_WEAKENING
    assert draft.severity is Severity.WARNING
    assert draft.recommendation_id == 7
    assert draft.recommendation_version == 2
    assert draft.instrument_id == 3
    assert draft.strategy_code == "mt_momentum_quality"
    assert draft.reason
    assert draft.message


def test_money_losing_events_are_at_least_warnings():
    for event_type, expected in (
        (EventType.EXIT_SIGNAL, Severity.ERROR),
        (EventType.RISK_TRIGGERED, Severity.ERROR),
        (EventType.EXIT_REVIEW_SIGNAL, Severity.WARNING),
        (EventType.RECOMMENDATION_DOWNGRADED, Severity.WARNING),
        (EventType.RECOMMENDATION_CREATED, Severity.INFO),
        (EventType.RECOMMENDATION_CLOSED, Severity.INFO),
    ):
        draft = from_event(_event(event_type=event_type.value), symbol="RELIANCE")[0]
        assert draft.severity is expected


def test_thesis_weakening_splits_into_group_notifications():
    drafts = from_event(
        _event(),
        symbol="RELIANCE",
        assessment=_assessment(growth_score=35.0, rs_trend="DETERIORATING"),
    )
    types = [d.notification_type for d in drafts]
    assert NotificationType.THESIS_WEAKENING in types
    assert NotificationType.FUNDAMENTAL_CHANGE in types
    assert NotificationType.TECHNICAL_CHANGE in types
    fundamental = next(
        d for d in drafts if d.notification_type is NotificationType.FUNDAMENTAL_CHANGE
    )
    assert "Growth score" in fundamental.message
    assert len(set(d.dedupe_key for d in drafts)) == len(drafts)


def test_no_group_notification_without_that_group_change():
    drafts = from_event(_event(), symbol="RELIANCE", assessment=_assessment(growth_score=35.0))
    assert [d.notification_type for d in drafts] == [
        NotificationType.THESIS_WEAKENING,
        NotificationType.FUNDAMENTAL_CHANGE,
    ]


def test_exit_triggers_produce_mechanism_specific_notifications():
    triggers = [
        ExitTrigger(
            mechanism=ExitMechanism.RISK,
            action=ExitAction.EXIT,
            reason="Price 880 breached the 900 stop",
            detail={"price": 880.0},
        ),
        ExitTrigger(
            mechanism=ExitMechanism.TECHNICAL,
            action=ExitAction.EXIT,
            reason="Death cross on the 50/200 DMA",
            detail={},
        ),
    ]
    drafts = from_event(
        _event(event_type=EventType.EXIT_SIGNAL.value, mechanism="RISK"),
        symbol="RELIANCE",
        triggers=triggers,
    )
    by_type = {d.notification_type: d for d in drafts}
    assert NotificationType.EXIT_CONDITION_TRIGGERED in by_type
    assert NotificationType.RISK_CONDITION_TRIGGERED in by_type
    assert NotificationType.TECHNICAL_CHANGE in by_type
    assert "880" in by_type[NotificationType.RISK_CONDITION_TRIGGERED].message
    assert "Death cross" in by_type[NotificationType.TECHNICAL_CHANGE].message


def test_target_mechanism_maps_to_the_target_review_type():
    drafts = from_event(
        _event(event_type=EventType.TARGET_REACHED.value, mechanism="TARGET"),
        symbol="RELIANCE",
        triggers=[
            ExitTrigger(
                mechanism=ExitMechanism.TARGET,
                action=ExitAction.REVIEW,
                reason="Price reached the 1R review zone",
                detail={},
            )
        ],
    )
    # The base and the mechanism share the type, so only one notification fires.
    assert [d.notification_type for d in drafts] == [NotificationType.TARGET_REVIEW_ZONE_REACHED]


def test_new_recommendation_draft_is_stable_per_recommendation():
    first = new_recommendation_draft(
        recommendation_id=7,
        instrument_id=3,
        symbol="RELIANCE",
        strategy_code="mt_momentum_quality",
        as_of=DAY_ONE,
        state=RecommendationState.WATCH.value,
        message="Watch the accumulation range",
        reason="Composite score 72.5 with improving RS",
        price=1000.0,
    )
    again = new_recommendation_draft(
        recommendation_id=7,
        instrument_id=3,
        symbol="RELIANCE",
        strategy_code="mt_momentum_quality",
        as_of=DAY_ONE,
        state=RecommendationState.WATCH.value,
        message="Watch the accumulation range",
        reason="Composite score 72.5 with improving RS",
        price=1000.0,
    )
    assert first.dedupe_key == again.dedupe_key == "NEW_RECOMMENDATION:rec7:v1"
    columns = first.as_columns("v1")
    assert columns["notification_type"] == "NEW_RECOMMENDATION"
    assert columns["dedupe_key"] == first.dedupe_key
    assert columns["recommendation_version"] == 1
    assert columns["payload"]["state"] == "WATCH"


def test_regime_change_draft_mentions_both_labels():
    draft = regime_change_draft(
        index_id=1,
        index_code="NIFTY_50",
        previous="RISK_ON",
        current="CAUTIOUS",
        as_of=DAY_TWO,
        score=55.5,
    )
    assert draft.notification_type is NotificationType.MARKET_REGIME_CHANGE
    assert "RISK_ON" in draft.reason and "CAUTIOUS" in draft.reason
    assert draft.index_id == 1
    assert draft.dedupe_key == "MARKET_REGIME_CHANGE:idx1:CAUTIOUS"
    # Returning to RISK_ON is a different fact, so it alerts again.
    back = regime_change_draft(
        index_id=1,
        index_code="NIFTY_50",
        previous="CAUTIOUS",
        current="RISK_ON",
        as_of=DAY_TWO,
        score=70.0,
    )
    assert back.dedupe_key != draft.dedupe_key


def test_dedupe_key_encodes_the_exact_fact():
    assert dedupe_key(NotificationType.THESIS_WEAKENING, recommendation_id=1, version=3) == (
        "THESIS_WEAKENING:rec1:v3"
    )
    assert dedupe_key(
        NotificationType.TECHNICAL_CHANGE, recommendation_id=1, version=3, mechanism="TECHNICAL"
    ) == "TECHNICAL_CHANGE:rec1:v3:TECHNICAL"
    assert dedupe_key(
        NotificationType.MARKET_REGIME_CHANGE, index_id=2, label="STRESSED", as_of=DAY_TWO
    ) == "MARKET_REGIME_CHANGE:idx2:STRESSED:2024-03-15"


def test_deduplicate_drops_already_stored_and_repeated_facts():
    drafts = [
        new_recommendation_draft(
            recommendation_id=1,
            instrument_id=3,
            symbol="A",
            strategy_code="s",
            as_of=DAY_ONE,
            state="WATCH",
            message="m",
            reason="r",
        ),
        new_recommendation_draft(
            recommendation_id=1,
            instrument_id=3,
            symbol="A",
            strategy_code="s",
            as_of=DAY_ONE,
            state="WATCH",
            message="m",
            reason="r",
        ),
        new_recommendation_draft(
            recommendation_id=2,
            instrument_id=3,
            symbol="A",
            strategy_code="s",
            as_of=DAY_ONE,
            state="WATCH",
            message="m",
            reason="r",
        ),
    ]
    fresh = deduplicate(drafts, {drafts[0].dedupe_key})
    assert [d.recommendation_id for d in fresh] == [2]


def test_a_repeated_eod_run_produces_no_new_facts():
    drafts = from_event(_event(), symbol="RELIANCE", assessment=_assessment(growth_score=35.0))
    assert deduplicate(drafts, set()) == drafts
    keys = {d.dedupe_key for d in drafts}
    assert deduplicate(drafts, keys) == []


def test_end_to_end_event_to_notification_pipeline():
    assessment = _assessment(growth_score=35.0, risk_score=40.0)
    decision = next_state(RecommendationState.ENTRY, RecommendationState.ENTRY, assessment, [])
    assert decision.event is EventType.THESIS_WEAKENED
    drafts = from_event(
        _event(
            event_type=decision.event.value,
            new_state=decision.state.value,
            message=decision.change_summary,
        ),
        symbol="RELIANCE",
        assessment=decision.assessment,
        triggers=decision.triggers,
    )
    assert drafts
    assert all(isinstance(draft, NotificationDraft) for draft in drafts)
    assert all(draft.reason for draft in drafts)
    assert all(draft.dedupe_key for draft in drafts)
    columns = [draft.as_columns("v1") for draft in drafts]
    assert {c["notification_type"] for c in columns} >= {"THESIS_WEAKENING", "FUNDAMENTAL_CHANGE"}
    assert all(c["reason"] for c in columns)
    assert all(c["as_of"] == DAY_TWO for c in columns)
