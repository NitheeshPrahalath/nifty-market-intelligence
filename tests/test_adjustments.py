from __future__ import annotations

from datetime import date
from decimal import Decimal

from nmi.ingestion.adjustments import (
    adjustment_factors,
    apply_adjustments,
)
from tests.conftest import make_candles, make_dividend, make_split_action


def _dates_of(candles):
    return [c.trade_date for c in candles]


def test_split_2to1_halves_pre_ex_prices():
    closes = [Decimal(x) for x in range(2400, 2400 + 12)]  # 12 flat-ish days
    candles = make_candles("RELIANCE", closes, start=date(2024, 6, 3))
    ex = date(2024, 6, 10)
    split = make_split_action("RELIANCE", ex, num=2, den=1)
    adjusted = apply_adjustments(candles, [split])
    factors, _ = adjustment_factors(candles, [split])

    for c in adjusted:
        if c.trade_date < ex:
            assert c.adjustment_factor == Decimal("0.5")
            assert c.adjusted_close == c.close * Decimal("0.5")
        else:
            assert c.adjustment_factor == Decimal(1)
            assert c.adjusted_close == c.close


def test_split_3to2_uses_inverse_ratio():
    candles = make_candles("TEST", [Decimal(3000)] * 10, start=date(2024, 6, 3))
    split = make_split_action("TEST", date(2024, 6, 10), num=3, den=2)
    adjusted = apply_adjustments(candles, [split])
    pre = [c for c in adjusted if c.trade_date < date(2024, 6, 10)]
    assert pre and all(c.adjusted_close == Decimal("2000") for c in pre)


def test_dividend_scales_pre_ex_prices_by_price_ratio():
    closes = [Decimal(1000)] * 10
    candles = make_candles("TCS", closes, start=date(2024, 6, 3))
    div = make_dividend("TCS", date(2024, 6, 10), Decimal("100"))
    adjusted = apply_adjustments(candles, [div])
    expected_factor = (Decimal(1000) - Decimal(100)) / Decimal(1000)
    pre = [c for c in adjusted if c.trade_date < date(2024, 6, 10)]
    assert pre
    assert pre[0].adjustment_factor == expected_factor
    assert pre[0].adjusted_close == Decimal("900.0000")


def test_bonus_issue_treated_like_share_ratio():
    candles = make_candles("INFY", [Decimal(2000)] * 10, start=date(2024, 6, 3))
    bonus = make_split_action("INFY", date(2024, 6, 10), num=2, den=1, kind="BONUS")
    adjusted = apply_adjustments(candles, [bonus])
    pre = [c for c in adjusted if c.trade_date < date(2024, 6, 10)]
    assert all(c.adjusted_close == Decimal("1000") for c in pre)


def test_combined_actions_compound_factors():
    closes = [Decimal(1000)] * 10
    candles = make_candles("TEST", closes, start=date(2024, 6, 3))
    split = make_split_action("TEST", date(2024, 6, 10), num=2, den=1)
    div = make_dividend("TEST", date(2024, 6, 17), Decimal("100"))
    adjusted = apply_adjustments(candles, [split, div])
    # Before both events: split (0.5) * dividend ratio ((1000-100)/1000=0.9) = 0.45
    pre_both = [c for c in adjusted if c.trade_date < date(2024, 6, 10)]
    assert pre_both
    assert pre_both[0].adjustment_factor == Decimal("0.45")
    # Between split and dividend: only dividend ratio applies.
    between = [
        c
        for c in adjusted
        if date(2024, 6, 10) <= c.trade_date < date(2024, 6, 17)
    ]
    assert between
    assert between[0].adjustment_factor == Decimal("0.9")
    assert between[0].adjusted_close == Decimal("900.0000")


def test_no_actions_leaves_prices_untouched():
    candles = make_candles("TEST", [Decimal(500), Decimal(510)], start=date(2024, 6, 3))
    adjusted = apply_adjustments(candles, [])
    assert all(c.adjustment_factor == Decimal(1) for c in adjusted)
    assert adjusted[0].adjusted_close == Decimal("500.0000")


def test_missing_ratio_flags_unadjusted_with_warning():
    from nmi.core.enums import CorporateActionType as CAT
    from nmi.ingestion.records import CorporateActionRecord

    candles = make_candles("TEST", [Decimal(500)] * 4, start=date(2024, 6, 3))
    bad = CorporateActionRecord(
        symbol="TEST",
        action_type=CAT.SPLIT,
        ex_date=date(2024, 6, 10),
        source="test",
        source_timestamp=None,
    )
    factors, warnings = adjustment_factors(candles, [bad])
    assert all(f == Decimal(1) for f in factors.values())
    assert any("NOT adjusted" in w for w in warnings)


def test_ohlc_adjusted_consistently_with_close():
    closes = [Decimal(x) for x in range(100, 112)]
    candles = make_candles("TEST", closes, start=date(2024, 6, 3))
    split = make_split_action("TEST", date(2024, 6, 10), num=2, den=1)
    adjusted = apply_adjustments(candles, [split])
    pre = [c for c in adjusted if c.trade_date < date(2024, 6, 10)]
    # open/high/low follow the same factor when provided.
    assert pre[0].adjusted_high == pre[0].high * Decimal("0.5")


def test_dividend_missing_amount_warns():
    from nmi.core.enums import CorporateActionType as CAT
    from nmi.ingestion.records import CorporateActionRecord

    candles = make_candles("TEST", [Decimal(500)] * 4, start=date(2024, 6, 3))
    bad = CorporateActionRecord(
        symbol="TEST",
        action_type=CAT.DIVIDEND,
        ex_date=date(2024, 6, 10),
        source="test",
        source_timestamp=None,
    )
    factors, warnings = adjustment_factors(candles, [bad])
    assert all(f == Decimal(1) for f in factors.values())
    assert any("DIVIDEND" in w for w in warnings)
