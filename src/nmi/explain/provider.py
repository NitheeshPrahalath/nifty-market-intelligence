"""Explanation providers.

Two implementations behind one tiny protocol:

* :class:`TemplateProvider` renders the facts deterministically with no network.
  It is the default and the fallback, which means the feature works — and its
  tests run — entirely offline.
* :class:`OpenAICompatibleProvider` calls any ``/chat/completions`` endpoint
  (OpenAI, Gemini's compatibility layer, a local Ollama server) and asks for a
  JSON object. Its output is validated by the citation checker before it is
  trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from nmi.core.config import settings
from nmi.explain.prompts import build_messages
from nmi.explain.schema import FactSheet, LLMBullet, LLMExplanation


class ProviderError(RuntimeError):
    """Any failure talking to or parsing a provider."""


@dataclass(slots=True)
class ProviderResult:
    draft: LLMExplanation
    provider: str
    model: str
    raw: str | None = None


class ExplanationProvider(Protocol):
    name: str
    model: str

    def generate(self, factsheet: FactSheet, kind: str) -> ProviderResult: ...


def _at(factsheet: FactSheet, *keys: str) -> list[str] | None:
    """Rendered text for ``keys``, or ``None`` if any fact is absent."""
    texts: list[str] = []
    for key in keys:
        fact = factsheet.by_key(key)
        if fact is None:
            return None
        texts.append(fact.text)
    return texts


def _bullets_citing(factsheet: FactSheet, specs: list) -> list[LLMBullet]:
    """Build bullets from ``(keys, render)`` specs, dropping the unbackable."""
    out: list[LLMBullet] = []
    for keys, render in specs:
        texts = _at(factsheet, *keys)
        if texts is None:
            continue
        out.append(LLMBullet(text=render(texts), citations=list(keys)))
    return out


def _headline(factsheet: FactSheet) -> tuple[str, list[str]]:
    texts = _at(factsheet, "state", "horizon")
    if texts:
        state, horizon = texts
        return f"{factsheet.symbol}: {state} on the {horizon} horizon", ["state", "horizon"]
    return f"{factsheet.symbol}: recommendation summary", []


def _summary(factsheet: FactSheet, kind: str) -> tuple[str, list[str]]:
    if kind == "change":
        texts = _at(factsheet, "thesis.weakened_count", "thesis.improved_count", "first_as_of")
        if texts:
            weakened, improved, since = texts
            return (
                f"Since {since}, {weakened} tracked factors weakened and {improved} "
                f"improved for {factsheet.symbol}.",
                ["thesis.weakened_count", "thesis.improved_count", "first_as_of"],
            )
    texts = _at(factsheet, "state", "strategy", "risk_level", "thesis.composite_score")
    if texts:
        state, strategy, risk, composite = texts
        return (
            f"{factsheet.symbol} is a {state} idea under the {strategy} strategy with "
            f"{risk} risk. The composite score is {composite}.",
            ["state", "strategy", "risk_level", "thesis.composite_score"],
        )
    return (f"Structured summary for {factsheet.symbol}.", [])


def _level_bullets(factsheet: FactSheet) -> list:
    return [
        (
            ["current_price", "entry_low", "entry_high", "target_low", "target_high"],
            lambda t: (
                f"Price is {t[0]}; the entry zone is {t[1]} to {t[2]}, with targets "
                f"{t[3]} to {t[4]}."
            ),
        ),
        (
            ["invalidation_price"],
            lambda t: f"The setup is invalidated below {t[0]}.",
        ),
        (
            ["thesis.composite_score", "confidence"],
            lambda t: f"Composite score {t[0]} with confidence {t[1]}.",
        ),
    ]


def _context_bullets(factsheet: FactSheet) -> list:
    return [
        (
            ["factor.growth_score", "factor.quality_score"],
            lambda t: f"Growth score {t[0]}, quality score {t[1]}.",
        ),
        (["factor.relative_strength_score"], lambda t: f"Relative strength score {t[0]}."),
        (["factor.rs_trend"], lambda t: f"Relative strength trend is {t[0]}."),
        (["factor.trend_state"], lambda t: f"Trend state is {t[0]}."),
        (["factor.price_vs_sma200"], lambda t: f"Price versus the 200-day average: {t[0]}."),
        (["factor.valuation_label"], lambda t: f"Valuation is {t[0]}."),
        (["factor.sector_state"], lambda t: f"Sector backdrop is {t[0]}."),
        (["factor.regime_label"], lambda t: f"Market regime is {t[0]}."),
    ]


def _change_bullets(factsheet: FactSheet) -> list:
    specs = []
    for fact in factsheet.facts:
        if fact.key.startswith("change."):
            label = fact.label
            specs.append(([fact.key], (lambda lbl: lambda t: f"{lbl}: {t[0]}.")(label)))
    return _bullets_citing(factsheet, specs)


def _risk_bullets(factsheet: FactSheet) -> list:
    specs = [
        (
            ["thesis.weakened_count", "factor_count"],
            lambda t: f"{t[0]} of {t[1]} tracked factors have weakened since the thesis was set.",
        ),
        (["invalidation_price"], lambda t: f"A close below {t[0]} invalidates the setup."),
        (["risk_level"], lambda t: f"Risk is rated {t[0]}."),
    ]
    return _bullets_citing(factsheet, specs)


class TemplateProvider:
    """Deterministic, offline provider — the default and the fallback."""

    name = "template"
    model = "deterministic-template"

    def generate(self, factsheet: FactSheet, kind: str) -> ProviderResult:
        headline, headline_cites = _headline(factsheet)
        summary, summary_cites = _summary(factsheet, kind)

        if kind == "change":
            bullets = _change_bullets(factsheet)
        else:
            bullets = _bullets_citing(factsheet, _level_bullets(factsheet))
            bullets.extend(_bullets_citing(factsheet, _context_bullets(factsheet)))
            if kind == "research":
                bullets.extend(_change_bullets(factsheet))

        citations = list(dict.fromkeys([*headline_cites, *summary_cites]))
        draft = LLMExplanation(
            headline=headline,
            summary=summary,
            bullets=bullets,
            risks=_risk_bullets(factsheet),
            citations=citations,
        )
        return ProviderResult(draft=draft, provider=self.name, model=self.model)


def _extract_json(content: str) -> dict:
    """Parse model output that may be wrapped in prose or a code fence."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ProviderError("model did not return a JSON object") from None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ProviderError(f"could not parse model JSON: {exc}") from exc


