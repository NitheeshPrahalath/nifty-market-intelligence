from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select

from nmi.analysis.scoring import DEFAULT_WEIGHTS
from nmi.core.enums import RunStatus
from nmi.core.models import (
    FundamentalMetric,
    HorizonMetric,
    IndexPrice,
    IngestionRun,
    Instrument,
    MarketRegime,
    MomentumMetric,
    Recommendation,
    RecommendationVersion,
    RelativeStrengthMetric,
    ScoringSnapshot,
    SectorMetric,
    Signal,
    Strategy,
    StrategyParameter,
    StrategyVersion,
    TechnicalIndicator,
    ValuationMetric,
)
from nmi.ingestion import store
from nmi.ingestion.backfill import IngestionService
from nmi.metrics.service import MetricsService, _benchmark_prices

START = date(2024, 1, 1)
END = date(2024, 6, 28)


def _sessions(start: date, end: date) -> int:
    n = (end - start).days + 1
    return sum(1 for i in range(n) if (start + timedelta(days=i)).weekday() < 5)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _stock_rows(session, model, symbol: str):
    return session.scalars(
        select(model)
        .join(Instrument, Instrument.id == model.instrument_id)
        .where(Instrument.symbol == symbol)
        .order_by(model.as_of)
    ).all()


def _seed_backtest(session) -> None:
    svc = IngestionService(session)
    svc.seed_universe()
    svc.backfill_corporate_actions(["NIFTY_50"], START, END)
    svc.backfill_prices(["NIFTY_50"], START, END)
    svc.backfill_fundamentals()


def test_metrics_e2e_populates_all_tables(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)

    idx = msvc.backfill_index_prices(["NIFTY_50"])
    assert idx.status == RunStatus.SUCCEEDED
    assert _count(sqlite_session, IndexPrice) == _sessions(date(2023, 7, 13), date(2024, 6, 28))

    fund = msvc.compute_fundamentals(["NIFTY_50"])
    assert fund.status == RunStatus.SUCCEEDED
    assert fund.items_failed == 0
    assert _count(sqlite_session, FundamentalMetric) == 198  # 11 income years x 18 metrics

    sessions = _sessions(START, END)
    tech = msvc.compute_technical(["NIFTY_50"], START, END)
    assert tech.status == RunStatus.SUCCEEDED
    assert tech.items_failed == 0
    assert tech.items_processed == 3 * sessions
    assert _count(sqlite_session, TechnicalIndicator) == 3 * sessions

    mom = msvc.compute_momentum(["NIFTY_50"], START, END)
    assert mom.status == RunStatus.SUCCEEDED
    assert mom.items_failed == 0
    assert _count(sqlite_session, MomentumMetric) == 3 * sessions
    assert _count(sqlite_session, RelativeStrengthMetric) == 3 * sessions

    val = msvc.compute_valuation(["NIFTY_50"], START, END)
    assert val.status == RunStatus.SUCCEEDED
    assert val.items_failed == 0
    assert _count(sqlite_session, ValuationMetric) == 3 * sessions


def test_metrics_as_of_values_and_no_lookahead(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])
    msvc.compute_fundamentals(["NIFTY_50"])
    msvc.compute_technical(["NIFTY_50"], START, END)
    msvc.compute_momentum(["NIFTY_50"], START, END)
    msvc.compute_valuation(["NIFTY_50"], START, END)

    tech = _stock_rows(sqlite_session, TechnicalIndicator, "RELIANCE")[-1]
    assert tech.as_of == END
    assert tech.sma20 is not None
    assert tech.rsi14 is not None

    mom = _stock_rows(sqlite_session, MomentumMetric, "RELIANCE")
    assert mom[-1].as_of == END
    assert mom[-1].return_1m is not None
    assert mom[-1].return_12m is None  # 252-session window > history available

    rs = _stock_rows(sqlite_session, RelativeStrengthMetric, "RELIANCE")
    assert rs[-1].benchmark == "NIFTY_50"
    assert rs[-1].rs_1m is not None

    bench_ret = _benchmark_prices(sqlite_session, "NIFTY_50")
    base = bench_ret[-1 - 21].close
    expected_rs = mom[-1].return_1m - (bench_ret[-1].close / base - 1) * 100
    assert rs[-1].rs_1m == pytest.approx(expected_rs, abs=1e-4)  # stored with 4dp scale

    val = _stock_rows(sqlite_session, ValuationMetric, "RELIANCE")
    assert val[-1].as_of == END
    assert val[-1].pe is not None
    assert val[-1].valuation_label in (
        "CHEAP",
        "FAIRLY_VALUED",
        "MODERATELY_EXPENSIVE",
        "EXPENSIVE",
    )


