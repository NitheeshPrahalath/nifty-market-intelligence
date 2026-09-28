"""Recommendation lifecycle state machine (Phase 5)."""

from __future__ import annotations

from datetime import date

from nmi.core.models import EventType, ExitMechanism, RecommendationState
from nmi.tracking import (
    ExitAction,
    ExitTrigger,
    ThesisAssessment,
    close_decision,
    next_state,
)
from nmi.tracking.thesis import build_snapshot, compare

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


def _assessment(**overrides) -> ThesisAssessment:
    return compare(
        build_snapshot(DAY_ONE, ORIGINAL),
        build_snapshot(DAY_TWO, {**ORIGINAL, **overrides}),
    )


def _trigger(action: ExitAction, mechanism: ExitMechanism = ExitMechanism.RISK) -> ExitTrigger:
    return ExitTrigger(mechanism=mechanism, action=action, reason="fired for this test")


def test_no_change_keeps_the_current_state():
    decision = next_state(RecommendationState.ENTRY, RecommendationState.ENTRY, _assessment(), [])
    assert decision.state is RecommendationState.ENTRY
    assert decision.event is None
    assert decision.changed is False
    assert decision.change_summary is None


def test_signal_state_wins_when_nothing_else_fired():
    decision = next_state(
        RecommendationState.WATCH, RecommendationState.POTENTIAL_ENTRY, _assessment(), []
    )
    assert decision.state is RecommendationState.POTENTIAL_ENTRY
    assert decision.event is EventType.RECOMMENDATION_UPGRADED
    assert decision.changed is True
    assert decision.change_summary and "WATCH -> POTENTIAL_ENTRY" in decision.change_summary


def test_thesis_weakening_needs_the_configured_number_of_factors():
    one = _assessment(growth_score=35.0)
    decision = next_state(
        RecommendationState.ENTRY, RecommendationState.ENTRY, one, [], min_weakened=2
    )
    assert decision.state is RecommendationState.ENTRY
    assert decision.changed is False

    two = _assessment(growth_score=35.0, risk_score=40.0)
    decision = next_state(
        RecommendationState.ENTRY, RecommendationState.ENTRY, two, [], min_weakened=2
    )
    assert decision.state is RecommendationState.THESIS_WEAKENING
    assert decision.event is EventType.THESIS_WEAKENED
    assert "Growth score" in decision.change_summary


def test_thesis_improvement_is_recorded():
    decision = next_state(
        RecommendationState.THESIS_WEAKENING,
        RecommendationState.WATCH,
        compare(
            build_snapshot(DAY_ONE, {**ORIGINAL, "growth_score": 30.0}),
            build_snapshot(DAY_TWO, ORIGINAL),
        ),
        [],
    )
    assert decision.state is RecommendationState.WATCH
    assert decision.event is EventType.THESIS_IMPROVED
    assert decision.changed is True


def test_review_mechanism_beats_thesis_weakening():
    decision = next_state(
        RecommendationState.ENTRY,
        RecommendationState.ENTRY,
        _assessment(growth_score=35.0, risk_score=40.0),
        [_trigger(ExitAction.REVIEW, ExitMechanism.TARGET)],
    )
    assert decision.state is RecommendationState.EXIT_REVIEW
    assert decision.event is EventType.TARGET_REACHED
    assert decision.mechanism is ExitMechanism.TARGET
    assert decision.is_review


def test_exit_mechanism_beats_everything():
    decision = next_state(
        RecommendationState.HOLD,
        RecommendationState.ENTRY,
        _assessment(growth_score=35.0),
        [
            _trigger(ExitAction.REVIEW, ExitMechanism.TARGET),
            _trigger(ExitAction.EXIT, ExitMechanism.RISK),
        ],
    )
    assert decision.state is RecommendationState.EXIT
    assert decision.event is EventType.RISK_TRIGGERED
    assert decision.mechanism is ExitMechanism.RISK
    assert decision.is_exit
    assert len(decision.triggers) == 2


