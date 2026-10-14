"""Endpoints over a fully computed database (one pipeline run, reused)."""

from __future__ import annotations

from datetime import date

END = date(2024, 6, 28)


def test_market_overview(seeded_engine, make_client):
    client = make_client()
    response = client.get("/api/v1/market/overview", params={"index": "NIFTY_50"})
    assert response.status_code == 200
    body = response.json()
    assert body["index"]["code"] == "NIFTY_50"
    assert body["as_of"] == END.isoformat()
    assert body["member_count"] == 3
    assert body["regime"] is not None
    assert len(body["sectors"]) >= 1
    assert any(body["signal_counts"].values())


def test_screener_returns_the_scored_universe(seeded_engine, make_client):
    client = make_client()
    body = client.get("/api/v1/screener").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    symbols = {row["symbol"] for row in body["items"]}
    assert symbols == {"RELIANCE", "TCS", "INFY"}
    for row in body["items"]:
        assert row["score_label"]
        assert row["preferred_horizon"]
        assert row["scores"]["trend"] is not None


def test_screener_paginates_and_sorts(seeded_engine, make_client):
    client = make_client()
    first = client.get("/api/v1/screener", params={"page": 1, "page_size": 1}).json()
    second = client.get("/api/v1/screener", params={"page": 2, "page_size": 1}).json()
    assert first["pages"] == 3
    assert len(first["items"]) == len(second["items"]) == 1
    # Default sort is composite descending, so page 1 leads page 2.
    assert first["items"][0]["composite_score"] >= second["items"][0]["composite_score"]

    ascending = client.get("/api/v1/screener", params={"sort": "symbol", "order": "asc"}).json()
    assert [row["symbol"] for row in ascending["items"]] == ["INFY", "RELIANCE", "TCS"]


def test_screener_filters_by_search_term(seeded_engine, make_client):
    client = make_client()
    body = client.get("/api/v1/screener", params={"q": "tcs"}).json()
    assert body["total"] == 1
    assert body["items"][0]["symbol"] == "TCS"


def test_stock_detail(seeded_engine, make_client):
    client = make_client()
    response = client.get("/api/v1/stocks/TCS")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "TCS"
    assert body["company"]["isin"]
    assert body["technical"] is not None
    assert body["momentum"] is not None
    assert body["scoring"] is not None
    assert body["latest_price"] is not None
    assert {ref["code"] for ref in body["indices"]} == {"NIFTY_50"}


def test_stock_price_history_paginates(seeded_engine, make_client):
    client = make_client()
    body = client.get("/api/v1/stocks/TCS/prices", params={"page_size": 5}).json()
    assert body["total"] > 5
    assert len(body["items"]) == 5
    dates = [row["trade_date"] for row in body["items"]]
    assert dates == sorted(dates, reverse=True)


def test_recommendations_and_alerts_are_listable(seeded_engine, make_client):
    client = make_client()
    recs = client.get("/api/v1/recommendations").json()
    assert recs["total"] == len(recs["items"])
    alerts = client.get("/api/v1/alerts").json()
    assert alerts["total"] == len(alerts["items"])


def test_backtests_list_is_empty_without_runs(seeded_engine, make_client):
    client = make_client()
    body = client.get("/api/v1/backtests").json()
    assert body["total"] == 0
    assert body["items"] == []
