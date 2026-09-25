from __future__ import annotations

from datetime import date

import pytest

from nmi.core.models import RecommendationState, SignalType
from nmi.strategies import (
    MT_MOMENTUM_QUALITY,
    StrategyContext,
    StrategyMeta,
    StrategyRules,
    evaluate_strategy,
)

AS_OF = date(2024, 6, 28)
META = StrategyMeta(
    code="mt_momentum_quality",
    name="Medium-term momentum + quality",
    horizon="MEDIUM_TERM",
    version=1,
)


def _ctx(**overrides) -> StrategyContext:
    values = {
        "composite_score": 70.0,
        "trend_score": 65.0,
        "momentum_score": 60.0,
        "rs_trend": "IMPROVING",
        "valuation_score": 60.0,
        "risk_score": 55.0,
        "preferred_horizon": "MEDIUM_TERM",
        "regime_label": "RISK_ON",
        "regime_score": 70.0,
        "sector_state": "LEADING",
        "sector_score": 65.0,
        "trend_state": "UPTREND",
        "drawdown_pct": -3.0,
        "score_label": "GOOD",
        "sma20": 98.0,
        "sma50": 95.0,
        "high_52w": 105.0,
    }
    values.update(overrides)
    return StrategyContext(
        instrument_id=1,
        as_of=AS_OF,
        values=values,
        close=overrides.get("close", 100.0),
        atr14=overrides.get("atr14", 2.0),
    )


def _evaluate(ctx=None, meta=META, rules=MT_MOMENTUM_QUALITY.rules):
    return evaluate_strategy(meta or META, rules, ctx or _ctx())


def test_entry_conditions_produce_a_potential_entry_above_the_pullback_zone():
    decision = _evaluate()
    assert decision.signal_type == SignalType.BUY_SETUP
    assert decision.state == RecommendationState.POTENTIAL_ENTRY  # close 100 > zone top
    assert decision.trigger == "entry"
    assert decision.entry_low == pytest.approx(97.0)  # SMA20 − 0.5 ATR
    assert decision.entry_high == pytest.approx(98.5)  # SMA20 + 0.25 ATR
    assert decision.invalidation_price == pytest.approx(92.0)
    assert decision.target_low == pytest.approx(105.0)
    assert decision.target_high == pytest.approx(114.75)
    assert decision.expected_holding_days_min == 60
    assert decision.expected_holding_days_max == 270


def test_close_inside_the_anchored_zone_is_an_entry():
    decision = _evaluate(_ctx(close=98.0))
    assert decision.signal_type == SignalType.BUY_SETUP
    assert decision.state == RecommendationState.ENTRY
    assert decision.current_price == 98.0
    # A pullback below the zone is not an entry either; the zone must be hit.
    below = _evaluate(_ctx(close=95.0))
    assert below.state == RecommendationState.POTENTIAL_ENTRY


def test_invalidation_and_exit_take_precedence_over_entry():
    invalidated = _evaluate(_ctx(risk_score=20.0))
    assert invalidated.signal_type == SignalType.EXIT
    assert invalidated.state == RecommendationState.EXIT
    assert invalidated.trigger == "invalidation"

    exiting = _evaluate(_ctx(regime_label="STRESSED"))
    assert exiting.signal_type == SignalType.EXIT_WARNING
    assert exiting.state == RecommendationState.EXIT_REVIEW
    assert exiting.trigger == "exit"


