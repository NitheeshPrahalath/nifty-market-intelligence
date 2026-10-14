"""Service: persistence, idempotency, and the fallback path."""

from __future__ import annotations

from sqlalchemy import func, select

from nmi.core.models import Explanation as ExplanationRow
from nmi.explain.provider import ProviderError, ProviderResult, TemplateProvider
from nmi.explain.schema import LLMExplanation
from nmi.explain.service import (
    generate_explanation,
    generate_explanations,
    get_latest_explanation,
    select_recommendations,
)


class _ExplodingProvider:
    name = "openai"
    model = "boom"

    def generate(self, factsheet, kind):
        raise ProviderError("upstream is down")


class _LyingProvider:
    """Cites a real fact but states a number that is not in it."""

    name = "openai"
    model = "liar"

    def generate(self, factsheet, kind):
        return ProviderResult(
            draft=LLMExplanation(
                headline=f"{factsheet.symbol} will reach 9999",
                summary="The target is 9999.",
                citations=["target_high"],
            ),
            provider=self.name,
            model=self.model,
            raw="{}",
        )


def _row_count(session) -> int:
    return session.scalar(select(func.count()).select_from(ExplanationRow))


def test_generate_persists_a_valid_explanation(recommendation, sqlite_session):
    explanation = generate_explanation(sqlite_session, recommendation.id, "thesis")
    assert explanation is not None
    assert explanation.valid is True
    assert explanation.provider == "template"
    assert explanation.citations
    assert _row_count(sqlite_session) == 1


def test_regenerating_the_same_facts_does_not_duplicate(recommendation, sqlite_session):
    first = generate_explanation(sqlite_session, recommendation.id, "thesis")
    second = generate_explanation(sqlite_session, recommendation.id, "thesis")
    assert first is not None and second is not None
    assert first.source_hash == second.source_hash
    assert _row_count(sqlite_session) == 1


def test_force_replaces_in_place(recommendation, sqlite_session):
    generate_explanation(sqlite_session, recommendation.id, "thesis")
    again = generate_explanation(sqlite_session, recommendation.id, "thesis", force=True)
    assert again is not None
    assert _row_count(sqlite_session) == 1


def test_a_provider_outage_falls_back_to_the_template(recommendation, sqlite_session):
    explanation = generate_explanation(
        sqlite_session, recommendation.id, "thesis", provider=_ExplodingProvider()
    )
    assert explanation is not None
    assert explanation.valid is True
    assert explanation.provider == "template"
    assert explanation.fallback_reason and "failed" in explanation.fallback_reason


def test_an_invented_number_is_rejected_in_favour_of_the_template(
    recommendation, sqlite_session
):
    explanation = generate_explanation(
        sqlite_session, recommendation.id, "thesis", provider=_LyingProvider()
    )
    assert explanation is not None
    assert explanation.valid is True
    assert explanation.provider == "template"
    assert "citation check failed" in (explanation.fallback_reason or "")
    assert "9999" not in explanation.summary


def test_latest_explanation_round_trips(recommendation, sqlite_session):
    generate_explanation(sqlite_session, recommendation.id, "thesis")
    fetched = get_latest_explanation(sqlite_session, recommendation.id)
    assert fetched is not None
    assert fetched.recommendation_id == recommendation.id
    assert fetched.kind == "thesis"
    assert get_latest_explanation(sqlite_session, 999999) is None


def test_batch_generation_and_selection(recommendation, sqlite_session):
    selected = select_recommendations(sqlite_session)
    assert [rec.id for rec in selected] == [recommendation.id]

    results = generate_explanations(
        sqlite_session, selected, "change", provider=TemplateProvider()
    )
    assert len(results) == 1
    _rec, explanation = results[0]
    assert explanation is not None
    assert explanation.kind == "change"
