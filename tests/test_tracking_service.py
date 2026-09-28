"""Phase 5 tracking, exits and notifications against the real pipeline (SQLite)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from nmi.core.enums import RunStatus, Severity
from nmi.core.models import (
    EventType,
    ExitMechanism,
    IngestionError,
    IngestionRun,
    Instrument,
    MarketRegime,
    Notification,
    Recommendation,
    RecommendationEvent,
    RecommendationState,
    RecommendationVersion,
    Signal,
    TechnicalIndicator,
    ThesisSnapshot,
)
from nmi.ingestion import store
from nmi.ingestion.backfill import IngestionService
from nmi.metrics.service import MetricsService

START = date(2024, 1, 1)
CREATED = date(2024, 3, 1)
EXIT_DAY = date(2024, 6, 27)
END = date(2024, 6, 28)
INDEX = ["NIFTY_50"]


def _count(session, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return session.scalar(statement)


def _seed(session) -> None:
    svc = IngestionService(session)
    svc.seed_universe()
    svc.backfill_corporate_actions(INDEX, START, END)
    svc.backfill_prices(INDEX, START, END)
    svc.backfill_fundamentals()


def _pipeline(session) -> MetricsService:
    """Full Phase 2-5 chain on the last available session."""
    _seed(session)
    msvc = MetricsService(session)
    msvc.backfill_index_prices(INDEX)
    msvc.compute_eod(INDEX, START, END)
    return msvc


def _pipeline_until(session, created: date) -> MetricsService:
    """Same chain, but recommendations are created on ``created`` so that real
    later sessions exist to review them on."""
    _seed(session)
    msvc = MetricsService(session)
    msvc.backfill_index_prices(INDEX)
    msvc.compute_fundamentals(INDEX)
    for job in (
        "compute_technical",
        "compute_momentum",
        "compute_valuation",
        "compute_sector",
        "compute_regime",
        "compute_horizon",
        "compute_scoring",
        "compute_signals",
    ):
        getattr(msvc, job)(INDEX, START, END)
    msvc.generate_recommendations(INDEX, created)
    return msvc


def test_eod_tracking_writes_a_snapshot_per_recommendation_day(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    recs = store.get_tracked_recommendations(sqlite_session)
    snapshots = sqlite_session.scalars(select(ThesisSnapshot)).all()
    assert snapshots
    for snapshot in snapshots:
        rec = next(r for r in recs if r.id == snapshot.recommendation_id)
        assert snapshot.as_of == END
        assert snapshot.factors
        assert snapshot.summary
        assert snapshot.state in {s.value for s in RecommendationState}
    # The baseline snapshot is the original thesis, stored once per rec.
    for rec in recs:
        baseline = sqlite_session.scalar(
            select(ThesisSnapshot).where(
                ThesisSnapshot.recommendation_id == rec.id,
                ThesisSnapshot.as_of == rec.first_as_of,
            )
        )
        assert baseline is not None
        assert baseline.summary
        # The baseline holds the factors the recommendation was made on, i.e.
        # the same day and strategy version the user was given.
        signal = sqlite_session.scalar(
            select(Signal).where(
                Signal.instrument_id == rec.instrument_id,
                Signal.strategy_version_id == rec.strategy_version_id,
                Signal.as_of == rec.first_as_of,
            )
        )
        assert signal is not None
        assert baseline.composite_score == signal.composite_score
        assert baseline.price == signal.price
        assert baseline.confidence == rec.confidence
        # JSON storage renders the value as a float plus its display text.
        assert float(baseline.factors["composite_score"]["value"]) == float(
            signal.composite_score
        )
        assert baseline.factors["composite_score"]["text"] == f"{float(signal.composite_score):.1f}"


def test_recommendations_are_never_overwritten(sqlite_session, pointed_at_long_fixtures):
    _pipeline(sqlite_session)
    recs = store.get_tracked_recommendations(sqlite_session)
    for rec in recs:
        first = sqlite_session.scalar(
            select(RecommendationVersion).where(
                RecommendationVersion.recommendation_id == rec.id,
                RecommendationVersion.version == 1,
            )
        )
        assert first is not None
        assert first.as_of == rec.first_as_of
        assert first.change_summary == "Initial recommendation"
        # The frozen levels the user was given survive every later review.
        assert first.entry_low == rec.entry_low
        assert first.target_low == rec.target_low
        assert first.invalidation_price == rec.invalidation_price
        assert rec.thesis
        assert rec.created_reason


def test_tracking_appends_versions_and_events_only_on_change(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    recs = store.get_tracked_recommendations(sqlite_session)
    before_versions = _count(sqlite_session, RecommendationVersion)
    before_events = _count(sqlite_session, RecommendationEvent)

    again = msvc.track_recommendations(INDEX, END)
    assert again.status == RunStatus.SUCCEEDED
    assert again.items_failed == 0
    # Re-reviewing the same day changes nothing: the audit trail is immutable.
    assert _count(sqlite_session, RecommendationVersion) == before_versions
    assert _count(sqlite_session, RecommendationEvent) == before_events
    # ... but the review itself is still recorded as having happened.
    assert all(rec.last_reviewed_at is not None for rec in recs)
    assert all(rec.last_as_of == END for rec in recs)

    later = msvc.track_recommendations(INDEX, END + timedelta(days=1))
    assert later.status == RunStatus.SUCCEEDED
    # A no-change day still advances the review stamp, not the state.
    assert all(rec.last_reviewed_at is not None for rec in recs)
    for rec in recs:
        versions = sqlite_session.scalars(
            select(RecommendationVersion)
            .where(RecommendationVersion.recommendation_id == rec.id)
            .order_by(RecommendationVersion.version)
        ).all()
        assert [v.version for v in versions] == list(range(1, len(versions) + 1))
        assert versions[-1].version == rec.latest_version


def test_thesis_snapshots_compare_against_the_original_day(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    rec = store.get_tracked_recommendations(sqlite_session)[0]
    snapshots = sqlite_session.scalars(
        select(ThesisSnapshot)
        .where(ThesisSnapshot.recommendation_id == rec.id)
        .order_by(ThesisSnapshot.as_of)
    ).all()
    as_ofs = {s.as_of for s in snapshots}
    assert rec.first_as_of in as_ofs
    if rec.state is RecommendationState.EXIT_REVIEW or rec.state is RecommendationState.EXIT:
        current = next(s for s in snapshots if s.as_of == END)
        assert current.weakened_count >= 0
        assert current.summary


def test_events_carry_a_reason_and_reference_a_version(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    events = sqlite_session.scalars(select(RecommendationEvent)).all()
    assert events
    types = {e.event_type for e in events}
    assert EventType.RECOMMENDATION_CREATED.value in types
    for event in events:
        rec = sqlite_session.get(Recommendation, event.recommendation_id)
        assert rec is not None
        assert event.title
        assert event.message
        assert event.dedupe_key
        assert event.recommendation_version == rec.latest_version or event.event_type in {
            EventType.RECOMMENDATION_CREATED.value
        }
        assert event.calc_version == msvc.calc_version


def test_created_event_is_written_once_per_recommendation(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    created = sqlite_session.scalars(
        select(RecommendationEvent).where(
            RecommendationEvent.event_type == EventType.RECOMMENDATION_CREATED.value
        )
    ).all()
    assert len(created) == _count(sqlite_session, Recommendation)
    assert len({e.dedupe_key for e in created}) == len(created)


def test_notifications_are_reasoned_deduplicated_and_immutable(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    notifications = sqlite_session.scalars(select(Notification)).all()
    assert notifications
    assert all(n.reason for n in notifications)
    assert all(n.title and n.message for n in notifications)
    assert all(n.calc_version == msvc.calc_version for n in notifications)
    assert len({n.dedupe_key for n in notifications}) == len(notifications)
    types = {n.notification_type for n in notifications}
    assert "NEW_RECOMMENDATION" in types

    before = _count(sqlite_session, Notification)
    again = msvc.dispatch_notifications(INDEX, END)
    assert again.status == RunStatus.SUCCEEDED
    assert _count(sqlite_session, Notification) == before


def test_regime_change_notification_is_deduplicated_by_label(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    latest = sqlite_session.scalars(
        select(MarketRegime)
        .where(MarketRegime.index_id.is_not(None))
        .order_by(MarketRegime.as_of.desc())
    ).first()
    assert latest is not None
    before = _count(
        sqlite_session, Notification, Notification.notification_type == "MARKET_REGIME_CHANGE"
    )
    flip = "STRESSED" if latest.regime_label != "STRESSED" else "RISK_ON"
    latest.regime_label = flip
    sqlite_session.commit()
    assert msvc.dispatch_notifications(INDEX, latest.as_of).items_processed >= 1
    rows = sqlite_session.scalars(
        select(Notification).where(Notification.notification_type == "MARKET_REGIME_CHANGE")
    ).all()
    assert len(rows) == before + 1
    assert flip in rows[-1].reason
    assert rows[-1].as_of == latest.as_of
    # The same label on a later day is the same fact: no new alert.
    assert msvc.dispatch_notifications(INDEX, latest.as_of + timedelta(days=1))
    assert (
        _count(
            sqlite_session, Notification, Notification.notification_type == "MARKET_REGIME_CHANGE"
        )
        == before + 1
    )


def test_new_recommendation_notification_fires_once_per_recommendation(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    rec_ids = {r.id for r in store.get_tracked_recommendations(sqlite_session)}
    rows = sqlite_session.scalars(
        select(Notification).where(Notification.notification_type == "NEW_RECOMMENDATION")
    ).all()
    assert {n.recommendation_id for n in rows} == rec_ids
    assert all(n.recommendation_version == 1 for n in rows)


def test_a_risk_condition_on_an_unheld_recommendation_is_reviewed_not_exited(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline_until(sqlite_session, date(2024, 3, 1))
    recs = store.get_tracked_recommendations(sqlite_session)
    rec = recs[0]
    # Never entered, so there is no position to exit.
    assert rec.active_since is None
    rec.invalidation_price = 10_000_000.0
    sqlite_session.commit()

    assert msvc.track_recommendations(INDEX, EXIT_DAY).status == RunStatus.SUCCEEDED
    after = sqlite_session.get(Recommendation, rec.id)
    assert after.state is RecommendationState.EXIT_REVIEW
    assert after.active_since is None
    assert after.closed_at is None
    event = sqlite_session.scalar(
        select(RecommendationEvent)
        .where(
            RecommendationEvent.recommendation_id == rec.id,
            RecommendationEvent.as_of == EXIT_DAY,
        )
        .order_by(RecommendationEvent.id.desc())
    )
    assert event.new_state == RecommendationState.EXIT_REVIEW.value
    reasons = (event.detail or {}).get("reasons", [])
    assert any("not an active position" in reason for reason in reasons)


def test_a_recommendation_in_exit_is_closed_on_the_next_review(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline_until(sqlite_session, date(2024, 3, 1))
    recs = store.get_tracked_recommendations(sqlite_session)
    assert recs
    rec = recs[0]
    # The user is in the position and their stop is hit on a real later session.
    rec.state = RecommendationState.ENTRY
    rec.active_since = rec.first_as_of
    rec.invalidation_price = 10_000_000.0
    sqlite_session.commit()

    assert msvc.track_recommendations(INDEX, EXIT_DAY).status == RunStatus.SUCCEEDED
    after_exit = sqlite_session.get(Recommendation, rec.id)
    assert after_exit.state is RecommendationState.EXIT
    assert after_exit.exit_reason
    assert after_exit.closed_at is None
    exit_event = sqlite_session.scalar(
        select(RecommendationEvent).where(
            RecommendationEvent.recommendation_id == rec.id,
            RecommendationEvent.as_of == EXIT_DAY,
        )
    )
    assert exit_event is not None
    assert exit_event.event_type in {
        EventType.RISK_TRIGGERED.value,
        EventType.EXIT_SIGNAL.value,
    }
    mechanisms = {t["mechanism"] for t in (exit_event.detail or {}).get("triggers", [])}
    assert ExitMechanism.RISK.value in mechanisms
    assert any(
        "invalidation" in t["reason"].lower()
        for t in exit_event.detail["triggers"]
        if t["mechanism"] == ExitMechanism.RISK.value
    )
    assert "invalidation" in (after_exit.exit_reason or "").lower()
    assert "10" in (after_exit.exit_reason or "")

    # Re-running the same day must not close it.
    msvc.track_recommendations(INDEX, EXIT_DAY)
    assert sqlite_session.get(Recommendation, rec.id).state is RecommendationState.EXIT

    assert msvc.track_recommendations(INDEX, END).status == RunStatus.SUCCEEDED
    closed = sqlite_session.get(Recommendation, rec.id)
    assert closed.state is RecommendationState.CLOSED
    assert closed.closed_at is not None
    close_event = sqlite_session.scalar(
        select(RecommendationEvent).where(
            RecommendationEvent.recommendation_id == rec.id,
            RecommendationEvent.event_type == EventType.RECOMMENDATION_CLOSED.value,
        )
    )
    assert close_event is not None
    assert close_event.previous_state == RecommendationState.EXIT.value
    assert close_event.new_state == RecommendationState.CLOSED.value
    assert msvc.dispatch_notifications(INDEX, END).status == RunStatus.SUCCEEDED
    assert (
        _count(
            sqlite_session,
            Notification,
            Notification.notification_type == "RECOMMENDATION_CLOSED",
            Notification.recommendation_id == rec.id,
        )
        == 1
    )


def test_closed_recommendations_are_never_reopened(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    recs = sqlite_session.scalars(select(Recommendation)).all()
    versions_before = {rec.id: rec.latest_version for rec in recs}
    events_before = _count(sqlite_session, RecommendationEvent)
    for rec in recs:
        rec.state = RecommendationState.CLOSED
        rec.closed_at = rec.last_as_of
    sqlite_session.commit()

    msvc.track_recommendations(INDEX, END)
    for rec in sqlite_session.scalars(select(Recommendation)).all():
        assert rec.state is RecommendationState.CLOSED
        assert rec.latest_version == versions_before[rec.id]
    assert _count(sqlite_session, RecommendationEvent) == events_before
    assert store.get_tracked_recommendations(sqlite_session) == []


def test_tracking_without_metrics_records_a_gap_and_changes_nothing(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    recs = store.get_tracked_recommendations(sqlite_session)
    before = {rec.id: (rec.state, rec.latest_version) for rec in recs}
    result = msvc.track_recommendations(INDEX, date(2030, 1, 1))
    assert result.status == RunStatus.SUCCEEDED
    assert result.items_processed == 0
    # The gap is surfaced per recommendation, not silently dropped.
    assert result.items_failed == len(recs)
    gaps = sqlite_session.scalars(
        select(IngestionError).where(IngestionError.code == "NO_DATA_FOR_DAY")
    ).all()
    assert len(gaps) == len(recs)
    assert all(g.severity == Severity.INFO for g in gaps)
    for rec in sqlite_session.scalars(select(Recommendation)).all():
        assert (rec.state, rec.latest_version) == before[rec.id]


def test_eod_is_idempotent_for_all_phase_5_tables(
    sqlite_session, pointed_at_long_fixtures
):
    msvc = _pipeline(sqlite_session)
    counts = {
        model: _count(sqlite_session, model)
        for model in (Recommendation, RecommendationVersion, ThesisSnapshot,
                      RecommendationEvent, Notification)
    }
    rerun = msvc.compute_eod(INDEX, START, END)
    assert all(r.status == RunStatus.SUCCEEDED for r in rerun)
    for model, count in counts.items():
        assert _count(sqlite_session, model) == count, model


def test_tracking_job_is_audited_like_every_other_job(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    for job in ("track_recommendations", "dispatch_notifications"):
        runs = sqlite_session.scalars(
            select(IngestionRun).where(IngestionRun.job_name == job)
        ).all()
        assert runs
        assert all(r.status is RunStatus.SUCCEEDED for r in runs)
        assert all(r.finished_at is not None for r in runs)
        assert all(r.error_summary is None for r in runs)
        assert all(r.config.get("index_codes") == INDEX for r in runs)


def test_signals_and_recommendations_stay_linked(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    for rec in store.get_tracked_recommendations(sqlite_session):
        signal = sqlite_session.scalar(
            select(Signal).where(
                Signal.instrument_id == rec.instrument_id,
                Signal.as_of == rec.first_as_of,
            )
        )
        assert signal is not None
        instrument = sqlite_session.get(Instrument, rec.instrument_id)
        assert instrument is not None
        assert rec.strategy_code and rec.horizon
        assert rec.entry_low <= rec.entry_high
        assert rec.invalidation_price < rec.entry_low
        assert rec.target_low > rec.entry_high


def test_technical_metrics_are_available_to_the_exit_engine(
    sqlite_session, pointed_at_long_fixtures
):
    _pipeline(sqlite_session)
    assert _count(sqlite_session, TechnicalIndicator) > 0
    rows = sqlite_session.scalars(
        select(TechnicalIndicator).order_by(TechnicalIndicator.as_of)
    ).all()
    assert any(r.drawdown_pct is not None for r in rows)
    assert any(r.trend_state for r in rows)
    store.get_corporate_actions(sqlite_session, 1)  # callable with no window
    assert store.get_corporate_actions(sqlite_session, 1, START, END) is not None