class OpenAICompatibleProvider:
    """A ``/chat/completions`` client for OpenAI, Gemini-compat or Ollama."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, factsheet: FactSheet, kind: str) -> dict:
        return {
            "model": self.model,
            "messages": build_messages(factsheet),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

    def generate(self, factsheet: FactSheet, kind: str) -> ProviderResult:
        import httpx

        url = f"{self.base_url}/chat/completions"
        try:
            response = httpx.post(
                url,
                json=self._payload(factsheet, kind),
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except ProviderError:
            raise
        except Exception as exc:  # httpx errors, key errors, bad JSON
            raise ProviderError(f"{self.name} request failed: {exc}") from exc

        try:
            draft = LLMExplanation.model_validate(_extract_json(content))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"{self.name} returned an invalid explanation: {exc}") from exc
        return ProviderResult(draft=draft, provider=self.name, model=self.model, raw=content)


def get_provider(name: str | None = None) -> ExplanationProvider:
    """Resolve a provider name (defaulting to settings) to an implementation."""
    resolved = (name or settings.explain_provider or "template").strip().lower()
    if resolved in {"", "template", "none", "off", "deterministic"}:
        return TemplateProvider()
    return OpenAICompatibleProvider(
        base_url=settings.explain_base_url,
        api_key=settings.explain_api_key,
        model=settings.explain_model,
        timeout=settings.explain_timeout_seconds,
        max_tokens=settings.explain_max_tokens,
        temperature=settings.explain_temperature,
    )
