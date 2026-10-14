"""The explanation endpoint over a fully computed pipeline database."""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from nmi.explain.provider import TemplateProvider
from nmi.explain.service import (
    generate_explanations,
    select_recommendations,
)


def _write_explanations(engine, kind: str = "thesis") -> int:
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        recommendations = select_recommendations(session, include_closed=True)
        assert recommendations, "the pipeline should have produced recommendations"
        generate_explanations(
            session, recommendations, kind, provider=TemplateProvider()
        )
        return recommendations[0].id
    finally:
        session.close()


def test_recommendation_explanation_round_trips(seeded_engine, make_client):
    recommendation_id = _write_explanations(seeded_engine)
    client = make_client()

    response = client.get(f"/api/v1/recommendations/{recommendation_id}/explanation")
    assert response.status_code == 200
    body = response.json()
    assert body["recommendation_id"] == recommendation_id
    assert body["kind"] == "thesis"
    assert body["headline"]
    assert body["summary"]
    assert body["valid"] is True
    assert body["citations"]
    assert body["provider"] == "template"
    # Every bullet carries the facts it was written from.
    assert any(bullet["citations"] for bullet in body["bullets"])


def test_missing_explanation_is_404(seeded_engine, make_client):
    client = make_client()
    assert (
        client.get("/api/v1/recommendations/999999/explanation").status_code == 404
    )
