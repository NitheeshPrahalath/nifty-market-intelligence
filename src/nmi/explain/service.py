"""Orchestration and persistence for AI explanations.

The flow is deliberately defensive: draft with the configured provider, *always*
run the citation checker, and fall back to the deterministic template writer if
the provider errored or invented a number. Every attempt is persisted with the
facts hash, so the same inputs never produce a second row and a failure is
recorded rather than hidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nmi.core.models import Explanation as ExplanationRow
from nmi.core.models import (
    Index,
    IndexMembership,
    Recommendation,
    RecommendationState,
)
from nmi.explain.citations import validate
from nmi.explain.facts import build_fact_sheet
from nmi.explain.provider import ExplanationProvider, TemplateProvider, get_provider
from nmi.explain.schema import (
    PROMPT_VERSION,
    Bullet,
    Citation,
    Explanation,
)


def _existing(
    session: Session,
    recommendation_id: int,
    kind: str,
    source_hash: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ExplanationRow | None:
    """A stored explanation for these exact facts (optionally one provider)."""
    stmt = select(ExplanationRow).where(
        ExplanationRow.recommendation_id == recommendation_id,
        ExplanationRow.kind == kind,
        ExplanationRow.source_hash == source_hash,
        ExplanationRow.prompt_version == PROMPT_VERSION,
    )
    if provider is not None:
        stmt = stmt.where(ExplanationRow.provider == provider)
    if model is not None:
        stmt = stmt.where(ExplanationRow.model == model)
    stmt = stmt.order_by(ExplanationRow.created_at.desc(), ExplanationRow.id.desc()).limit(1)
    return session.scalar(stmt)


def _to_explanation(row: ExplanationRow) -> Explanation:
    return Explanation(
        recommendation_id=row.recommendation_id,
        kind=row.kind,
        as_of=row.as_of,
        headline=row.headline,
        summary=row.summary,
        bullets=[Bullet(**bullet) for bullet in (row.bullets or [])],
        risks=[Bullet(**risk) for risk in (row.risks or [])],
        citations=[Citation(**citation) for citation in (row.citations or [])],
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        generated_at=row.created_at,
        source_hash=row.source_hash,
        fallback_reason=row.fallback_reason,
        valid=row.valid,
        validation_errors=list(row.validation_errors or []),
    )


def generate_explanation(
    session: Session,
    recommendation_id: int,
    kind: str = "thesis",
    *,
    provider: ExplanationProvider | None = None,
    force: bool = False,
) -> Explanation | None:
    """Write (or return the already-written) explanation for a recommendation."""
    factsheet = build_fact_sheet(session, recommendation_id, kind)
    if factsheet is None:
        return None

    chooser = provider or get_provider()
    fallback = TemplateProvider()
    source_hash = factsheet.source_hash()

    cached = _existing(session, recommendation_id, kind, source_hash)
    if cached is not None and not force:
        return _to_explanation(cached)

    producer = chooser
    fallback_reason: str | None = None
    try:
        result = chooser.generate(factsheet, kind)
    except Exception as exc:  # provider outage, bad JSON, any transport error
        fallback_reason = f"{chooser.name} provider failed: {exc}"
        producer = fallback
        result = fallback.generate(factsheet, kind)

    draft = result.draft
    facts_by_key = {fact.key: fact for fact in factsheet.facts}
    errors = validate(draft, facts_by_key)

    if errors and producer.name != fallback.name:
        fallback_reason = "; ".join(
            filter(None, [fallback_reason, f"citation check failed: {'; '.join(errors[:5])}"])
        )
        producer = fallback
        result = fallback.generate(factsheet, kind)
        draft = result.draft
        errors = validate(draft, facts_by_key)

    citations = [
        Citation(
            key=fact.key,
            label=fact.label,
            value=fact.text,
            group=fact.group,
            source=fact.source,
        )
        for key in draft.cited_keys()
        if (fact := facts_by_key.get(key)) is not None
    ]

    fields = {
        "as_of": factsheet.as_of,
        "provider": result.provider,
        "model": result.model,
        "headline": draft.headline,
        "summary": draft.summary,
        "bullets": [bullet.model_dump() for bullet in draft.bullets],
        "risks": [risk.model_dump() for risk in draft.risks],
        "citations": [citation.model_dump() for citation in citations],
        "fallback_reason": fallback_reason,
        "valid": not errors,
        "validation_errors": errors,
        "raw_response": result.raw,
    }

    row = _existing(
        session, recommendation_id, kind, source_hash,
        provider=result.provider, model=result.model,
    )
    if row is None:
        row = ExplanationRow(
            recommendation_id=recommendation_id,
            kind=kind,
            prompt_version=PROMPT_VERSION,
            source_hash=source_hash,
            **fields,
        )
        session.add(row)
    else:  # force re-run: replace the stored prose in place
        for name, value in fields.items():
            setattr(row, name, value)
    session.flush()
    return _to_explanation(row)


def get_latest_explanation(
    session: Session, recommendation_id: int, kind: str | None = None
) -> Explanation | None:
    """The most recently written explanation for a recommendation."""
    stmt = select(ExplanationRow).where(
        ExplanationRow.recommendation_id == recommendation_id
    )
    if kind:
        stmt = stmt.where(ExplanationRow.kind == kind)
    stmt = stmt.order_by(ExplanationRow.created_at.desc(), ExplanationRow.id.desc()).limit(1)
    row = session.scalar(stmt)
    return _to_explanation(row) if row is not None else None


def select_recommendations(
    session: Session,
    *,
    recommendation_ids: Sequence[int] | None = None,
    index_code: str | None = None,
    include_closed: bool = False,
) -> list[Recommendation]:
    """Resolve the work list for a batch run (all open, by id, or by index)."""
    stmt = select(Recommendation)
    if not include_closed:
        stmt = stmt.where(Recommendation.state != RecommendationState.CLOSED)
    if recommendation_ids:
        stmt = stmt.where(Recommendation.id.in_(list(recommendation_ids)))
    if index_code:
        index = session.scalar(select(Index).where(Index.code == index_code))
        if index is None:
            return []
        as_of = session.scalar(select(func.max(Recommendation.last_as_of))) or date.today()
        member_ids = list(
            session.scalars(
                select(IndexMembership.instrument_id).where(
                    IndexMembership.index_id == index.id,
                    IndexMembership.effective_from <= as_of,
                    or_(
                        IndexMembership.effective_to.is_(None),
                        IndexMembership.effective_to >= as_of,
                    ),
                )
            )
        )
        stmt = stmt.where(Recommendation.instrument_id.in_(member_ids))
    return list(session.scalars(stmt.order_by(Recommendation.id)).all())


def generate_explanations(
    session: Session,
    recommendations: Iterable[Recommendation],
    kind: str = "thesis",
    *,
    provider: ExplanationProvider | None = None,
    force: bool = False,
) -> list[tuple[Recommendation, Explanation | None]]:
    """Generate explanations for a batch, committing once at the end."""
    chooser = provider or get_provider()
    results: list[tuple[Recommendation, Explanation | None]] = []
    for rec in recommendations:
        explanation = generate_explanation(
            session, rec.id, kind, provider=chooser, force=force
        )
        results.append((rec, explanation))
    session.commit()
    return results
