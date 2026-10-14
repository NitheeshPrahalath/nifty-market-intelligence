"""Providers: the deterministic writer and the OpenAI-compatible client."""

from __future__ import annotations

import json

import httpx
import pytest

from nmi.explain.citations import validate
from nmi.explain.facts import build_fact_sheet
from nmi.explain.provider import (
    OpenAICompatibleProvider,
    ProviderError,
    TemplateProvider,
    get_provider,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_template_provider_is_the_default_writer():
    assert isinstance(get_provider("template"), TemplateProvider)
    assert isinstance(get_provider(None), TemplateProvider)
    assert get_provider("deterministic").name == "template"


def test_template_output_is_fully_cited_for_every_kind(recommendation, sqlite_session):
    facts = {fact.key: fact for fact in build_fact_sheet(sqlite_session, recommendation.id).facts}
    for kind in ("thesis", "change", "research"):
        sheet = build_fact_sheet(sqlite_session, recommendation.id, kind)
        result = TemplateProvider().generate(sheet, kind)
        assert result.draft.headline
        assert result.draft.summary
        assert validate(result.draft, facts) == [], kind
        assert result.draft.bullets


def test_openai_provider_parses_a_well_formed_response(recommendation, sqlite_session, monkeypatch):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)
    content = json.dumps(
        {
            "headline": "INFY holds a BUY",
            "summary": "Composite score is 72.40.",
            "bullets": [{"text": "Price is 1650.00.", "citations": ["current_price"]}],
            "risks": [],
            "citations": ["thesis.composite_score"],
        }
    )
    captured: dict = {}

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleProvider(
        base_url="https://example.test/v1/",
        api_key="secret",
        model="test-model",
    )
    result = provider.generate(sheet, "thesis")
    assert result.provider == "openai"
    assert result.model == "test-model"
    assert result.draft.headline == "INFY holds a BUY"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_openai_provider_json_wrapped_in_a_code_fence_still_parses(
    recommendation, sqlite_session, monkeypatch
):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)
    content = '```json\n{"headline": "ok", "summary": "fine", "citations": []}\n```'
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"choices": [{"message": {"content": content}}]}),
    )
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1", api_key=None, model="llama3"
    )
    result = provider.generate(sheet, "thesis")
    assert result.draft.headline == "ok"


def test_openai_provider_raises_on_garbage(recommendation, sqlite_session, monkeypatch):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse({"choices": [{"message": {"content": "not json"}}]}),
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", api_key=None, model="m")
    with pytest.raises(ProviderError):
        provider.generate(sheet, "thesis")


def test_openai_provider_wraps_transport_errors(recommendation, sqlite_session, monkeypatch):
    sheet = build_fact_sheet(sqlite_session, recommendation.id)

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    provider = OpenAICompatibleProvider(base_url="http://x/v1", api_key=None, model="m")
    with pytest.raises(ProviderError):
        provider.generate(sheet, "thesis")
