"""Build the fact sheet an explanation is written from.

Everything here is read straight from the database for one recommendation: the
tracked object, its latest version, its thesis snapshots (baseline and current)
and its lifecycle events. Each value becomes a named :class:`Fact` with a
rendered ``text`` and the ``source`` column it came from, so any sentence in the
prose can point back at a single, checkable number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from nmi.analysis.common import as_float
from nmi.core.models import Company, Instrument, Recommendation
from nmi.explain.schema import Fact, FactSheet
from nmi.ingestion import store
from nmi.tracking.thesis import FACTORS_BY_KEY, ThesisSnapshot, compare

#: A factor must move by this much to appear as a "change" fact (matches the
#: snapper's own tolerance so the note agrees with the alerts).
DEFAULT_DIGITS = 2


def _clean(value) -> object:
    """A JSON-safe, hashable rendering of a raw column value."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value"):  # StrEnum and friends
        return str(value.value)
    return value


def _num(value, digits: int = DEFAULT_DIGITS) -> str:
    number = as_float(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}"


def _text(value) -> str:
    if value is None:
        return "n/a"
    return str(getattr(value, "value", value))


class _Builder:
    """Small helper that keeps fact construction terse and collision-free."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []

    def add(
        self,
        key: str,
        label: str,
        value,
        *,
        text: str | None = None,
        group: str = "score",
        source: str = "",
        digits: int = DEFAULT_DIGITS,
    ) -> None:
        cleaned = _clean(value)
        self.facts.append(
            Fact(
                key=key,
                label=label,
                value=cleaned,
                text=_num(value, digits) if text is None else text,
                group=group,
                source=source,
            )
        )

    def add_text(
        self, key: str, label: str, value: str | None, *, group: str = "context",
        source: str = "",
    ) -> None:
        text = value if value not in (None, "") else "n/a"
        self.facts.append(
            Fact(key=key, label=label, value=text, text=text, group=group, source=source)
        )


def build_fact_sheet(
    session: Session, recommendation_id: int, kind: str = "thesis"
) -> FactSheet | None:
    """Return every fact about one recommendation, or ``None`` if unknown."""
    rec = session.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    instrument = session.get(Instrument, rec.instrument_id)
    company = session.get(Company, instrument.company_id) if instrument else None
    symbol = instrument.symbol if instrument else f"#{rec.instrument_id}"

    builder = _Builder()
    builder.add_text("symbol", "Symbol", symbol, group="context", source="instruments.symbol")
    builder.add_text(
        "company", "Company", company.name if company else None,
        group="context", source="companies.name",
    )
    builder.add_text(
        "as_of", "As of", rec.last_as_of.isoformat(),
        group="context", source="recommendations.last_as_of",
    )
    builder.add_text(
        "state", "State", _text(rec.state), group="context", source="recommendations.state"
    )
    builder.add_text(
        "strategy", "Strategy", rec.strategy_code,
        group="context", source="recommendations.strategy_code",
    )
    builder.add_text("horizon", "Horizon", _text(rec.horizon), group="context")
    builder.add_text("risk_level", "Risk level", _text(rec.risk_level), group="context")
    builder.add_text(
        "created_reason", "Created because", rec.created_reason,
        group="context", source="recommendations.created_reason",
    )

    builder.add("confidence", "Confidence", rec.confidence, source="recommendations.confidence")
    builder.add("current_price", "Current price", rec.current_price, group="price")
    builder.add("entry_low", "Entry low", rec.entry_low, group="price")
    builder.add("entry_high", "Entry high", rec.entry_high, group="price")
    builder.add("target_low", "Target low", rec.target_low, group="price")
    builder.add("target_high", "Target high", rec.target_high, group="price")
    builder.add(
        "invalidation_price", "Invalidation price", rec.invalidation_price, group="price"
    )
    builder.add(
        "holding_days_min", "Minimum holding days", rec.expected_holding_days_min,
        group="context", digits=0,
    )
    builder.add(
        "holding_days_max", "Maximum holding days", rec.expected_holding_days_max,
        group="context", digits=0,
    )
    builder.add(
        "first_as_of", "First recommended on", rec.first_as_of,
        group="context", source="recommendations.first_as_of",
    )

    _add_version(builder, session, rec.id)
    _add_thesis(builder, session, rec.id)
    _add_events(builder, session, rec.id)

    return FactSheet(
        recommendation_id=rec.id,
        symbol=symbol,
        kind=kind,
        as_of=rec.last_as_of,
        facts=builder.facts,
    )


def _add_version(builder: _Builder, session: Session, recommendation_id: int) -> None:
    version = store.latest_recommendation_version(session, recommendation_id)
    if version is None:
        return
    builder.add(
        "version.number", "Latest version", version.version,
        group="context", source="recommendation_versions.version", digits=0,
    )
    builder.add_text(
        "version.as_of", "Latest version as of", version.as_of.isoformat(), group="context"
    )
    builder.add_text(
        "version.signal_type", "Signal", _text(version.signal_type),
        group="context", source="recommendation_versions.signal_type",
    )
    builder.add(
        "version.composite_score", "Composite score", version.composite_score,
        group="score", source="recommendation_versions.composite_score",
    )
    builder.add(
        "version.confidence", "Version confidence", version.confidence,
        group="score", source="recommendation_versions.confidence",
    )


def _add_thesis(builder: _Builder, session: Session, recommendation_id: int) -> None:
    snapshots = store.get_thesis_snapshots(session, recommendation_id)
    if not snapshots:
        return
    baseline = snapshots[0]
    current = snapshots[-1]

    builder.add(
        "thesis.as_of", "Thesis as of", current.as_of,
        group="context", source="thesis_snapshots.as_of",
    )
    builder.add(
        "thesis.composite_score", "Composite score", current.composite_score,
        group="score", source="thesis_snapshots.composite_score",
    )
    builder.add(
        "thesis.confidence", "Confidence", current.confidence,
        group="score", source="thesis_snapshots.confidence",
    )
    builder.add(
        "thesis.price", "Price", current.price, group="price", source="thesis_snapshots.price"
    )
    builder.add(
        "thesis.weakened_count", "Weakened factors", current.weakened_count,
        group="context", digits=0,
    )
    builder.add(
        "thesis.improved_count", "Improved factors", current.improved_count,
        group="context", digits=0,
    )
    if current.summary:
        builder.add_text("thesis.summary", "Thesis summary", current.summary, group="context")

    for key, payload in (current.factors or {}).items():
        if not isinstance(payload, dict):
            continue
        spec = FACTORS_BY_KEY.get(key)
        builder.facts.append(
            Fact(
                key=f"factor.{key}",
                label=spec.label if spec else key,
                value=_clean(payload.get("value")),
                text=payload.get("text") or _num(payload.get("value")),
                group=spec.group if spec else "score",
                source=f"thesis_snapshots.factors.{key}",
            )
        )
    builder.add(
        "factor_count", "Tracked factors", len(current.factors or {}),
        group="context", digits=0,
    )

    builder.add(
        "baseline.composite_score", "Baseline composite score", baseline.composite_score,
        group="score", digits=DEFAULT_DIGITS,
    )
    builder.add(
        "baseline.price", "Baseline price", baseline.price, group="price"
    )

    assessment = compare(
        ThesisSnapshot.from_stored(baseline.as_of, baseline.factors),
        ThesisSnapshot.from_stored(current.as_of, current.factors),
    )
    for change in assessment.changes:
        builder.facts.append(
            Fact(
                key=f"change.{change.key}",
                label=f"{change.label} change",
                value=change.direction.value,
                text=f"{change.before_text} -> {change.after_text} ({change.direction.value})",
                group=change.group,
                source=f"thesis_snapshots.factors.{change.key}",
            )
        )


def _add_events(builder: _Builder, session: Session, recommendation_id: int) -> None:
    events = store.get_recommendation_events(session, recommendation_id)
    builder.add(
        "events.count", "Lifecycle events", len(events),
        group="context", source="recommendation_events", digits=0,
    )
    if not events:
        return
    latest = events[-1]
    builder.add_text(
        "events.latest_type", "Latest event", _text(latest.event_type),
        group="context", source="recommendation_events.event_type",
    )
    builder.add_text(
        "events.latest_as_of", "Latest event on", latest.as_of.isoformat(), group="context"
    )
    builder.add_text(
        "events.latest_message", "Latest event detail", latest.message,
        group="context", source="recommendation_events.message",
    )
