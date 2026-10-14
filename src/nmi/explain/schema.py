"""Typed structures for the explanations layer.

The prose is never free-form: the deterministic provider renders it and every
other provider must return the same shape (:class:`LLMExplanation`). A
:class:`Fact` is the only place a number is allowed to come from, which is what
makes the output auditable rather than merely plausible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

#: Bumped whenever the prompt or the fact rendering changes in a way that could
#: change the output, so old rows stay interpretable.
PROMPT_VERSION = "v1"

#: The three explanation flavours. Each is a different question over the same
#: facts: why is this live, what changed, and what is the fuller research note.
KINDS = ("thesis", "change", "research")


@dataclass(frozen=True, slots=True)
class Fact:
    """One named, sourced number or label a statement may cite."""

    key: str
    label: str
    value: Any
    text: str
    group: str = "score"
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "text": self.text,
            "group": self.group,
            "source": self.source,
        }


@dataclass(slots=True)
class FactSheet:
    """The complete, ordered set of facts one explanation may draw on."""

    recommendation_id: int
    symbol: str
    kind: str
    as_of: date
    facts: list[Fact] = field(default_factory=list)

    @property
    def keys(self) -> set[str]:
        return {fact.key for fact in self.facts}

    def by_key(self, key: str) -> Fact | None:
        for fact in self.facts:
            if fact.key == key:
                return fact
        return None

    def value(self, key: str) -> Any:
        fact = self.by_key(key)
        return fact.value if fact else None

    def as_dict(self) -> list[dict[str, Any]]:
        return [fact.as_dict() for fact in self.facts]

    def canonical(self) -> dict[str, Any]:
        """A stable projection used to hash the exact facts behind a note."""
        return {
            "recommendation_id": self.recommendation_id,
            "symbol": self.symbol,
            "kind": self.kind,
            "as_of": self.as_of.isoformat(),
            "facts": [
                {k: fact.as_dict()[k] for k in ("key", "value", "text")}
                for fact in sorted(self.facts, key=lambda f: f.key)
            ],
        }

    def source_hash(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class Bullet(BaseModel):
    """A single statement plus the fact keys that back it."""

    text: str
    citations: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    """The resolved value of a cited fact, so the client can render the source."""

    key: str
    label: str
    value: str
    group: str = "score"
    source: str = ""


class LLMBullet(BaseModel):
    """A bullet as a model returns it: text plus the keys it used.

    ``citations`` may be empty, in which case the surrounding explanation's
    top-level citations apply.
    """

    text: str
    citations: list[str] = Field(default_factory=list)


class LLMExplanation(BaseModel):
    """The structured shape every provider must produce."""

    headline: str
    summary: str
    bullets: list[LLMBullet] = Field(default_factory=list)
    risks: list[LLMBullet] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)

    def cited_keys(self) -> list[str]:
        keys = list(self.citations)
        for bullet in [*self.bullets, *self.risks]:
            keys.extend(bullet.citations)
        seen: set[str] = set()
        out: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def texts(self) -> list[tuple[str, list[str]]]:
        """Every text field with the citations that govern its numbers."""
        top = self.citations
        fields = [("headline", self.headline, top), ("summary", self.summary, top)]
        for index, bullet in enumerate(self.bullets):
            fields.append((f"bullets[{index}]", bullet.text, bullet.citations or top))
        for index, risk in enumerate(self.risks):
            fields.append((f"risks[{index}]", risk.text, risk.citations or top))
        return fields


class Explanation(BaseModel):
    """The persisted, fully-resolved explanation returned to callers."""

    recommendation_id: int
    kind: str
    as_of: date
    headline: str
    summary: str
    bullets: list[Bullet] = Field(default_factory=list)
    risks: list[Bullet] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    provider: str = "template"
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    generated_at: datetime
    source_hash: str
    fallback_reason: str | None = None
    valid: bool = True
    validation_errors: list[str] = Field(default_factory=list)
