"""Reconciliation checks over *stored* market data (Phase 10).

Unlike :mod:`nmi.ingestion.validation`, which vets a provider batch on its way
into the database, these checks run against what is already persisted. They
exist to catch problems that only become visible once data lands: a member whose
prices never arrived, a stale series, a split whose adjusted close drifted from
the raw close, or an index benchmark that stopped updating.

Every check is a pure function of a :class:`CheckContext` and returns a list of
:class:`QualityIssue`. The service layer persists them; nothing here touches the
session's transaction state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nmi.core.enums import Severity
from nmi.core.models import DailyPrice, IndexPrice, Instrument


@dataclass(slots=True)
class QualityIssue:
    """A single reconciliation finding."""

    check_code: str
    severity: Severity
    scope: str | None
    message: str
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "check_code": self.check_code,
            "severity": self.severity.value,
            "scope": self.scope,
            "message": self.message,
            "details": self.details,
        }


@dataclass(slots=True)
class CheckContext:
    """Everything a check needs to reconcile one index over one window."""

    session: Session
    index_code: str
    index_id: int | None
    members: list[Instrument]
    start: date
    end: date
    as_of: date
    stale_business_days: int = 4
    adjustment_tolerance_pct: float = 0.5


def _business_days(a: date, b: date):
    """Inclusive business days (Mon-Fri) between ``a`` and ``b``."""
    day = a
    while day <= b:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _subtract_business_days(anchor: date, count: int) -> date:
    day = anchor
    remaining = count
    while remaining > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(value)


def check_price_presence(ctx: CheckContext) -> list[QualityIssue]:
    """Every current member must have at least one price in the window."""
    if not ctx.members:
        return []
    ids = [m.id for m in ctx.members]
    present = set(
        ctx.session.scalars(
            select(DailyPrice.instrument_id)
            .where(
                DailyPrice.instrument_id.in_(ids),
                DailyPrice.trade_date.between(ctx.start, ctx.end),
            )
            .distinct()
        ).all()
    )
    issues: list[QualityIssue] = []
    for member in ctx.members:
        if member.id not in present:
            issues.append(
                QualityIssue(
                    check_code="PRICE_MISSING",
                    severity=Severity.ERROR,
                    scope=member.symbol,
                    message=(
                        f"{member.symbol} has no stored prices between "
                        f"{ctx.start} and {ctx.end}"
                    ),
                    details={"index": ctx.index_code, "start": str(ctx.start), "end": str(ctx.end)},
                )
            )
    return issues


def check_staleness(ctx: CheckContext) -> list[QualityIssue]:
    """The most recent price should be close to the reconciliation as-of date."""
    if not ctx.members:
        return []
    ids = [m.id for m in ctx.members]
    latest = dict(
        ctx.session.execute(
            select(DailyPrice.instrument_id, func.max(DailyPrice.trade_date))
            .where(
                DailyPrice.instrument_id.in_(ids),
                DailyPrice.trade_date.between(ctx.start, ctx.end),
            )
            .group_by(DailyPrice.instrument_id)
        ).all()
    )
    cutoff = _subtract_business_days(ctx.end, ctx.stale_business_days)
    issues: list[QualityIssue] = []
    for member in ctx.members:
        last = latest.get(member.id)
        if last is None:
            continue  # presence check owns this
        if last < cutoff:
            issues.append(
                QualityIssue(
                    check_code="PRICE_STALE",
                    severity=Severity.WARNING,
                    scope=member.symbol,
                    message=(
                        f"{member.symbol} last price {last} is older than "
                        f"{cutoff} ({ctx.stale_business_days} business days)"
                    ),
                    details={"last_trade_date": str(last), "cutoff": str(cutoff)},
                )
            )
    return issues


def check_completeness(ctx: CheckContext) -> list[QualityIssue]:
    """Rows explicitly flagged ``is_incomplete`` by ingestion validation."""
    if not ctx.members:
        return []
    ids = [m.id for m in ctx.members]
    counts = dict(
        ctx.session.execute(
            select(DailyPrice.instrument_id, func.count())
            .where(
                DailyPrice.instrument_id.in_(ids),
                DailyPrice.trade_date.between(ctx.start, ctx.end),
                DailyPrice.is_incomplete.is_(True),
            )
            .group_by(DailyPrice.instrument_id)
        ).all()
    )
    symbols = {m.id: m.symbol for m in ctx.members}
    issues: list[QualityIssue] = []
    for instrument_id, count in counts.items():
        symbol = symbols.get(instrument_id, str(instrument_id))
        issues.append(
            QualityIssue(
                check_code="PRICE_INCOMPLETE",
                severity=Severity.WARNING,
                scope=symbol,
                message=f"{symbol} has {count} incomplete price row(s) in the window",
                details={"incomplete_rows": count},
            )
        )
    return issues


def check_gaps(ctx: CheckContext) -> list[QualityIssue]:
    """Runs of missing business days between a member's first and last price."""
    if not ctx.members:
        return []
    issues: list[QualityIssue] = []
    for member in ctx.members:
        dates = ctx.session.scalars(
            select(DailyPrice.trade_date)
            .where(
                DailyPrice.instrument_id == member.id,
                DailyPrice.trade_date.between(ctx.start, ctx.end),
            )
            .order_by(DailyPrice.trade_date)
        ).all()
        if len(dates) < 2:
            continue
        known = set(dates)
        missing = [d for d in _business_days(dates[0], dates[-1]) if d not in known]
        if len(missing) >= ctx.stale_business_days:
            issues.append(
                QualityIssue(
                    check_code="PRICE_GAP",
                    severity=Severity.WARNING,
                    scope=member.symbol,
                    message=(
                        f"{member.symbol} is missing {len(missing)} business day(s) "
                        f"between {dates[0]} and {dates[-1]}"
                    ),
                    details={
                        "missing_count": len(missing),
                        "sample": [str(d) for d in missing[:5]],
                    },
                )
            )
    return issues


