"""Phase-10 hardening: observability, RBAC, security headers and quality API."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import sessionmaker

from nmi.core.enums import RunStatus, Severity
from nmi.core.metrics import REGISTRY
from nmi.core.models import DataQualityIssue, DataQualityRun


def test_readiness_endpoint_is_ready(make_client):
    client = make_client()
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_security_headers_are_present(make_client):
    client = make_client()
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_root_advertises_operational_surfaces(make_client):
    client = make_client()
    body = client.get("/").json()
    assert body["ready"] == "/health/ready"
    assert body["metrics"] == "/metrics"
    assert body["quality"] == "/api/v1/quality"


def test_metrics_endpoint_exposes_counters(make_client):
    REGISTRY.reset()
    client = make_client()
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text


def test_metrics_endpoint_is_not_authenticated(make_client):
    client = make_client(auth=True, keys=("s3cr3t",))
    assert client.get("/metrics").status_code == 200


def test_viewer_cannot_read_the_audit_log_but_analyst_can(make_client):
    client = make_client(
        auth=True,
        roles={"viewer-key": "viewer", "analyst-key": "analyst"},
    )
    viewer = client.get("/api/v1/audit", headers={"X-API-Key": "viewer-key"})
    assert viewer.status_code == 403
    assert "scope" in viewer.json()["detail"]

    analyst = client.get("/api/v1/audit", headers={"X-API-Key": "analyst-key"})
    assert analyst.status_code == 200


def test_viewer_can_read_data_endpoints(make_client):
    client = make_client(auth=True, roles={"viewer-key": "viewer"})
    assert client.get("/api/v1/screener", headers={"X-API-Key": "viewer-key"}).status_code == 200


def test_unknown_role_fails_closed_to_viewer(make_client):
    client = make_client(auth=True, roles={"odd-key": "superuser"})
    response = client.get("/api/v1/audit", headers={"X-API-Key": "odd-key"})
    assert response.status_code == 403


def test_audit_row_records_role_and_request_id(make_client, api_engine):
    client = make_client(auth=True, roles={"analyst-key": "analyst"})
    response = client.get("/api/v1/screener", headers={"X-API-Key": "analyst-key"})
    request_id = response.headers["X-Request-Id"]

    audit = client.get(
        "/api/v1/audit",
        headers={"X-API-Key": "analyst-key"},
        params={"request_id": request_id},
    ).json()
    assert audit["total"] == 1
    entry = audit["items"][0]
    assert entry["role"] == "analyst"
    assert entry["request_id"] == request_id


def test_quality_unavailable_when_no_run(make_client):
    client = make_client()
    body = client.get("/api/v1/quality").json()
    assert body == {"available": False, "run": None}


def test_quality_report_returns_latest_run(make_client, api_engine):
    Session = sessionmaker(bind=api_engine, expire_on_commit=False)
    session = Session()
    try:
        run = DataQualityRun(
            job_name="quality_reconcile",
            status=RunStatus.SUCCEEDED,
            started_at=datetime(2024, 6, 28, 12, 0, tzinfo=UTC),
            finished_at=datetime(2024, 6, 28, 12, 1, tzinfo=UTC),
            as_of=date(2024, 6, 28),
            checks_run=7,
            checks_failed=1,
            summary="1 issue",
            config={},
        )
        session.add(run)
        session.flush()
        session.add(
            DataQualityIssue(
                run_id=run.id,
                check_code="PRICE_MISSING",
                severity=Severity.ERROR,
                scope="BBB",
                message="BBB has no prices",
                details={"index": "NIFTY_50"},
            )
        )
        session.commit()
    finally:
        session.close()

    client = make_client()
    body = client.get("/api/v1/quality").json()
    assert body["available"] is True
    run_body = body["run"]
    assert run_body["checks_run"] == 7
    assert run_body["error_count"] == 1
    assert run_body["issues"][0]["check_code"] == "PRICE_MISSING"