def test_watch_hold_and_no_match_states():
    watch_only = _evaluate(_ctx(composite_score=52.0, trend_score=40.0, momentum_score=40.0,
                                rs_trend="DETERIORATING", valuation_score=40.0,
                                risk_score=40.0))
    assert watch_only.signal_type == SignalType.WATCH
    assert watch_only.state == RecommendationState.WATCH

    hold = _evaluate(_ctx(composite_score=45.0, trend_score=50.0, momentum_score=40.0,
                          rs_trend="DETERIORATING", valuation_score=40.0, risk_score=40.0))
    assert hold.signal_type == SignalType.HOLD
    assert hold.state == RecommendationState.HOLD

    nothing = evaluate_strategy(
        META,
        StrategyRules.from_dict(
            {
                "entry": {
                    "conditions": [
                        {"key": "composite_score", "op": ">=", "value": 95.0,
                         "label": "Composite score"}
                    ],
                    "mode": "all",
                }
            }
        ),
        _ctx(),
    )
    assert nothing.signal_type == SignalType.REDUCE
    assert nothing.state == RecommendationState.THESIS_WEAKENING
    assert nothing.trigger is None
    assert "No rule group matched" in nothing.reasons[0]


def test_missing_context_never_fabricates_a_signal():
    empty = StrategyContext(instrument_id=1, as_of=AS_OF, values={}, close=None, atr14=None)
    decision = _evaluate(empty)
    assert decision.signal_type == SignalType.REDUCE
    assert decision.state == RecommendationState.THESIS_WEAKENING
    assert decision.entry_low is None
    assert decision.invalidation_price is None
    assert decision.target_low is None
    assert decision.confidence is None
    statuses = {
        c["status"] for group in decision.rules_result for c in group["conditions"]
    }
    assert statuses == {"unknown"}


def test_confidence_blends_composite_regime_and_sector_scores():
    full = _evaluate()
    assert full.confidence == pytest.approx((70.0 + 70.0 + 65.0) / 3, abs=1e-4)
    no_sector = _evaluate(_ctx(sector_state=None, sector_score=None))
    assert no_sector.confidence == pytest.approx(70.0, abs=1e-4)


def test_reasons_and_thesis_explain_the_decision():
    decision = _evaluate()
    joined = " | ".join(decision.reasons)
    assert "Entry conditions met (8/8)" in joined
    assert "Composite score: 70.0 >= 60.0 -> pass" in joined
    assert "Market regime RISK_ON" in joined
    assert "Sector LEADING" in joined
    assert "Risk level MODERATE" in joined

    thesis = decision.thesis
    assert "Medium-term momentum + quality" in thesis
    assert "mt_momentum_quality v1" in thesis
    assert "BUY_SETUP" in thesis
    assert "Entry zone 97.00-98.50" in thesis
    assert "anchored to sma20" in thesis
    assert "Invalidation 92.00" in thesis
    assert "expected holding 60-270 days" in thesis
    assert "Generated" not in thesis  # no AI-generated text


def test_rules_result_records_every_group_and_condition():
    decision = _evaluate()
    names = [group["name"] for group in decision.rules_result]
    assert names == ["entry", "watch", "hold", "exit", "invalidation"]
    entry = next(g for g in decision.rules_result if g["name"] == "entry")
    assert entry["passed"] is True
    assert entry["passes"] == entry["required"] == 8
    assert all(c["status"] == "pass" for c in entry["conditions"])


def test_signal_columns_are_ready_for_persistence():
    columns = _evaluate().as_signal_columns()
    assert columns["signal_type"] == "BUY_SETUP"
    assert columns["state"] == "POTENTIAL_ENTRY"
    assert columns["horizon"] == "MEDIUM_TERM"
    assert columns["risk_level"] == "MODERATE"
    assert columns["price"] == 100.0
    assert columns["thesis"]
    assert columns["reasons"]
    assert isinstance(columns["rules_result"], list)


def test_engine_evaluates_any_rule_document_not_just_the_catalog():
    lenient = StrategyRules.from_dict(
        {
            "entry": {
                "conditions": [{"key": "composite_score", "op": ">=", "value": 0.0,
                                "label": "Composite"}],
                "mode": "all",
            }
        }
    )
    decision = evaluate_strategy(
        StrategyMeta("any", "Any", "SHORT_TERM", 7), lenient, _ctx(composite_score=12.0)
    )
    assert decision.signal_type == SignalType.BUY_SETUP
    assert decision.horizon == "SHORT_TERM"