def test_non_risk_exit_uses_the_generic_exit_event():
    decision = next_state(
        RecommendationState.ENTRY,
        RecommendationState.ENTRY,
        _assessment(),
        [_trigger(ExitAction.EXIT, ExitMechanism.TECHNICAL)],
    )
    assert decision.event is EventType.EXIT_SIGNAL


def test_strategy_exit_state_exits_even_without_triggers():
    decision = next_state(RecommendationState.HOLD, RecommendationState.EXIT, _assessment(), [])
    assert decision.state is RecommendationState.EXIT
    assert decision.event is EventType.EXIT_SIGNAL
    assert decision.mechanism is ExitMechanism.STRATEGY


def test_repeating_the_same_state_is_not_a_change():
    decision = next_state(
        RecommendationState.EXIT_REVIEW,
        RecommendationState.ENTRY,
        _assessment(),
        [_trigger(ExitAction.REVIEW, ExitMechanism.TARGET)],
    )
    assert decision.state is RecommendationState.EXIT_REVIEW
    assert decision.changed is False


def test_closed_recommendations_are_never_reopened():
    for signal in RecommendationState:
        decision = next_state(RecommendationState.CLOSED, signal, _assessment(), [])
        assert decision.state is RecommendationState.CLOSED
        assert decision.event is None
        assert decision.changed is False
        assert decision.reasons


def test_close_decision_is_terminal_and_explained():
    decision = close_decision(RecommendationState.EXIT, DAY_TWO)
    assert decision.state is RecommendationState.CLOSED
    assert decision.event is EventType.RECOMMENDATION_CLOSED
    assert decision.changed is True
    assert DAY_TWO.isoformat() in decision.change_summary
    assert "EXIT -> CLOSED" in decision.change_summary
    assert close_decision(RecommendationState.CLOSED, DAY_TWO).changed is False


def test_decision_reasons_are_always_populated():
    for previous in RecommendationState:
        decision = next_state(
            previous, RecommendationState.ENTRY, _assessment(growth_score=20.0), []
        )
        assert decision.reasons
        if decision.changed:
            assert decision.change_summary


def test_an_unheld_recommendation_is_reviewed_not_exited():
    decision = next_state(
        RecommendationState.WATCH,
        RecommendationState.WATCH,
        _assessment(),
        [_trigger(ExitAction.EXIT, ExitMechanism.RISK)],
        active=False,
    )
    assert decision.state is RecommendationState.EXIT_REVIEW
    assert decision.event is EventType.RISK_TRIGGERED
    assert "not yet held" in decision.change_summary
    assert any("not an active position" in reason for reason in decision.reasons)


def test_a_held_recommendation_exits():
    decision = next_state(
        RecommendationState.ENTRY,
        RecommendationState.ENTRY,
        _assessment(),
        [_trigger(ExitAction.EXIT, ExitMechanism.RISK)],
        active=True,
    )
    assert decision.state is RecommendationState.EXIT
    assert decision.event is EventType.RISK_TRIGGERED


def test_strategy_exit_on_an_unheld_recommendation_is_a_review():
    decision = next_state(
        RecommendationState.POTENTIAL_ENTRY,
        RecommendationState.EXIT,
        _assessment(),
        [],
        active=False,
    )
    assert decision.state is RecommendationState.EXIT_REVIEW
    assert decision.event is EventType.EXIT_REVIEW_SIGNAL
    assert any("not an active position" in reason for reason in decision.reasons)


def test_review_mechanisms_are_unaffected_by_being_held():
    held = next_state(
        RecommendationState.ENTRY,
        RecommendationState.ENTRY,
        _assessment(),
        [_trigger(ExitAction.REVIEW, ExitMechanism.TARGET)],
        active=True,
    )
    unheld = next_state(
        RecommendationState.WATCH,
        RecommendationState.WATCH,
        _assessment(),
        [_trigger(ExitAction.REVIEW, ExitMechanism.TARGET)],
        active=False,
    )
    assert held.state is unheld.state is RecommendationState.EXIT_REVIEW