def test_compute_eod_chains_and_rerun_is_idempotent(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])

    results = msvc.compute_eod(["NIFTY_50"], START, END)
    assert len(results) == 10
    assert all(r.status == RunStatus.SUCCEEDED for r in results)
    assert all(r.items_failed == 0 for r in results)

    tech_before = _count(sqlite_session, TechnicalIndicator)
    mom_before = _count(sqlite_session, MomentumMetric)
    val_before = _count(sqlite_session, ValuationMetric)
    sector_before = _count(sqlite_session, SectorMetric)
    regime_before = _count(sqlite_session, MarketRegime)
    horizon_before = _count(sqlite_session, HorizonMetric)
    scoring_before = _count(sqlite_session, ScoringSnapshot)
    signal_before = _count(sqlite_session, Signal)
    rec_before = _count(sqlite_session, Recommendation)
    assert tech_before > 0 and mom_before > 0 and val_before > 0
    assert sector_before > 0 and regime_before > 0
    assert horizon_before > 0 and scoring_before > 0
    assert signal_before > 0

    rerun = msvc.compute_eod(["NIFTY_50"], START, END)
    assert all(r.status == RunStatus.SUCCEEDED for r in rerun)
    assert _count(sqlite_session, TechnicalIndicator) == tech_before
    assert _count(sqlite_session, MomentumMetric) == mom_before
    assert _count(sqlite_session, ValuationMetric) == val_before
    assert _count(sqlite_session, SectorMetric) == sector_before
    assert _count(sqlite_session, MarketRegime) == regime_before
    assert _count(sqlite_session, HorizonMetric) == horizon_before
    assert _count(sqlite_session, ScoringSnapshot) == scoring_before
    assert _count(sqlite_session, Signal) == signal_before
    assert _count(sqlite_session, Recommendation) == rec_before
    assert _count(sqlite_session, RecommendationVersion) == _count(
        sqlite_session, Recommendation
    )  # one initial version per live recommendation, never rewritten

    runs = sqlite_session.scalars(select(IngestionRun)).all()
    assert all(r.status == RunStatus.SUCCEEDED for r in runs)


def test_phase3_tables_and_values(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])
    msvc.compute_eod(["NIFTY_50"], START, END)

    sessions = _sessions(START, END)

    # Sector aggregates: two fixture sectors have members, one sector has none.
    assert _count(sqlite_session, SectorMetric) == 2 * sessions
    sector_rows = sqlite_session.scalars(
        select(SectorMetric).order_by(SectorMetric.as_of.desc())
    ).all()
    assert {r.as_of for r in sector_rows} >= {START, END}
    assert all(
        r.sector_state in {"LEADING", "NEUTRAL", "LAGGING", "DEFENSIVE"} for r in sector_rows
    )
    assert all(START <= r.as_of <= END for r in sector_rows)
    assert all(r.calc_version == "v1" for r in sector_rows)
    assert all(r.member_count > 0 for r in sector_rows)

    # Market regime: one row per index session, with as-of-consistent index stats.
    assert _count(sqlite_session, MarketRegime) == sessions
    regime = sqlite_session.scalars(
        select(MarketRegime).order_by(MarketRegime.as_of.desc())
    ).first()
    assert regime.as_of == END
    assert regime.member_count == 3
    assert regime.breadth_above_sma50_pct is not None
    assert regime.regime_label in {"RISK_ON", "CAUTIOUS", "RISK_OFF", "STRESSED"}
    assert 0 <= regime.regime_score <= 100

    bench = _benchmark_prices(sqlite_session, "NIFTY_50")
    expected_3m = (bench[-1].close / bench[-1 - 63].close - 1) * 100
    assert regime.index_return_3m == pytest.approx(expected_3m, abs=1e-4)
    assert regime.index_drawdown_pct <= 0

    # Horizon: every member-day scored, one preferred horizon named.
    assert _count(sqlite_session, HorizonMetric) == 3 * sessions
    horizon = _stock_rows(sqlite_session, HorizonMetric, "RELIANCE")[-1]
    assert horizon.as_of == END
    assert horizon.preferred_horizon in {"SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"}
    assert 0 <= horizon.short_term_score <= 100
    assert 0 <= horizon.medium_term_score <= 100
    assert 0 <= horizon.long_term_score <= 100

    # Scoring: eight components + composite, driven by seeded weights.
    assert _count(sqlite_session, ScoringSnapshot) == 3 * sessions
    score = _stock_rows(sqlite_session, ScoringSnapshot, "RELIANCE")[-1]
    assert score.as_of == END
    assert score.parameter_set == "default"
    assert score.score_label in {"STRONG", "GOOD", "NEUTRAL", "WEAK", "POOR"}
    assert 0 <= score.composite_score <= 100
    assert score.trend_score is not None
    assert score.quality_score is not None
    assert score.preferred_horizon in {"SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"}

    weights = sqlite_session.scalars(select(StrategyParameter)).all()
    assert len(weights) == 8
    assert sum(float(w.value) for w in weights) == pytest.approx(1.0)
    assert {w.parameter_set for w in weights} == {"default"}


