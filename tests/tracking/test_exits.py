"""Exit engine (Phase 5): every mechanism fires on its own evidence."""

from __future__ import annotations

from datetime import date

from nmi.core.models import ExitMechanism, RecommendationState
from nmi.tracking import (
    ExitAction,
    ExitContext,
    ExitPolicy,
    ExitTrigger,
    evaluate_exits,
)

AS_OF = date(2024, 6, 28)

BASELINE = {
    "composite_score": 70.0,
    "growth_score": 60.0,
    "quality_score": 65.0,
    "eps_growth_pct": 20.0,
    "valuation_label": "FAIRLY_VALUED",
    "trend_state": "UPTREND",
}

VALUES = {
    **BASELINE,
    "risk_score": 70.0,
    "pe_percentile_3y": 40.0,
    "sma50": 950.0,
    "sma200": 900.0,
}


def _ctx(
    *,
    values: dict | None = None,
    baseline: dict | None = None,
    **overrides,
) -> ExitContext:
    base: dict = {
        "as_of": AS_OF,
        "price": 1000.0,
        "invalidation_price": 900.0,
        "entry_low": 950.0,
        "entry_high": 980.0,
        "target_low": 1200.0,
        "target_high": 1250.0,
        "entry_price": 960.0,
        "expected_holding_days_max": 60,
        "baseline_factors": dict(BASELINE),
        "values": dict(VALUES),
    }
    base.update(overrides)
    if values is not None:
        base["values"] = {**VALUES, **values}
    if baseline is not None:
        base["baseline_factors"] = {**BASELINE, **baseline}
    return ExitContext(**base)


def _of_mechanism(triggers: list[ExitTrigger], mechanism: ExitMechanism) -> list[ExitTrigger]:
    return [t for t in triggers if t.mechanism is mechanism]


def test_healthy_recommendation_fires_nothing():
    assert evaluate_exits(_ctx()) == []


def test_invalidation_breach_is_a_risk_exit():
    triggers = _of_mechanism(evaluate_exits(_ctx(price=880.0)), ExitMechanism.RISK)
    assert triggers
    assert all(t.action is ExitAction.EXIT for t in triggers)
    assert any("invalidation" in t.reason.lower() for t in triggers)
    assert triggers[0].detail["invalidation_price"] == 900.0


def test_risk_score_below_the_floor_exits():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"risk_score": 20.0})), ExitMechanism.RISK
    )
    assert any("risk score" in t.reason.lower() for t in triggers)
    assert triggers[0].detail["floor"] == 35.0


def test_drawdown_beyond_the_limit_exits():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"drawdown_pct": -16.7})), ExitMechanism.RISK
    )
    assert triggers
    assert "drawdown" in triggers[0].reason.lower()
    assert triggers[0].detail["drawdown_pct"] == -16.7


def test_policy_thresholds_are_honoured():
    lenient = evaluate_exits(
        _ctx(values={"risk_score": 40.0, "drawdown_pct": -8.0}),
        policy=ExitPolicy(risk_score_floor=10.0, max_drawdown_pct=-25.0),
    )
    assert not _of_mechanism(lenient, ExitMechanism.RISK)


def test_death_cross_is_a_technical_exit():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"sma50": 900.0, "sma200": 1000.0})), ExitMechanism.TECHNICAL
    )
    assert triggers
    assert "death cross" in triggers[0].reason.lower()
    assert triggers[0].detail == {"sma50": 900.0, "sma200": 1000.0}


def test_downtrend_below_sma200_is_a_technical_exit():
    triggers = _of_mechanism(
        evaluate_exits(
            _ctx(
                values={"trend_state": "DOWNTREND", "sma50": 1000.0, "sma200": 1000.0},
                price=880.0,
            )
        ),
        ExitMechanism.TECHNICAL,
    )
    assert triggers
    assert "DOWNTREND" in triggers[0].reason
    assert triggers[0].detail["price_vs_sma200"] == "BELOW_SMA200"


def test_uptrend_above_sma200_fires_no_technical_exit():
    triggers = evaluate_exits(_ctx(values={"trend_state": "UPTREND"}))
    assert not _of_mechanism(triggers, ExitMechanism.TECHNICAL)


def test_eps_growth_deterioration_is_a_fundamental_exit():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"eps_growth_pct": 5.0})), ExitMechanism.FUNDAMENTAL
    )
    assert triggers
    assert "eps growth" in triggers[0].reason.lower()
    assert triggers[0].detail == {"factor": "eps_growth_pct", "before": 20.0, "after": 5.0}


def test_score_drops_are_fundamental_exits():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"growth_score": 35.0, "quality_score": 40.0})),
        ExitMechanism.FUNDAMENTAL,
    )
    factors = {t.detail["factor"] for t in triggers}
    assert factors == {"growth_score", "quality_score"}
    assert all(t.action is ExitAction.EXIT for t in triggers)


def test_small_score_noise_does_not_fire():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(values={"growth_score": 52.0})), ExitMechanism.FUNDAMENTAL
    )
    assert triggers == []


def test_valuation_percentile_is_review_then_exit():
    review = _of_mechanism(
        evaluate_exits(_ctx(values={"pe_percentile_3y": 92.0})), ExitMechanism.VALUATION
    )
    assert review and review[0].action is ExitAction.REVIEW
    assert review[0].detail["pe_percentile_3y"] == 92.0

    extreme = _of_mechanism(
        evaluate_exits(_ctx(values={"pe_percentile_3y": 99.5})), ExitMechanism.VALUATION
    )
    assert extreme and extreme[0].action is ExitAction.EXIT


