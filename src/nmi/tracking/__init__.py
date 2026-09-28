"""Tracking, thesis monitoring, exit and notification engines (Phase 5).

Layers
------
``thesis``      snapshot the factors behind a recommendation and diff them
``exits``       independent exit/review mechanisms, each with its own reason
``lifecycle``   the explicit state machine that turns evidence into a state
``notifications`` event -> alert translation with dedupe and immutable history

Every module here is pure and free of database access, so the Phase-6
backtester can replay the same logic over historical data and get identical
results to the live pipeline.
"""

from nmi.core.models import ExitMechanism
from nmi.tracking.exits import (
    DEFAULT_EXIT_POLICY,
    ExitAction,
    ExitContext,
    ExitPolicy,
    ExitTrigger,
    evaluate_exits,
)
from nmi.tracking.lifecycle import (
    LIVE_STATES,
    STATE_RANK,
    TERMINAL_STATES,
    LifecycleDecision,
    close_decision,
    next_state,
)
from nmi.tracking.notifications import (
    NotificationDraft,
    dedupe_key,
    deduplicate,
    from_event,
    new_recommendation_draft,
    regime_change_draft,
)
from nmi.tracking.thesis import (
    THESIS_FACTORS,
    ChangeDirection,
    FactorSpec,
    ThesisAssessment,
    ThesisChange,
    ThesisSnapshot,
    assessment_reason,
    build_snapshot,
    compare,
    price_structure,
    summary_lines,
)

#: Bump when thesis/exit/notification semantics change.
TRACKING_VERSION = "v1"

__all__ = [
    "TRACKING_VERSION",
    "LIVE_STATES",
    "STATE_RANK",
    "TERMINAL_STATES",
    "THESIS_FACTORS",
    "DEFAULT_EXIT_POLICY",
    "ChangeDirection",
    "ExitAction",
    "ExitContext",
    "ExitMechanism",
    "ExitPolicy",
    "ExitTrigger",
    "FactorSpec",
    "LifecycleDecision",
    "NotificationDraft",
    "ThesisAssessment",
    "ThesisChange",
    "ThesisSnapshot",
    "assessment_reason",
    "build_snapshot",
    "close_decision",
    "compare",
    "deduplicate",
    "dedupe_key",
    "evaluate_exits",
    "from_event",
    "new_recommendation_draft",
    "next_state",
    "price_structure",
    "regime_change_draft",
    "summary_lines",
]
