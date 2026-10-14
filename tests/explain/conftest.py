"""Fixtures for the Phase-9 explanation tests.

A real pipeline run is unnecessary here: the explanations layer only reads the
tracked recommendation and its snapshots, so a hand-built recommendation with a
baseline and a current thesis is both faster and more precise than generating
one from price fixtures.
"""

from __future__ import annotations

from datetime import date

import pytest

from nmi.core.enums import Exchange, InstrumentType
from nmi.core.models import (
    Company,
    EventType,
    HorizonType,
    Instrument,
    Recommendation,
    RecommendationEvent,
    RecommendationState,
    RecommendationVersion,
    RiskLevel,
    Sector,
    SignalType,
    Strategy,
    StrategyVersion,
    ThesisSnapshot,
)

FIRST = date(2024, 3, 1)
AS_OF = date(2024, 6, 28)


def _factor(value, text=None):
    return {"value": value, "text": text if text is not None else str(value)}


@pytest.fixture()
def recommendation(sqlite_session) -> Recommendation:
    """One open recommendation with a baseline and a current thesis snapshot."""
    session = sqlite_session

    sector = Sector(name="Information Technology", code="IT")
    session.add(sector)
    session.flush()

    company = Company(isin="INE009A01021", name="Infosys Ltd", sector_id=sector.id)
    session.add(company)
    session.flush()

    instrument = Instrument(
        company_id=company.id,
        exchange=Exchange.NSE,
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
    )
    session.add(instrument)
    session.flush()

    strategy = Strategy(
        code="MOMENTUM_TREND", name="Momentum & Trend", horizon=HorizonType.MEDIUM_TERM
    )
    session.add(strategy)
    session.flush()

    version = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        engine_version="v1",
        rules={"all": []},
        is_current=True,
    )
    session.add(version)
    session.flush()

    rec = Recommendation(
        instrument_id=instrument.id,
        strategy_version_id=version.id,
        strategy_code=strategy.code,
        state=RecommendationState.ENTRY,
        horizon=HorizonType.MEDIUM_TERM,
        confidence=72.0,
        current_price=1650.0,
        entry_low=1600.0,
        entry_high=1650.0,
        target_low=1800.0,
        target_high=1950.0,
        invalidation_price=1520.0,
        risk_level=RiskLevel.MODERATE,
        expected_holding_days_min=20,
        expected_holding_days_max=60,
        thesis="Strong earnings momentum with improving relative strength.",
        created_reason="Golden cross with rising EPS growth.",
        latest_version=2,
        first_as_of=FIRST,
        last_as_of=AS_OF,
        active_since=FIRST,
    )
    session.add(rec)
    session.flush()

    session.add_all(
        [
            RecommendationVersion(
                recommendation_id=rec.id,
                version=1,
                as_of=FIRST,
                state=RecommendationState.ENTRY,
                signal_type=SignalType.BUY_SETUP,
                price=1610.0,
                confidence=74.0,
                composite_score=75.0,
                risk_level=RiskLevel.MODERATE,
                entry_low=1600.0,
                entry_high=1650.0,
                target_low=1800.0,
                target_high=1950.0,
                rules_result=[{"group": "entry", "passed": True}],
                change_summary="Initial entry.",
                reasons=["Golden cross"],
            ),
            RecommendationVersion(
                recommendation_id=rec.id,
                version=2,
                as_of=AS_OF,
                state=RecommendationState.ENTRY,
                signal_type=SignalType.BUY_SETUP,
                price=1650.0,
                confidence=72.0,
                composite_score=72.4,
                risk_level=RiskLevel.MODERATE,
                entry_low=1600.0,
                entry_high=1650.0,
                target_low=1800.0,
                target_high=1950.0,
                rules_result=[{"group": "entry", "passed": True}],
                change_summary="Confidence eased slightly.",
                reasons=["Golden cross"],
            ),
        ]
    )

    baseline_factors = {
        "composite_score": _factor(75.0, "75.0"),
        "quality_score": _factor(80.0, "80.0"),
        "relative_strength_score": _factor(70.0, "70.0"),
        "rs_trend": _factor("IMPROVING", "IMPROVING"),
        "price_vs_sma200": _factor("ABOVE_SMA200", "ABOVE_SMA200"),
        "valuation_label": _factor("CHEAP", "CHEAP"),
        "sector_state": _factor("LEADING", "LEADING"),
        "regime_label": _factor("RISK_ON", "RISK_ON"),
    }
    current_factors = {
        "composite_score": _factor(72.4, "72.4"),
        "quality_score": _factor(80.0, "80.0"),
        "relative_strength_score": _factor(70.0, "70.0"),
        "rs_trend": _factor("IMPROVING", "IMPROVING"),
        "price_vs_sma200": _factor("BELOW_SMA200", "BELOW_SMA200"),
        "valuation_label": _factor("FAIRLY_VALUED", "FAIRLY_VALUED"),
        "sector_state": _factor("LEADING", "LEADING"),
        "regime_label": _factor("RISK_ON", "RISK_ON"),
    }
    session.add_all(
        [
            ThesisSnapshot(
                recommendation_id=rec.id,
                as_of=FIRST,
                state=RecommendationState.ENTRY,
                factors=baseline_factors,
                composite_score=75.0,
                confidence=74.0,
                price=1610.0,
                weakened_count=0,
                improved_count=0,
                summary="0 of 8 tracked factors weakened; thesis intact.",
            ),
            ThesisSnapshot(
                recommendation_id=rec.id,
                as_of=AS_OF,
                state=RecommendationState.ENTRY,
                factors=current_factors,
                composite_score=72.4,
                confidence=72.0,
                price=1650.0,
                weakened_count=2,
                improved_count=0,
                summary="2 of 8 tracked factors weakened since entry.",
            ),
        ]
    )
    session.add_all(
        [
            RecommendationEvent(
                recommendation_id=rec.id,
                as_of=FIRST,
                event_type=EventType.RECOMMENDATION_CREATED,
                title="New recommendation",
                message="Created on a golden cross.",
                detail={},
                recommendation_version=1,
                price=1610.0,
                calc_version="v1",
                dedupe_key=f"rec{rec.id}:created",
            ),
            RecommendationEvent(
                recommendation_id=rec.id,
                as_of=AS_OF,
                event_type=EventType.THESIS_WEAKENED,
                title="Thesis weakening",
                message="Price fell below the 200 DMA.",
                detail={},
                recommendation_version=2,
                price=1650.0,
                calc_version="v1",
                dedupe_key=f"rec{rec.id}:weakened",
            ),
        ]
    )
    session.commit()
    return rec
