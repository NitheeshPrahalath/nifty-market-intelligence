"""The citation checker is the core safety property of Phase 9."""

from __future__ import annotations

from nmi.explain.citations import extract_numbers, numbers_in_texts, validate
from nmi.explain.schema import Fact, LLMBullet, LLMExplanation


def _fact(key: str, label: str, value, text: str) -> Fact:
    return Fact(key=key, label=label, value=value, text=text)


def test_extract_numbers_normalises_commas_and_percents():
    tokens = extract_numbers("Revenue 1,234.5 and margin 12%")
    assert [(t.value, t.is_percent, t.has_decimal) for t in tokens] == [
        (1234.5, False, True),
        (12.0, True, False),
    ]


def test_numbers_in_texts_collects_from_every_source():
    assert numbers_in_texts(["72.40", "MOMENTUM_6M", None]) == {72.4, 6.0}


def test_a_cited_number_passes_and_an_invented_one_fails():
    facts = {"score": _fact("score", "Composite score", 72.4, "72.40")}
    good = LLMExplanation(
        headline="Steady",
        summary="The composite score is 72.40.",
        citations=["score"],
    )
    assert validate(good, facts) == []

    bad = LLMExplanation(
        headline="Steady",
        summary="The composite score is 73.",
        citations=["score"],
    )
    errors = validate(bad, facts)
    assert any("uncited number: 73" in error for error in errors)


def test_unknown_citation_key_is_rejected():
    facts = {"score": _fact("score", "Composite score", 72.4, "72.40")}
    draft = LLMExplanation(headline="x", summary="y", citations=["does.not.exist"])
    assert any("unknown citation key" in error for error in validate(draft, facts))


def test_a_bullet_may_override_the_top_level_citations():
    facts = {
        "score": _fact("score", "Composite score", 72.4, "72.40"),
        "price": _fact("price", "Price", 1650.0, "1650.00"),
    }
    draft = LLMExplanation(
        headline="Steady",
        summary="Score is 72.40.",
        citations=["score"],
        bullets=[LLMBullet(text="Price is 1650.00.", citations=["price"])],
    )
    assert validate(draft, facts) == []


def test_label_numbers_are_citable():
    facts = {"sma": _fact("sma", "Price vs 200 DMA", "ABOVE_SMA200", "ABOVE_SMA200")}
    draft = LLMExplanation(
        headline="Trend",
        summary="Price is above the 200 DMA.",
        citations=["sma"],
    )
    assert validate(draft, facts) == []
