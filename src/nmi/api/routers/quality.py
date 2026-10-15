"""Data-quality reconciliation report (Phase 10).

Exposes the most recent :class:`~nmi.core.models.DataQualityRun` and its issues
so operators can see the freshness and integrity of the stored market data
without shelling into the database. Reconciliation itself is a job
(``nmi quality-report``), not an API call.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from nmi.api.deps import get_db
from nmi.api.schemas import QualityIssueView, QualityReport, QualityRunView
from nmi.core.enums import Severity
from nmi.quality import issues_for_run, latest_run

router = APIRouter(tags=["quality"])


@router.get("/quality", response_model=QualityReport)
def quality_report(db: Annotated[Session, Depends(get_db)] = None) -> QualityReport:  # type: ignore[assignment]
    """Latest data-quality reconciliation, or ``available: false`` if none ran."""
    run = latest_run(db)
    if run is None:
        return QualityReport(available=False)
    issues = issues_for_run(db, run.id)
    error_count = sum(1 for i in issues if i.severity == Severity.ERROR)
    warning_count = sum(1 for i in issues if i.severity == Severity.WARNING)
    return QualityReport(
        available=True,
        run=QualityRunView(
            run_id=run.id,
            job_name=run.job_name,
            status=run.status.value,
            as_of=run.as_of,
            started_at=run.started_at,
            finished_at=run.finished_at,
            checks_run=run.checks_run,
            checks_failed=run.checks_failed,
            summary=run.summary,
            config=run.config,
            issue_count=len(issues),
            error_count=error_count,
            warning_count=warning_count,
            issues=[
                QualityIssueView(
                    id=i.id,
                    check_code=i.check_code,
                    severity=i.severity.value,
                    scope=i.scope,
                    message=i.message,
                    details=i.details,
                )
                for i in issues
            ],
        ),
    )
