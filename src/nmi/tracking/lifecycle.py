"""Recommendation lifecycle state machine (Phase 5).

Tracking re-evaluates an open recommendation every day and has to decide what
happens next. The rule below is deliberately explicit and ordered so the
outcome is reproducible and explainable:

1. a closed recommendation is never reopened;
2. a recommendation that already reached ``EXIT`` is closed once it has been
   seen in that state, so the user always sees the exit before the close;
3. an exit mechanism beats everything;
4. a review mechanism beats thesis weakening;
5. thesis weakening beats the raw signal state;
6. otherwise the strategy's own signal state wins.

Upgrade/downgrade labels are derived from the state ranking below.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nmi.core.models import EventType, ExitMechanism, RecommendationState
from nmi.tracking.exits import ExitAction, ExitTrigger
from nmi.tracking.thesis import (
    ThesisAssessment,
    assessment_reason,
    summary_lines,
)

#: Ordering used to decide whether a state change is an upgrade or downgrade.
STATE_RANK: dict[RecommendationState, int] = {
    RecommendationState.WATCH: 0,
    RecommendationState.THESIS_WEAKENING: 1,
    RecommendationState.POTENTIAL_ENTRY: 2,
    RecommendationState.HOLD: 3,
    RecommendationState.ENTRY: 4,
    RecommendationState.EXIT_REVIEW: 5,
    RecommendationState.EXIT: 6,
    RecommendationState.CLOSED: 7,
}

TERMINAL_STATES = (RecommendationState.CLOSED,)
LIVE_STATES = (
    RecommendationState.WATCH,
    RecommendationState.POTENTIAL_ENTRY,
    RecommendationState.ENTRY,
    RecommendationState.HOLD,
    RecommendationState.THESIS_WEAKENING,
    RecommendationState.EXIT_REVIEW,
    RecommendationState.EXIT,
)


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    """The outcome of one daily re-evaluation."""

    state: RecommendationState
    event: EventType | None
    mechanism: ExitMechanism | None
    reasons: tuple[str, ...]
    change_summary: str | None
    triggers: tuple[ExitTrigger, ...] = ()
    assessment: ThesisAssessment | None = None
    changed: bool = False

    @property
    def is_exit(self) -> bool:
        return self.state is RecommendationState.EXIT

    @property
    def is_review(self) -> bool:
        return self.state is RecommendationState.EXIT_REVIEW


def _is_upgrade(previous: RecommendationState, new: RecommendationState) -> bool:
    if new is RecommendationState.EXIT_REVIEW and previous not in (
        RecommendationState.EXIT,
        RecommendationState.CLOSED,
    ):
        return False
    if new in (RecommendationState.EXIT, RecommendationState.CLOSED):
        return False
    return STATE_RANK[new] > STATE_RANK[previous]


def _summary(
    previous: RecommendationState,
    new: RecommendationState,
    assessment: ThesisAssessment | None,
    triggers: Sequence[ExitTrigger],
    limit: int = 6,
) -> str:
    parts = [f"{previous.value} -> {new.value}"]
    if assessment is not None:
        parts.append(assessment_reason(assessment))
        for line in summary_lines(assessment, limit=limit):
            parts.append(line)
    for trigger in triggers:
        parts.append(f"[{trigger.mechanism.value}] {trigger.reason}")
    return " ".join(parts)


def close_decision(previous: RecommendationState, as_of) -> LifecycleDecision:
    """Close a recommendation that has already been marked ``EXIT``."""
    reason = (
        f"Recommendation closed on {as_of.isoformat() if hasattr(as_of, 'isoformat') else as_of} "
        "after the exit signal was raised and reviewed."
    )
    return LifecycleDecision(
        state=RecommendationState.CLOSED,
        event=EventType.RECOMMENDATION_CLOSED,
        mechanism=None,
        reasons=(reason,),
        change_summary=f"{previous.value} -> CLOSED. {reason}",
        changed=previous is not RecommendationState.CLOSED,
    )


def next_state(
    previous: RecommendationState,
    signal_state: RecommendationState | None,
    assessment: ThesisAssessment | None,
    triggers: Sequence[ExitTrigger],
    *,
    min_weakened: int = 1,
    active: bool = True,
) -> LifecycleDecision:
    """Decide the state of a tracked recommendation for today.

    ``active`` says whether the user is actually in the position. A condition
    that would end an active trade is only *flagged for review* on a
    recommendation that was never entered, because there is nothing to exit.
    """
    if previous is RecommendationState.CLOSED:
        return LifecycleDecision(
            state=previous,
            event=None,
            mechanism=None,
            reasons=("Recommendation is closed and is never reopened.",),
            change_summary=None,
        )

    exits = [t for t in triggers if t.action is ExitAction.EXIT]
    reviews = [t for t in triggers if t.action is ExitAction.REVIEW]
    weakened = assessment.weakened_count if assessment else 0

    if exits:
        primary = exits[0]
        if not active:
            return LifecycleDecision(
                state=RecommendationState.EXIT_REVIEW,
                event=EventType.RISK_TRIGGERED
                if primary.mechanism is ExitMechanism.RISK
                else EventType.EXIT_REVIEW_SIGNAL,
                mechanism=primary.mechanism,
                reasons=(
                    *(t.reason for t in triggers),
                    "The recommendation is not an active position, so the condition is "
                    "flagged for review instead of an exit.",
                ),
                change_summary=(
                    _summary(previous, RecommendationState.EXIT_REVIEW, assessment, triggers)
                    + " (not yet held: flagged for review, not an exit)"
                ),
                triggers=tuple(triggers),
                assessment=assessment,
                changed=previous is not RecommendationState.EXIT_REVIEW,
            )
        return LifecycleDecision(
            state=RecommendationState.EXIT,
            event=EventType.RISK_TRIGGERED
            if primary.mechanism is ExitMechanism.RISK
            else EventType.EXIT_SIGNAL,
            mechanism=primary.mechanism,
            reasons=tuple(t.reason for t in triggers),
            change_summary=_summary(previous, RecommendationState.EXIT, assessment, triggers),
            triggers=tuple(triggers),
            assessment=assessment,
            changed=previous is not RecommendationState.EXIT,
        )

    if reviews:
        primary = reviews[0]
        event = (
            EventType.TARGET_REACHED
            if primary.mechanism is ExitMechanism.TARGET
            else EventType.EXIT_REVIEW_SIGNAL
        )
        return LifecycleDecision(
            state=RecommendationState.EXIT_REVIEW,
            event=event,
            mechanism=primary.mechanism,
            reasons=tuple(t.reason for t in triggers),
            change_summary=_summary(
                previous, RecommendationState.EXIT_REVIEW, assessment, triggers
            ),
            triggers=tuple(triggers),
            assessment=assessment,
            changed=previous is not RecommendationState.EXIT_REVIEW,
        )

    if signal_state is RecommendationState.EXIT:
        state = RecommendationState.EXIT if active else RecommendationState.EXIT_REVIEW
        return LifecycleDecision(
            state=state,
            event=EventType.EXIT_SIGNAL
            if active
            else EventType.EXIT_REVIEW_SIGNAL,
            mechanism=ExitMechanism.STRATEGY,
            reasons=(
                "The strategy's exit rules are satisfied.",
                *(
                    ()
                    if active
                    else (
                        "The recommendation is not an active position, so the condition is "
                        "flagged for review instead of an exit.",
                    )
                ),
            ),
            change_summary=_summary(previous, state, assessment, triggers),
            triggers=tuple(triggers),
            assessment=assessment,
            changed=previous is not state,
        )

    if weakened >= min_weakened and signal_state not in (
        RecommendationState.EXIT,
        RecommendationState.EXIT_REVIEW,
    ):
        new = RecommendationState.THESIS_WEAKENING
        return LifecycleDecision(
            state=new,
            event=EventType.THESIS_WEAKENED,
            mechanism=None,
            reasons=(assessment_reason(assessment, "THESIS_WEAKENING"),),
            change_summary=_summary(previous, new, assessment, triggers),
            triggers=tuple(triggers),
            assessment=assessment,
            changed=previous is not new,
        )

    new = signal_state or RecommendationState.HOLD
    if new is previous:
        improved = bool(assessment and assessment.improved_count > 0 and weakened == 0)
        event = EventType.THESIS_IMPROVED if improved else None
        return LifecycleDecision(
            state=previous,
            event=event,
            mechanism=None,
            reasons=(assessment_reason(assessment) if assessment else "No rule change.",),
            change_summary=None,
            triggers=tuple(triggers),
            assessment=assessment,
            changed=event is not None,
        )

    if new is RecommendationState.ENTRY:
        event = EventType.ENTRY_ZONE_REACHED
    elif (
        previous is RecommendationState.THESIS_WEAKENING
        and new not in (RecommendationState.EXIT, RecommendationState.EXIT_REVIEW)
        and STATE_RANK[new] < STATE_RANK[previous]
        and assessment is not None
        and assessment.improved_count > 0
    ):
        # Recovering from a weakened thesis is news in its own right: the user
        # who was warned wants to know the warning no longer applies.
        event = EventType.THESIS_IMPROVED
    elif _is_upgrade(previous, new):
        event = EventType.RECOMMENDATION_UPGRADED
    else:
        event = EventType.RECOMMENDATION_DOWNGRADED
    return LifecycleDecision(
        state=new,
        event=event,
        mechanism=None,
        reasons=(assessment_reason(assessment) if assessment else f"Signal state {new.value}.",),
        change_summary=_summary(previous, new, assessment, triggers),
        triggers=tuple(triggers),
        assessment=assessment,
        changed=True,
    )
