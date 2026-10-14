"""Citation checking — the guarantee that no number is invented.

A statement is only as trustworthy as the arithmetic behind it, so before an
explanation is accepted every numeric token in its prose must appear in a fact
it cited. The check is deliberately literal: ``72`` is not accepted as a
rendering of a cited ``72.4``, because the writer should quote the source, not
paraphrase it. A draft that fails is replaced by the deterministic template
provider rather than shipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from nmi.explain.schema import Fact, LLMExplanation

#: A number, optionally with thousands separators and a trailing percent sign.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


@dataclass(frozen=True, slots=True)
class NumberToken:
    raw: str
    value: float
    is_percent: bool
    has_decimal: bool


def extract_numbers(text: str | None) -> list[NumberToken]:
    """Every numeric token in ``text``, normalised for comparison."""
    if not text:
        return []
    tokens: list[NumberToken] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        is_percent = raw.endswith("%")
        body = raw[:-1] if is_percent else raw
        cleaned = body.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:  # pragma: no cover - the regex guarantees a float
            continue
        tokens.append(
            NumberToken(
                raw=raw,
                value=value,
                is_percent=is_percent,
                has_decimal="." in cleaned,
            )
        )
    return tokens


def numbers_in_texts(texts: Iterable[str | None]) -> set[float]:
    """The set of numeric values a body of text may legitimately reference."""
    values: set[float] = set()
    for text in texts:
        for token in extract_numbers(text):
            values.add(token.value)
    return values


def _allowed_numbers(keys: Iterable[str], facts: Mapping[str, Fact]) -> set[float]:
    corpus: list[str] = []
    for key in keys:
        fact = facts.get(key)
        if fact is None:
            continue
        corpus.extend([fact.text, str(fact.value), fact.label])
    return numbers_in_texts(corpus)


def validate(draft: LLMExplanation, facts: Mapping[str, Fact]) -> list[str]:
    """Return the reasons ``draft`` is not fully cited; empty means valid."""
    errors: list[str] = []
    known = set(facts)
    for key in draft.cited_keys():
        if key not in known:
            errors.append(f"unknown citation key: {key}")

    for name, text, keys in draft.texts():
        allowed = _allowed_numbers(keys, facts)
        for token in extract_numbers(text):
            if token.value not in allowed:
                errors.append(f"{name} cites an uncited number: {token.raw}")

    return errors
