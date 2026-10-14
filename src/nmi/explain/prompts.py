"""Prompt templates for the AI providers.

The prompts are written around one invariant: the model is a *writer*, not a
calculator. It is handed a closed list of facts and asked to explain them in
prose, citing the key of every fact it uses. It is explicitly forbidden from
introducing a number of its own — and the citation checker enforces that
afterwards regardless of what the model does.
"""

from __future__ import annotations

from nmi.explain.schema import PROMPT_VERSION, FactSheet

__all__ = ["KIND_INSTRUCTIONS", "PROMPT_VERSION", "build_messages", "render_facts"]

SYSTEM_PROMPT = """\
You are an equity-research writer for an Indian-markets decision-support tool.

You will be given a set of FACTS, each with a stable key, a label and a value.
Write a short, plain-language explanation grounded *only* in those facts.

Hard rules:
1. Every number you write must be copied verbatim from a FACT value or its
   rendered value, and you must list that fact's key in the `citations` array of
   the statement that uses it.
2. Never invent, estimate, sum, average or derive a number. If a figure is not
   in the FACTS, do not write it.
3. Do not invent company names, dates or events.
4. `citations` at the top level cover `headline` and `summary`; each bullet or
   risk may add its own `citations` list.
5. If the facts are too thin to support a claim, say so instead of guessing.

Return a single JSON object with exactly these keys:
  headline (string): one line, at most ~90 characters.
  summary (string): 2-3 sentences.
  bullets (array): each {"text": string, "citations": [fact-key, ...]}.
  risks (array): each {"text": string, "citations": [fact-key, ...]}.
  citations (array of fact-key strings): the facts used by headline and summary.
"""

KIND_INSTRUCTIONS = {
    "thesis": (
        "Explain why this recommendation exists: what the setup is, the levels, "
        "the score and confidence behind it, and the main risk to the idea."
    ),
    "change": (
        "Explain what has changed since the recommendation was first made, using "
        "the baseline and change facts. Say which tracked factors weakened or "
        "improved and what that means for holding the position."
    ),
    "research": (
        "Write a compact research note: the thesis, the supporting evidence, the "
        "valuation and technical context, and the key risks. Do not add any fact "
        "that is not provided."
    ),
}


def render_facts(factsheet: FactSheet) -> str:
    """A stable, compact listing of every fact the writer may cite."""
    lines = []
    for fact in factsheet.facts:
        lines.append(f"- key={fact.key} | {fact.label} = {fact.text} ({fact.group})")
    return "\n".join(lines)


def build_user_prompt(factsheet: FactSheet) -> str:
    instruction = KIND_INSTRUCTIONS.get(factsheet.kind, KIND_INSTRUCTIONS["thesis"])
    return (
        f"Write a `{factsheet.kind}` explanation for {factsheet.symbol} "
        f"as of {factsheet.as_of.isoformat()}.\n\n"
        f"{instruction}\n\n"
        f"FACTS:\n{render_facts(factsheet)}\n\n"
        "Return only the JSON object described in the system message."
    )


def build_messages(factsheet: FactSheet) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(factsheet)},
    ]