def test_scoring_weights_come_from_strategy_parameters(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])
    msvc.compute_eod(["NIFTY_50"], START, END)

    momentum_only = {name: 0.0 for name in DEFAULT_WEIGHTS}
    momentum_only["momentum"] = 1.0
    sqlite_session.execute(
        delete(StrategyParameter).where(StrategyParameter.parameter_set == "momentum_only")
    )
    store.upsert_strategy_parameters(sqlite_session, "momentum_only", momentum_only)
    sqlite_session.commit()

    msvc.compute_scoring(["NIFTY_50"], START, END, "momentum_only")

    rows = _stock_rows(sqlite_session, ScoringSnapshot, "RELIANCE")
    assert rows[-1].parameter_set == "momentum_only"
    assert float(rows[-1].composite_score) == pytest.approx(
        float(rows[-1].momentum_score), abs=1e-4
    )


def test_phase4_strategies_and_signals(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])

    assert msvc.seed_strategies() == 3
    assert msvc.seed_strategies() == 0  # idempotent: same rules, no new versions
    strategies = sqlite_session.scalars(select(Strategy).order_by(Strategy.code)).all()
    assert [s.code for s in strategies] == [
        "lt_quality_value",
        "mt_momentum_quality",
        "st_breakout_momentum",
    ]
    assert all(s.is_active for s in strategies)
    versions = sqlite_session.scalars(select(StrategyVersion)).all()
    assert len(versions) == 3
    assert all(v.version == 1 and v.is_current for v in versions)
    assert {v.engine_version for v in versions} == {"v1"}
    assert all("entry" in v.rules and "risk" in v.rules for v in versions)

    msvc.compute_eod(["NIFTY_50"], START, END)

    sessions = _sessions(START, END)
    assert _count(sqlite_session, Signal) == 3 * sessions * 3
    latest = sqlite_session.scalars(
        select(Signal).where(Signal.as_of == END).order_by(Signal.id)
    ).all()
    assert len(latest) == 9  # 3 instruments x 3 strategies
    for signal in latest:
        assert signal.calc_version == "v1"
        assert signal.signal_type in {
            "BUY_SETUP", "WATCH", "HOLD", "REDUCE", "EXIT_WARNING", "EXIT"
        }
        assert signal.state in {
            "WATCH", "POTENTIAL_ENTRY", "ENTRY", "HOLD",
            "THESIS_WEAKENING", "EXIT_REVIEW", "EXIT", "CLOSED",
        }
        assert signal.horizon in {"SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"}
        assert signal.risk_level in {"LOW", "MODERATE", "HIGH", "VERY_HIGH"}
        assert signal.thesis
        assert signal.reasons
        assert signal.expected_holding_days_min < signal.expected_holding_days_max
        if signal.entry_low is not None and signal.entry_high is not None:
            assert signal.entry_low <= signal.entry_high
            if signal.invalidation_price is not None:
                assert signal.invalidation_price < signal.entry_low
            if signal.target_low is not None:
                assert signal.target_low > signal.entry_high
        groups = {group["name"] for group in signal.rules_result}
        assert "entry" in groups
    assert all(s.as_of >= START and s.as_of <= END for s in sqlite_session.scalars(select(Signal)))

    # Context really flows into the rules: the strategy that demands a 52-week
    # breakout only fires on breakout days.
    breakout = sqlite_session.scalars(
        select(Signal).join(StrategyVersion, StrategyVersion.id == Signal.strategy_version_id)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(Strategy.code == "st_breakout_momentum")
    ).all()
    assert breakout
    for signal in breakout:
        entry = next(g for g in signal.rules_result if g["name"] == "entry")
        breakout_condition = next(c for c in entry["conditions"] if c["key"] == "breakout_52w")
        assert (breakout_condition["status"] == "pass") == (signal.signal_type == "BUY_SETUP")

    # Re-running the EOD chain updates signals in place, never duplicates them.
    before = _count(sqlite_session, Signal)
    msvc.compute_eod(["NIFTY_50"], START, END)
    assert _count(sqlite_session, Signal) == before


