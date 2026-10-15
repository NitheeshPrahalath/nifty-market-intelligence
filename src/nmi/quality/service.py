"""Data-quality reconciliation service (Phase 10).

Runs the :mod:`nmi.quality.checks` suite against stored market data and records
the outcome as a :class:`~nmi.core.models.DataQualityRun` plus its
:class:`~nmi.core.models.DataQualityIssue` children, mirroring the ingestion
runs convention. ``latest_report`` reads the most recent run back for the API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nmi.core.config import settings
from nmi.core.enums import IndexCode, RunStatus, Severity
from nmi.core.models import DailyPrice, DataQualityIssue, DataQualityRun, Index
from nmi.ingestion.backfill import resolve_members
from nmi.quality.checks import CHECKS, CheckContext, QualityIssue, run_checks

JOB_NAME = "quality_reconcile"
MAX_ISSUES = 500


@dataclass(slots=True)
class QualityResult:
    run_id: int
    job_name: str
    status: RunStatus
    as_of: date | None
    checks_run: int
    checks_failed: int
    issues: list[QualityIssue] = field(default_factory=list)
    truncated: int = 0

    @property
    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "status": self.status.value,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "checks_run": self.checks_run,
            "checks_failed": self.checks_failed,
            "issue_count": len(self.issues),
            "truncated": self.truncated,
            "issues": [i.as_dict() for i in self.issues],
        }


class QualityService:
    """Reconciles stored prices for one or more indices."""

    def __init__(self, session: Session):
        self.session = session

    def reconcile(
        self,
        index_codes: Sequence[str] | None = None,
        *,
        as_of: date | None = None,
        lookback_days: int | None = None,
        stale_business_days: int | None = None,
        adjustment_tolerance_pct: float | None = None,
    ) -> QualityResult:
        lookback = (
            lookback_days if lookback_days is not None else settings.quality_lookback_days
        )
        stale = (
            stale_business_days
            if stale_business_days is not None
            else settings.quality_stale_business_days
        )
        tolerance = (
            adjustment_tolerance_pct
            if adjustment_tolerance_pct is not None
            else settings.quality_adjustment_tolerance_pct
        )
        resolved_as_of = as_of or self.session.scalar(
            select(func.max(DailyPrice.trade_date))
        )
        config = {
            "index_codes": list(index_codes) if index_codes else None,
            "lookback_days": lookback,
            "stale_business_days": stale,
            "adjustment_tolerance_pct": tolerance,
        }
        run = DataQualityRun(
            job_name=JOB_NAME,
            status=RunStatus.RUNNING,
            started_at=datetime.now(UTC),
            as_of=resolved_as_of,
            config=config,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        try:
            issues, indices_run = self._collect(
                index_codes, resolved_as_of, lookback, stale, tolerance
            )
            truncated = 0
            if len(issues) > MAX_ISSUES:
                truncated = len(issues) - MAX_ISSUES
                issues = issues[:MAX_ISSUES]
            checks_failed = len(
                {i.check_code for i in issues if i.severity == Severity.ERROR}
            )
            checks_run = len(CHECKS) * indices_run
            for issue in issues:
                self.session.add(
                    DataQualityIssue(
                        run_id=run.id,
                        check_code=issue.check_code,
                        severity=issue.severity,
                        scope=issue.scope,
                        message=issue.message,
                        details=issue.details or None,
                    )
                )
            errors = sum(1 for i in issues if i.severity == Severity.ERROR)
            warnings = sum(1 for i in issues if i.severity == Severity.WARNING)
            run.status = RunStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            run.checks_run = checks_run
            run.checks_failed = checks_failed
            run.summary = (
                f"{len(issues)} issue(s) across {indices_run} index(es): "
                f"{errors} error(s), {warnings} warning(s)"
            )
            self.session.commit()
            return QualityResult(
                run_id=run.id,
                job_name=JOB_NAME,
                status=RunStatus.SUCCEEDED,
                as_of=resolved_as_of,
                checks_run=checks_run,
                checks_failed=checks_failed,
                issues=issues,
                truncated=truncated,
            )
        except Exception as exc:  # noqa: BLE001 - record then re-raise
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.summary = f"{type(exc).__name__}: {exc}"
            self.session.commit()
            raise

    def _collect(
        self,
        index_codes: Sequence[str] | None,
        as_of: date | None,
        lookback: int,
        stale: int,
        tolerance: float,
    ) -> tuple[list[QualityIssue], int]:
        if as_of is None:
            return (
                [
                    QualityIssue(
                        check_code="NO_PRICE_DATA",
                        severity=Severity.ERROR,
                        scope=None,
                        message="no daily prices stored; nothing to reconcile",
                    )
                ],
                0,
            )
        start = as_of - timedelta(days=lookback)
        codes = list(index_codes) if index_codes else list(
            self.session.scalars(select(Index.code).order_by(Index.code)).all()
        )
        issues: list[QualityIssue] = []
        indices_run = 0
        for code in codes:
            index = self.session.scalar(select(Index).where(Index.code == code))
            if index is None:
                issues.append(
                    QualityIssue(
                        check_code="INDEX_UNKNOWN",
                        severity=Severity.ERROR,
                        scope=code,
                        message=f"index {code} is not registered",
                    )
                )
                continue
            members = resolve_members(self.session, [code], start, as_of)
            ctx = CheckContext(
                session=self.session,
                index_code=code,
                index_id=index.id,
                members=members,
                start=start,
                end=as_of,
                as_of=as_of,
                stale_business_days=stale,
                adjustment_tolerance_pct=tolerance,
            )
            issues.extend(run_checks(ctx))
            indices_run += 1
        return issues, indices_run


def latest_run(session: Session) -> DataQualityRun | None:
    """The most recent reconciliation run, newest first."""
    return session.scalar(
        select(DataQualityRun)
        .order_by(DataQualityRun.started_at.desc(), DataQualityRun.id.desc())
        .limit(1)
    )


def issues_for_run(session: Session, run_id: int) -> list[DataQualityIssue]:
    return list(
        session.scalars(
            select(DataQualityIssue)
            .where(DataQualityIssue.run_id == run_id)
            .order_by(DataQualityIssue.severity.desc(), DataQualityIssue.check_code)
        ).all()
    )


def default_index_codes() -> list[str]:
    """Fallback index list when none is supplied to the CLI."""
    return [IndexCode.NIFTY_50.value]
