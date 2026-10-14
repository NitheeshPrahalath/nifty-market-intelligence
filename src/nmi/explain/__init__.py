"""AI explanations layer (Phase 9).

Turns the structured, as-of facts the platform already computes into
natural-language thesis/change/research notes. Two properties make the output
trustworthy rather than decorative:

* **Cited** — every number in the prose is traceable to a named fact drawn from
  the database, and a draft that mentions a number it did not cite is rejected;
* **Audited** — each explanation is stored immutably with its provider, model,
  prompt version and the hash of the exact facts it was written from.

The deterministic ``template`` provider needs no network and doubles as the
fallback for every other provider, so an LLM outage degrades to plain prose, not
to a missing feature.
"""

from __future__ import annotations

from nmi.explain.schema import Bullet, Citation, Fact, FactSheet

__all__ = ["Bullet", "Citation", "Fact", "FactSheet"]
