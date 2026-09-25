from __future__ import annotations

import pytest

from nmi.strategies.rules import (
    LEVEL_KEYS,
    Condition,
    RiskParams,
    RuleGroup,
    StrategyRules,
    condition,
    group,
    price_levels,
)


def _outcomes(values, *conditions, mode="all", min_passes=None):
    rule_group = RuleGroup(
        name="entry",
        conditions=tuple(
            c if isinstance(c, Condition) else Condition.from_dict(c) for c in conditions
        ),
        mode=mode,
        min_passes=min_passes,
    )
    return rule_group.evaluate(values)


def test_numeric_operators_compare_in_every_direction():
    values = {"score": 60.0, "drawdown": -12.0, "flag": True, "label": "RISK_ON"}
    assert _outcomes(values, condition("score", ">=", 60.0, "Score")).passed
    assert _outcomes(values, condition("score", ">", 59.0, "Score")).passed
    assert not _outcomes(values, condition("score", "<", 60.0, "Score")).passed
    assert _outcomes(values, condition("drawdown", "<=", -12.0, "Drawdown")).passed
    assert not _outcomes(values, condition("drawdown", ">", 0.0, "Drawdown")).passed
    assert _outcomes(values, condition("flag", "==", True, "Breakout")).passed
    assert not _outcomes(values, condition("flag", "!=", True, "Breakout")).passed
    assert _outcomes(values, condition("label", "==", "RISK_ON", "Regime")).passed


def test_membership_operators():
    values = {"rs_trend": "IMPROVING", "horizon": "SHORT_TERM"}
    assert _outcomes(values, condition("rs_trend", "in", ["IMPROVING", "STABLE"], "RS")).passed
    assert not _outcomes(
        values, condition("horizon", "in", ["MEDIUM_TERM", "LONG_TERM"], "Horizon")
    ).passed
    assert _outcomes(values, condition("horizon", "not_in", ["LONG_TERM"], "Horizon")).passed


def test_missing_or_incomparable_values_are_unknown_and_never_pass():
    result = _outcomes(
        {},
        condition("composite_score", ">=", 60.0, "Composite"),
        condition("regime_label", ">=", 60.0, "Regime"),
    )
    assert not result.passed
    assert result.passes == 0
    assert all(o.unknown for o in result.outcomes)
    assert all(o.status == "unknown" for o in result.outcomes)


def test_group_modes_and_min_passes():
    values = {"a": 10.0, "b": 1.0, "c": 0.5}
    conds = (
        condition("a", ">=", 5.0, "A"),
        condition("b", ">=", 5.0, "B"),
        condition("c", ">=", 0.0, "C"),
    )
    assert not _outcomes(values, *conds).passed
    assert _outcomes(values, *conds, mode="any").passed
    assert _outcomes(values, *conds, min_passes=2).passed
    assert not _outcomes(values, *conds, min_passes=3).passed


def test_condition_outcome_dict_and_description_are_auditable():
    outcome = _outcomes(
        {"composite": 72.4}, condition("composite", ">=", 60.0, "Composite score")
    ).outcomes[0]
    assert outcome.passed
    assert outcome.as_dict() == {
        "key": "composite",
        "label": "Composite score",
        "op": ">=",
        "threshold": 60.0,
        "actual": 72.4,
        "status": "pass",
    }
    assert "Composite score: 72.4 >= 60.0 -> pass" in outcome.describe()


def test_risk_bands_map_risk_score_to_level():
    risk = RiskParams()
    assert risk.risk_level(80.0) == "LOW"
    assert risk.risk_level(60.0) == "MODERATE"
    assert risk.risk_level(45.0) == "HIGH"
    assert risk.risk_level(10.0) == "VERY_HIGH"
    assert risk.risk_level(None) == "MODERATE"


def test_entry_zone_stop_and_targets_scale_with_atr():
    risk = RiskParams(anchor="sma20", entry_low_atr=0.5, entry_high_atr=-0.25,
                      stop_atr=2.5, target_r=2.0)
    levels = price_levels(100.0, 2.0, risk)
    assert levels["entry_low"] == pytest.approx(99.0)  # 0.5 ATR below the anchor
    assert levels["entry_high"] == pytest.approx(100.5)  # 0.25 ATR above it
    assert levels["invalidation_price"] == pytest.approx(94.0)  # 2.5 ATR below the zone
    risk_per_share = levels["entry_high"] - levels["invalidation_price"]
    assert levels["target_low"] == pytest.approx(levels["entry_high"] + risk_per_share)
    assert levels["target_high"] == pytest.approx(
        levels["entry_high"] + risk_per_share * 2.0
    )


def test_breakout_zone_sits_above_the_anchor():
    risk = RiskParams(anchor="high_52w", entry_low_atr=0.0, entry_high_atr=-0.25,
                      stop_atr=2.0, target_r=2.0)
    levels = price_levels(100.0, 4.0, risk)
    assert levels["entry_low"] == 100.0
    assert levels["entry_high"] == 101.0
    assert levels["invalidation_price"] == 92.0  # 2 ATR below the breakout level
    assert levels["target_low"] == 110.0  # 1R
    assert levels["target_high"] == 119.0  # 2R


def test_levels_are_none_without_anchor_or_volatility():
    risk = RiskParams()
    for levels in (
        price_levels(None, 2.0, risk),
        price_levels(100.0, None, risk),
        price_levels(100.0, 0.0, risk),
    ):
        assert set(levels) == set(LEVEL_KEYS)
        assert all(value is None for value in levels.values())


def test_inverted_zone_yields_no_targets():
    risk = RiskParams(entry_low_atr=-1.0, entry_high_atr=2.0, stop_atr=0.0)
    levels = price_levels(100.0, 2.0, risk)
    assert levels["entry_low"] == 102.0
    assert levels["entry_high"] == 96.0
    assert levels["target_low"] is None
    assert levels["target_high"] is None


def test_anchor_is_preserved_in_the_rule_document():
    risk = RiskParams(anchor="sma50")
    assert risk.to_dict()["anchor"] == "sma50"
    assert RiskParams.from_dict(risk.to_dict()).anchor == "sma50"
    assert RiskParams.from_dict({}).anchor == "close"


def test_strategy_rules_roundtrip_through_a_document():
    document = {
        "entry": group(
            condition("composite_score", ">=", 60.0, "Composite score"),
            condition("regime_label", "in", ["RISK_ON"], "Regime"),
        ),
        "exit": group(condition("risk_score", "<", 30.0, "Risk score"), mode="any"),
        "risk": RiskParams(stop_atr=3.0, holding_days_min=10, holding_days_max=90).to_dict(),
    }
    rules = StrategyRules.from_dict(document)
    assert rules.entry.required_passes == 2
    assert rules.exit.mode == "any"
    assert rules.hold is None
    assert rules.risk.stop_atr == 3.0
    assert rules.risk.risk_bands[0] == (70.0, "LOW")
    assert rules.risk.anchor == "close"

    restored = StrategyRules.from_dict(rules.to_dict())
    assert restored.to_dict() == rules.to_dict()
    assert restored.risk.holding_days_max == 90


def test_rule_documents_reject_missing_entry_or_bad_operator():
    with pytest.raises(ValueError, match="entry group"):
        StrategyRules.from_dict({"watch": group(condition("a", ">=", 1.0, "A"))})
    with pytest.raises(ValueError, match="unsupported operator"):
        Condition.from_dict({"key": "a", "op": "~=", "value": 1.0, "label": "A"})