def test_valuation_label_without_percentile_still_reviews():
    triggers = _of_mechanism(
        evaluate_exits(
            _ctx(values={"pe_percentile_3y": None, "valuation_label": "EXPENSIVE"})
        ),
        ExitMechanism.VALUATION,
    )
    assert triggers and triggers[0].action is ExitAction.REVIEW
    assert "EXPENSIVE" in triggers[0].reason


def test_target_zone_is_a_review_not_an_exit():
    reached = _of_mechanism(evaluate_exits(_ctx(price=1260.0)), ExitMechanism.TARGET)
    assert reached
    assert all(t.action is ExitAction.REVIEW for t in reached)
    assert "target zone" in reached[0].reason.lower()
    assert reached[0].detail["target_high"] == 1250.0

    partial = _of_mechanism(evaluate_exits(_ctx(price=1230.0)), ExitMechanism.TARGET)
    assert "1R review zone" in partial[0].reason


def test_target_mechanism_is_silent_below_the_zone():
    assert not _of_mechanism(evaluate_exits(_ctx(price=1100.0)), ExitMechanism.TARGET)


def test_time_exit_requires_no_development():
    developed = evaluate_exits(
        _ctx(sessions_since_active=90, baseline_composite=70.0, current_composite=75.0)
    )
    assert not _of_mechanism(developed, ExitMechanism.TIME)

    stalled = evaluate_exits(
        _ctx(sessions_since_active=90, baseline_composite=70.0, current_composite=50.0)
    )
    time_triggers = _of_mechanism(stalled, ExitMechanism.TIME)
    assert time_triggers
    assert time_triggers[0].action is ExitAction.EXIT
    assert time_triggers[0].detail["sessions_since_entry"] == 90


def test_time_exit_can_be_disabled_or_relaxed():
    triggered = evaluate_exits(
        _ctx(
            sessions_since_active=90,
            baseline_composite=70.0,
            current_composite=50.0,
        ),
        policy=ExitPolicy(time_exit_requires_no_development=False),
    )
    assert _of_mechanism(triggered, ExitMechanism.TIME)
    not_yet = evaluate_exits(
        _ctx(sessions_since_active=30, baseline_composite=70.0, current_composite=50.0)
    )
    assert not _of_mechanism(not_yet, ExitMechanism.TIME)


def test_material_corporate_actions_exit_but_routine_ones_do_not():
    triggers = _of_mechanism(
        evaluate_exits(
            _ctx(
                corporate_actions=[
                    {
                        "action_type": "BONUS",
                        "ex_date": AS_OF,
                        "description": "1:1 bonus issue",
                    },
                    {"action_type": "DIVIDEND", "ex_date": AS_OF, "description": "final dividend"},
                ]
            )
        ),
        ExitMechanism.EVENT,
    )
    assert len(triggers) == 1
    assert "BONUS" in triggers[0].reason
    assert triggers[0].detail["action_type"] == "BONUS"


def test_strategy_exit_state_becomes_a_trigger_with_its_own_reason():
    triggers = _of_mechanism(
        evaluate_exits(
            _ctx(
                signal_state=RecommendationState.EXIT,
                signal_reasons=["RS rank collapsed below 20"],
                signal_rules_result=[{"group": "exit", "passed": True, "checks": []}],
            )
        ),
        ExitMechanism.STRATEGY,
    )
    assert triggers
    assert triggers[0].action is ExitAction.EXIT
    assert "RS rank collapsed below 20" in triggers[0].reason
    assert triggers[0].detail["signal_state"] == "EXIT"


def test_strategy_review_state_is_only_a_review():
    triggers = _of_mechanism(
        evaluate_exits(_ctx(signal_state=RecommendationState.EXIT_REVIEW)),
        ExitMechanism.STRATEGY,
    )
    assert triggers and triggers[0].action is ExitAction.REVIEW


def test_strategy_signal_ignores_non_exit_states():
    triggers = evaluate_exits(_ctx(signal_state=RecommendationState.HOLD))
    assert not _of_mechanism(triggers, ExitMechanism.STRATEGY)


def test_exits_are_ordered_and_fully_explainable():
    triggers = evaluate_exits(
        _ctx(
            price=880.0,
            values={"risk_score": 20.0, "drawdown_pct": -22.0, "sma50": 800.0, "sma200": 1000.0},
        )
    )
    mechanisms = [t.mechanism for t in triggers]
    assert ExitMechanism.RISK in mechanisms and ExitMechanism.TECHNICAL in mechanisms
    # Every EXIT precedes every REVIEW, and ties are ordered deterministically.
    actions = [t.action for t in triggers]
    assert actions == sorted(actions, key=lambda a: 0 if a is ExitAction.EXIT else 1)
    for trigger in triggers:
        payload = trigger.as_dict()
        assert payload["mechanism"] in {m.value for m in ExitMechanism}
        assert payload["action"] in {a.value for a in ExitAction}
        assert payload["reason"]
        assert isinstance(payload["detail"], dict)


def test_policy_is_serialisable_for_audit():
    payload = ExitPolicy().as_dict()
    assert payload["risk_score_floor"] == 35.0
    assert payload["max_drawdown_pct"] == -15.0
    assert "MERGER" in payload["event_exit_types"]
