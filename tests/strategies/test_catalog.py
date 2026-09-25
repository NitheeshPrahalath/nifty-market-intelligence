from __future__ import annotations

import pytest

from nmi.core.models import HorizonType
from nmi.strategies import DEFAULT_STRATEGIES, StrategyRules, catalog_payloads
from nmi.strategies.rules import price_levels

ANCHORS = {"sma20": 98.0, "high_52w": 105.0, "sma50": 95.0, "close": 100.0}


def test_catalog_ships_three_unique_strategies():
    codes = [definition.code for definition in DEFAULT_STRATEGIES]
    assert codes == ["mt_momentum_quality", "st_breakout_momentum", "lt_quality_value"]
    assert len(set(codes)) == 3
    assert {d.horizon for d in DEFAULT_STRATEGIES} == set(HorizonType)


def test_every_strategy_rule_document_roundtrips_and_is_complete():
    for definition in DEFAULT_STRATEGIES:
        document = definition.rules_document()
        rules = StrategyRules.from_dict(document)
        assert rules.entry.conditions, f"{definition.code} needs entry conditions"
        assert rules.exit is not None, f"{definition.code} needs exit conditions"
        assert rules.invalidation is not None, f"{definition.code} needs invalidation rules"
        assert StrategyRules.from_dict(rules.to_dict()).to_dict() == document
        assert 0 < rules.risk.holding_days_min < rules.risk.holding_days_max
        assert rules.risk.target_r > 0
        assert rules.risk.stop_atr > 0
        for group in (rules.entry, rules.exit, rules.invalidation):
            for cond in group.conditions:
                assert cond.label
                assert cond.key


def test_entry_zones_are_sane_for_each_strategy():
    for definition in DEFAULT_STRATEGIES:
        risk = definition.rules.risk
        assert risk.anchor in ANCHORS, f"{definition.code} needs a supported anchor"
        levels = price_levels(ANCHORS[risk.anchor], 2.0, risk)
        low, high = levels["entry_low"], levels["entry_high"]
        assert low is not None and high is not None
        assert low <= high
        # The zone sits within 1 ATR of its anchor: pullbacks below, breakouts above.
        assert abs(low - ANCHORS[risk.anchor]) <= 2.0
        assert abs(high - ANCHORS[risk.anchor]) <= 2.0
        assert levels["invalidation_price"] < low
        assert levels["target_low"] > high


def test_pullback_strategies_anchor_below_and_breakouts_anchor_above():
    by_code = {d.code: d.rules.risk for d in DEFAULT_STRATEGIES}
    assert by_code["mt_momentum_quality"].entry_low_atr > 0  # buy the dip into SMA20
    assert by_code["mt_momentum_quality"].entry_high_atr < 0
    assert by_code["lt_quality_value"].anchor == "sma50"
    st = by_code["st_breakout_momentum"]
    assert st.anchor == "high_52w"
    assert st.entry_low_atr == 0.0 and st.entry_high_atr < 0


def test_catalog_payloads_expose_strategy_rows_and_rule_documents():
    payloads = catalog_payloads()
    assert len(payloads) == len(DEFAULT_STRATEGIES)
    for payload in payloads:
        strategy = payload["strategy"]
        assert set(strategy) == {"code", "name", "description", "horizon", "is_active"}
        assert strategy["is_active"] is True
        assert strategy["horizon"] in {h.value for h in HorizonType}
        assert StrategyRules.from_dict(payload["rules"]).entry.conditions


def test_holding_periods_roughly_follow_each_horizon():
    by_code = {d.code: d.rules.risk for d in DEFAULT_STRATEGIES}
    short = by_code["st_breakout_momentum"]
    medium = by_code["mt_momentum_quality"]
    long = by_code["lt_quality_value"]
    assert short.holding_days_max < medium.holding_days_min
    assert medium.holding_days_max <= long.holding_days_min
    assert (short.holding_days_min, short.holding_days_max) == (10, 45)
    assert (medium.holding_days_min, medium.holding_days_max) == (60, 270)
    assert (long.holding_days_min, long.holding_days_max) == (270, 900)


@pytest.mark.parametrize("definition", DEFAULT_STRATEGIES, ids=lambda d: d.code)
def test_strategy_rows_match_their_definition(definition):
    row = definition.strategy_row()
    assert row["code"] == definition.code
    assert row["name"] == definition.name
    assert row["horizon"] == definition.horizon.value