def test_recommendations_are_created_once_with_an_initial_version(
    sqlite_session, pointed_at_long_fixtures
):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])
    msvc.compute_eod(["NIFTY_50"], START, END)

    # A deliberately lenient strategy so the latest day always qualifies.
    store.upsert_strategies(
        sqlite_session,
        [
            {
                "code": "always_entry",
                "name": "Test always-entry",
                "description": "Composite score at any level",
                "horizon": "MEDIUM_TERM",
                "is_active": True,
            }
        ],
    )
    strategy = store.get_strategy_by_code(sqlite_session, "always_entry")
    store.upsert_strategy_versions(
        sqlite_session,
        strategy.id,
        [
            {
                "version": 1,
                "engine_version": "v1",
                "rules": {
                    "entry": {
                        "conditions": [
                            {
                                "key": "composite_score",
                                "op": ">=",
                                "value": 0.0,
                                "label": "Composite score",
                            }
                        ],
                        "mode": "all",
                    },
                    "risk": {
                        "entry_low_atr": 0.25,
                        "entry_high_atr": 0.0,
                        "stop_atr": 2.0,
                        "target_r": 2.0,
                        "holding_days_min": 30,
                        "holding_days_max": 120,
                    },
                },
                "notes": None,
                "is_current": True,
            }
        ],
    )
    sqlite_session.commit()
    assert msvc.seed_strategies() == 0  # catalog seeding leaves the custom strategy alone

    signals_result = msvc.compute_signals(["NIFTY_50"], START, END)
    assert signals_result.items_failed == 0
    rec_result = msvc.generate_recommendations(["NIFTY_50"], END)
    assert rec_result.status == RunStatus.SUCCEEDED

    recs = sqlite_session.scalars(
        select(Recommendation).where(Recommendation.strategy_code == "always_entry")
    ).all()
    assert len(recs) == 3  # one per instrument, not per day
    for rec in recs:
        assert rec.state == "ENTRY"
        assert rec.horizon == "MEDIUM_TERM"
        assert rec.first_as_of == END
        assert rec.last_as_of == END
        assert rec.latest_version == 1
        assert rec.current_price is not None
        assert rec.entry_low <= rec.entry_high
        assert rec.invalidation_price < rec.entry_low
        assert rec.target_low > rec.entry_high
        assert rec.thesis
        assert rec.created_reason
        assert rec.expected_holding_days_min == 30
        assert rec.expected_holding_days_max == 120

    versions = sqlite_session.scalars(
        select(RecommendationVersion).where(
            RecommendationVersion.recommendation_id.in_([r.id for r in recs])
        )
    ).all()
    assert len(versions) == 3
    for version in versions:
        rec = next(r for r in recs if r.id == version.recommendation_id)
        assert version.version == 1
        assert version.as_of == END
        assert version.state == rec.state
        assert version.price == rec.current_price
        assert version.change_summary == "Initial recommendation"
        assert version.rules_result

    # A second pass must not duplicate or rewrite the live recommendations.
    again = msvc.generate_recommendations(["NIFTY_50"], END)
    assert again.items_processed == 0
    # Every recommendation still has exactly one (initial) version.
    assert _count(sqlite_session, Recommendation) == _count(sqlite_session, RecommendationVersion)
    assert all(v.version == 1 for v in sqlite_session.scalars(select(RecommendationVersion)))
    assert _count(sqlite_session, Recommendation) > 3  # catalog strategies qualify too
