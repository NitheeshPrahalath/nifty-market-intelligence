"""Notification engine (Phase 5).

Turns recommendation events and market-regime changes into notification rows.
Three rules shape the design:

* every notification carries the reason that produced it — no unexplained
  ``BUY``/``SELL`` alerts;
* ``dedupe_key`` encodes the *exact fact* (recommendation + version + mechanism
  or regime label), so a repeated EOD run never re-alerts on unchanged data
  while a genuinely new fact always alerts;
* notifications are immutable history — nothing is edited or re-sent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nmi.core.enums import Severity
from nmi.core.models import EventType, ExitMechanism, NotificationType
from nmi.tracking.exits import ExitTrigger
from nmi.tracking.thesis import ThesisAssessment, assessment_reason

#: Lifecycle events that must produce a user notification.
EVENT_NOTIFICATION: dict[EventType, NotificationType] = {
    EventType.RECOMMENDATION_CREATED: NotificationType.NEW_RECOMMENDATION,
    EventType.ENTRY_ZONE_REACHED: NotificationType.ENTRY_ZONE_REACHED,
    EventType.RECOMMENDATION_UPGRADED: NotificationType.RECOMMENDATION_UPGRADED,
    EventType.RECOMMENDATION_DOWNGRADED: NotificationType.RECOMMENDATION_DOWNGRADED,
    EventType.THESIS_IMPROVED: NotificationType.THESIS_IMPROVED,
    EventType.THESIS_WEAKENED: NotificationType.THESIS_WEAKENING,
    EventType.TARGET_REACHED: NotificationType.TARGET_REVIEW_ZONE_REACHED,
    EventType.RISK_TRIGGERED: NotificationType.RISK_CONDITION_TRIGGERED,
    EventType.EXIT_REVIEW_SIGNAL: NotificationType.RECOMMENDATION_DOWNGRADED,
    EventType.EXIT_SIGNAL: NotificationType.EXIT_CONDITION_TRIGGERED,
    EventType.RECOMMENDATION_CLOSED: NotificationType.RECOMMENDATION_CLOSED,
}

#: Severity per event; anything that can cost money is at least a warning.
EVENT_SEVERITY: dict[EventType, Severity] = {
    EventType.RECOMMENDATION_CREATED: Severity.INFO,
    EventType.ENTRY_ZONE_REACHED: Severity.INFO,
    EventType.RECOMMENDATION_UPGRADED: Severity.INFO,
    EventType.RECOMMENDATION_DOWNGRADED: Severity.WARNING,
    EventType.THESIS_IMPROVED: Severity.INFO,
    EventType.THESIS_WEAKENED: Severity.WARNING,
    EventType.TARGET_REACHED: Severity.INFO,
    EventType.RISK_TRIGGERED: Severity.ERROR,
    EventType.EXIT_REVIEW_SIGNAL: Severity.WARNING,
    EventType.EXIT_SIGNAL: Severity.ERROR,
    EventType.RECOMMENDATION_CLOSED: Severity.INFO,
}

#: Extra notifications derived from the thesis change itself.
FACTOR_GROUP_NOTIFICATION = {
    "technical": NotificationType.TECHNICAL_CHANGE,
    "fundamental": NotificationType.FUNDAMENTAL_CHANGE,
}

MECHANISM_NOTIFICATION = {
    ExitMechanism.TARGET: NotificationType.TARGET_REVIEW_ZONE_REACHED,
    ExitMechanism.RISK: NotificationType.RISK_CONDITION_TRIGGERED,
    ExitMechanism.VALUATION: NotificationType.RISK_CONDITION_TRIGGERED,
    ExitMechanism.TECHNICAL: NotificationType.TECHNICAL_CHANGE,
    ExitMechanism.FUNDAMENTAL: NotificationType.FUNDAMENTAL_CHANGE,
}


@dataclass(slots=True)
class NotificationDraft:
    """A notification ready to be persisted (not yet deduplicated)."""

    notification_type: NotificationType
    as_of: date
    title: str
    message: str
    reason: str
    dedupe_key: str
    severity: Severity = Severity.INFO
    recommendation_id: int | None = None
    recommendation_version: int | None = None
    instrument_id: int | None = None
    index_id: int | None = None
    strategy_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_columns(self, calc_version: str) -> dict:
        return {
            "notification_type": self.notification_type.value,
            "severity": self.severity.value,
            "as_of": self.as_of,
            "recommendation_id": self.recommendation_id,
            "recommendation_version": self.recommendation_version,
            "instrument_id": self.instrument_id,
            "index_id": self.index_id,
            "strategy_code": self.strategy_code,
            "title": self.title,
            "message": self.message,
            "reason": self.reason,
            "payload": self.payload,
            "dedupe_key": self.dedupe_key,
            "calc_version": calc_version,
        }


def dedupe_key(
    notification_type: NotificationType,
    *,
    recommendation_id: int | None = None,
    version: int | None = None,
    mechanism: ExitMechanism | str | None = None,
    index_id: int | None = None,
    label: str | None = None,
    as_of: date | None = None,
) -> str:
    """Deterministic identity of a single alertable fact."""
    parts = [notification_type.value]
    if recommendation_id is not None:
        parts.append(f"rec{recommendation_id}")
    if version is not None:
        parts.append(f"v{version}")
    if mechanism is not None:
        parts.append(str(getattr(mechanism, "value", mechanism)))
    if index_id is not None:
        parts.append(f"idx{index_id}")
    if label is not None:
        parts.append(label)
    if as_of is not None and recommendation_id is None and index_id is not None:
        parts.append(as_of.isoformat())
    return ":".join(parts)


def _title(
    notification_type: NotificationType, subject: str, state: str | None = None
) -> str:
    labels = {
        NotificationType.NEW_RECOMMENDATION: "New recommendation",
        NotificationType.ENTRY_ZONE_REACHED: "Entry zone reached",
        NotificationType.RECOMMENDATION_UPGRADED: "Recommendation upgraded",
        NotificationType.RECOMMENDATION_DOWNGRADED: "Recommendation downgraded",
        NotificationType.THESIS_WEAKENING: "Thesis weakening",
        NotificationType.THESIS_IMPROVED: "Thesis improving",
        NotificationType.FUNDAMENTAL_CHANGE: "Fundamental change",
        NotificationType.TECHNICAL_CHANGE: "Technical change",
        NotificationType.TARGET_REVIEW_ZONE_REACHED: "Target/review zone reached",
        NotificationType.RISK_CONDITION_TRIGGERED: "Risk condition triggered",
        NotificationType.EXIT_CONDITION_TRIGGERED: "Exit condition triggered",
        NotificationType.RECOMMENDATION_CLOSED: "Recommendation closed",
        NotificationType.MARKET_REGIME_CHANGE: "Market regime change",
    }
    title = labels[notification_type]
    if state and notification_type not in (
        NotificationType.MARKET_REGIME_CHANGE,
        NotificationType.NEW_RECOMMENDATION,
    ):
        return f"{title}: {subject} ({state})"
    return f"{title}: {subject}"


def _factor_lines(assessment: ThesisAssessment | None, groups: tuple[str, ...]) -> list[str]:
    if assessment is None:
        return []
    return [
        f"- {change.label}: {change.before_text} -> {change.after_text}"
        for change in assessment.changes
        if change.group in groups
    ]


def from_event(
    event: Mapping,
    *,
    symbol: str,
    assessment: ThesisAssessment | None = None,
    triggers: Sequence[ExitTrigger] = (),
) -> list[NotificationDraft]:
    """Translate one recommendation event into notifications.

    Returns more than one notification when a single event carries distinct
    facts the user must see separately (e.g. an exit plus the technical change
    that caused it).
    """
    event_type = EventType(event["event_type"])
    as_of = event["as_of"]
    recommendation_id = event["recommendation_id"]
    version = event.get("recommendation_version")
    instrument_id = event.get("instrument_id")
    strategy_code = event.get("strategy_code")
    mechanism = event.get("mechanism")
    state = event.get("new_state")
    reason = event.get("message") or event.get("title") or ""
    base_type = EVENT_NOTIFICATION[event_type]
    severity = EVENT_SEVERITY[event_type]

    drafts: list[NotificationDraft] = [
        NotificationDraft(
            notification_type=base_type,
            severity=severity,
            as_of=as_of,
            title=_title(base_type, symbol, state),
            message=event.get("title") or reason,
            reason=reason,
            dedupe_key=dedupe_key(
                base_type,
                recommendation_id=recommendation_id,
                version=version,
                mechanism=mechanism,
            ),
            recommendation_id=recommendation_id,
            recommendation_version=version,
            instrument_id=instrument_id,
            strategy_code=strategy_code,
            payload={
                "event_type": event_type.value,
                "mechanism": mechanism,
                "previous_state": event.get("previous_state"),
                "new_state": state,
                "price": event.get("price"),
                "detail": event.get("detail") or {},
            },
        )
    ]

    for trigger in triggers:
        extra_type = MECHANISM_NOTIFICATION.get(trigger.mechanism)
        if extra_type is None or extra_type == base_type:
            continue
        drafts.append(
            NotificationDraft(
                notification_type=extra_type,
                severity=severity,
                as_of=as_of,
                title=_title(extra_type, symbol, state),
                message=trigger.reason,
                reason=trigger.reason,
                dedupe_key=dedupe_key(
                    extra_type,
                    recommendation_id=recommendation_id,
                    version=version,
                    mechanism=trigger.mechanism,
                ),
                recommendation_id=recommendation_id,
                recommendation_version=version,
                instrument_id=instrument_id,
                strategy_code=strategy_code,
                payload={
                    "event_type": event_type.value,
                    "mechanism": trigger.mechanism.value,
                    "detail": trigger.detail,
                },
            )
        )

    if event_type is EventType.THESIS_WEAKENED and assessment is not None:
        for group, extra_type in FACTOR_GROUP_NOTIFICATION.items():
            lines = _factor_lines(assessment, (group,))
            if not lines:
                continue
            message = "; ".join(lines)
            drafts.append(
                NotificationDraft(
                    notification_type=extra_type,
                    severity=Severity.WARNING,
                    as_of=as_of,
                    title=_title(extra_type, symbol, state),
                    message=message,
                    reason=assessment_reason(assessment),
                    dedupe_key=dedupe_key(
                        extra_type,
                        recommendation_id=recommendation_id,
                        version=version,
                        label=group,
                    ),
                    recommendation_id=recommendation_id,
                    recommendation_version=version,
                    instrument_id=instrument_id,
                    strategy_code=strategy_code,
                    payload={"group": group, "changes": lines},
                )
            )
    return drafts


def new_recommendation_draft(
    *, recommendation_id: int, instrument_id: int, symbol: str, strategy_code: str,
    as_of: date, state: str, message: str, reason: str, price: float | None = None
) -> NotificationDraft:
    """Alert for a freshly created recommendation (fires once per recommendation)."""
    return NotificationDraft(
        notification_type=NotificationType.NEW_RECOMMENDATION,
        severity=Severity.INFO,
        as_of=as_of,
        title=_title(NotificationType.NEW_RECOMMENDATION, symbol, state),
        message=message,
        reason=reason,
        dedupe_key=dedupe_key(
            NotificationType.NEW_RECOMMENDATION, recommendation_id=recommendation_id, version=1
        ),
        recommendation_id=recommendation_id,
        recommendation_version=1,
        instrument_id=instrument_id,
        strategy_code=strategy_code,
        payload={"state": state, "price": price},
    )


def regime_change_draft(
    *, index_id: int, index_code: str, previous: str, current: str, as_of: date, score: float | None
) -> NotificationDraft:
    """Alert when the market regime label changes for an index."""
    score_text = f" (regime score {score:.1f})" if score is not None else ""
    reason = (
        f"Market regime for {index_code} moved from {previous} to {current}{score_text} "
        f"on {as_of.isoformat()}."
    )
    return NotificationDraft(
        notification_type=NotificationType.MARKET_REGIME_CHANGE,
        severity=Severity.INFO,
        as_of=as_of,
        title=f"Market regime change: {index_code} {previous} -> {current}",
        message=reason,
        reason=reason,
        dedupe_key=dedupe_key(
            NotificationType.MARKET_REGIME_CHANGE, index_id=index_id, label=current
        ),
        index_id=index_id,
        payload={
            "index_code": index_code,
            "previous": previous,
            "current": current,
            "score": score,
        },
    )


def deduplicate(
    drafts: Iterable[NotificationDraft], existing_keys: set[str]
) -> list[NotificationDraft]:
    """Keep the first draft per dedupe key that is not already stored."""
    seen: set[str] = set()
    out: list[NotificationDraft] = []
    for draft in drafts:
        if draft.dedupe_key in existing_keys or draft.dedupe_key in seen:
            continue
        seen.add(draft.dedupe_key)
        out.append(draft)
    return out