def check_adjustments(ctx: CheckContext) -> list[QualityIssue]:
    """``adjusted_close`` should equal ``close * adjustment_factor``."""
    if not ctx.members:
        return []
    ids = [m.id for m in ctx.members]
    rows = ctx.session.execute(
        select(
            DailyPrice.instrument_id,
            DailyPrice.trade_date,
            DailyPrice.close,
            DailyPrice.adjusted_close,
            DailyPrice.adjustment_factor,
        ).where(
            DailyPrice.instrument_id.in_(ids),
            DailyPrice.trade_date.between(ctx.start, ctx.end),
            DailyPrice.adjustment_factor.isnot(None),
            DailyPrice.adjusted_close.isnot(None),
        )
    ).all()
    symbols = {m.id: m.symbol for m in ctx.members}
    mismatches: dict[int, list[dict]] = {}
    tolerance = Decimal(str(ctx.adjustment_tolerance_pct))
    for instrument_id, trade_date, close, adjusted, factor in rows:
        raw = _decimal(close)
        adj = _decimal(adjusted)
        fac = _decimal(factor)
        if raw is None or adj is None or fac is None or raw == 0:
            continue
        expected = raw * fac
        if expected == 0:
            continue
        drift_pct = abs(adj - expected) / abs(expected) * Decimal(100)
        if drift_pct > tolerance:
            mismatches.setdefault(instrument_id, []).append(
                {
                    "trade_date": str(trade_date),
                    "close": str(raw),
                    "adjusted_close": str(adj),
                    "adjustment_factor": str(fac),
                    "drift_pct": round(float(drift_pct), 4),
                }
            )
    issues: list[QualityIssue] = []
    for instrument_id, samples in mismatches.items():
        symbol = symbols.get(instrument_id, str(instrument_id))
        issues.append(
            QualityIssue(
                check_code="ADJUSTMENT_MISMATCH",
                severity=Severity.ERROR,
                scope=symbol,
                message=(
                    f"{symbol} adjusted close drifts from close x factor on "
                    f"{len(samples)} row(s)"
                ),
                details={"count": len(samples), "sample": samples[:5]},
            )
        )
    return issues


def check_ohlc(ctx: CheckContext) -> list[QualityIssue]:
    """OHLC bars must be internally consistent."""
    if not ctx.members:
        return []
    ids = [m.id for m in ctx.members]
    rows = ctx.session.execute(
        select(
            DailyPrice.instrument_id,
            DailyPrice.trade_date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
        ).where(
            DailyPrice.instrument_id.in_(ids),
            DailyPrice.trade_date.between(ctx.start, ctx.end),
        )
    ).all()
    symbols = {m.id: m.symbol for m in ctx.members}
    bad: dict[int, list[str]] = {}
    for instrument_id, trade_date, op, high, low, close in rows:
        o, h, low_, c = _decimal(op), _decimal(high), _decimal(low), _decimal(close)
        if None in (o, h, low_, c):
            continue
        if h < max(o, c) or low_ > min(o, c) or low_ > h:
            bad.setdefault(instrument_id, []).append(str(trade_date))
    issues: list[QualityIssue] = []
    for instrument_id, dates in bad.items():
        symbol = symbols.get(instrument_id, str(instrument_id))
        issues.append(
            QualityIssue(
                check_code="OHLC_INCONSISTENT",
                severity=Severity.ERROR,
                scope=symbol,
                message=f"{symbol} has {len(dates)} inconsistent OHLC bar(s)",
                details={"count": len(dates), "sample": dates[:5]},
            )
        )
    return issues


def check_index_coverage(ctx: CheckContext) -> list[QualityIssue]:
    """The benchmark index must have a fresh series for the window."""
    if ctx.index_id is None:
        return []
    rows = ctx.session.scalars(
        select(IndexPrice.trade_date)
        .where(
            IndexPrice.index_id == ctx.index_id,
            IndexPrice.trade_date.between(ctx.start, ctx.end),
        )
        .order_by(IndexPrice.trade_date)
    ).all()
    if not rows:
        return [
            QualityIssue(
                check_code="INDEX_PRICE_MISSING",
                severity=Severity.ERROR,
                scope=ctx.index_code,
                message=f"{ctx.index_code} has no index prices in the window",
                details={"start": str(ctx.start), "end": str(ctx.end)},
            )
        ]
    cutoff = _subtract_business_days(ctx.end, ctx.stale_business_days)
    if rows[-1] < cutoff:
        return [
            QualityIssue(
                check_code="INDEX_PRICE_STALE",
                severity=Severity.WARNING,
                scope=ctx.index_code,
                message=(
                    f"{ctx.index_code} last index price {rows[-1]} is older than {cutoff}"
                ),
                details={"last_trade_date": str(rows[-1]), "cutoff": str(cutoff)},
            )
        ]
    return []


CHECKS: list[Callable[[CheckContext], list[QualityIssue]]] = [
    check_price_presence,
    check_staleness,
    check_completeness,
    check_gaps,
    check_adjustments,
    check_ohlc,
    check_index_coverage,
]
"""The ordered set of checks every reconciliation run executes."""


def run_checks(ctx: CheckContext) -> list[QualityIssue]:
    """Execute every registered check and return the combined findings."""
    issues: list[QualityIssue] = []
    for check in CHECKS:
        issues.extend(check(ctx))
    return issues
