"""Gateway behaviour that is independent of the data in the database."""

from __future__ import annotations


def test_health_reports_database_round_trip(make_client):
    client = make_client()
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_root_advertises_docs_and_prefix(make_client):
    client = make_client()
    body = client.get("/").json()
    assert body["docs"] == "/docs"
    assert body["api"] == "/api/v1"


def test_request_id_header_is_present(make_client):
    client = make_client()
    response = client.get("/health")
    assert response.headers.get("x-request-id")


def test_openapi_document_lists_the_routes(make_client):
    client = make_client()
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/api/v1/screener" in paths
    assert "/api/v1/stocks/{symbol}" in paths
    assert "/api/v1/recommendations/{recommendation_id}" in paths


def test_auth_disabled_allows_anonymous(make_client):
    client = make_client(auth=False)
    response = client.get("/api/v1/screener")
    assert response.status_code == 200


def test_auth_enabled_rejects_missing_and_wrong_keys(make_client):
    client = make_client(auth=True, keys=("s3cr3t",))
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"X-API-Key": "nope"}).status_code == 401


def test_auth_enabled_accepts_api_key_and_bearer(make_client):
    client = make_client(auth=True, keys=("s3cr3t",))
    assert client.get("/health", headers={"X-API-Key": "s3cr3t"}).status_code == 200
    assert client.get("/health", headers={"Authorization": "Bearer s3cr3t"}).status_code == 200


def test_rate_limit_returns_429_with_retry_after(make_client):
    client = make_client(limit=2)
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 200
    blocked = client.get("/health")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_rate_limit_headers_are_reported(make_client):
    client = make_client(limit=5)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-ratelimit-remaining"] == "4"


def test_cache_reports_miss_then_hit(make_client):
    client = make_client(ttl=60)
    first = client.get("/api/v1/screener")
    second = client.get("/api/v1/screener")
    assert first.status_code == second.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert first.json() == second.json()


def test_health_is_never_cached(make_client):
    client = make_client(ttl=60)
    assert client.get("/health").headers["x-cache"] == "MISS"
    assert client.get("/health").headers["x-cache"] == "MISS"


def test_cache_disabled_when_ttl_is_zero(make_client):
    client = make_client(ttl=0)
    assert client.get("/api/v1/screener").headers["x-cache"] == "MISS"
    assert client.get("/api/v1/screener").headers["x-cache"] == "MISS"


def test_requests_are_written_to_the_audit_log(make_client):
    client = make_client(audit=True)
    client.get("/health")
    response = client.get("/api/v1/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(row["path"] == "/health" for row in body["items"])


def test_audit_can_be_disabled(make_client):
    client = make_client(audit=False)
    client.get("/health")
    body = client.get("/api/v1/audit").json()
    assert body["total"] == 0


def test_unknown_symbol_and_index_return_404(make_client):
    client = make_client()
    assert client.get("/api/v1/stocks/ZZZ").status_code == 404
    assert client.get("/api/v1/market/overview?index=NOPE").status_code == 404
    assert client.get("/api/v1/recommendations/999999").status_code == 404
    assert client.get("/api/v1/backtests/999999").status_code == 404
