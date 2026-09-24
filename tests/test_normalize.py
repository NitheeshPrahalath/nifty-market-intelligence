from __future__ import annotations

from datetime import date
from decimal import Decimal

from nmi.core.enums import Exchange
from nmi.ingestion.normalize import (
    count_duplicates,
    normalize_actions,
    normalize_candles,
    normalize_membership,
)
from tests.conftest import make_candles


def test_normalize_candles_sorts_and_deduplicates_last_wins():
    candles = make_candles("TEST", [Decimal(100), Decimal(101)], start=date(2024, 6, 3))
    duplicate = make_candles("TEST", [Decimal(999)], start=date(2024, 6, 3))[0]
    candles.insert(0, duplicate)  # same date with a different value
    assert count_duplicates(candles) == 1
    normalized = normalize_candles(candles)
    assert len(normalized) == 2
    assert normalized[0].trade_date <= normalized[1].trade_date
    # Later record won for the repeated date.
    assert normalized[0].close == Decimal("100")


def test_normalize_membership_sorts_deterministically():
    from nmi.ingestion.records import IndexMembershipRecord

    records = [
        IndexMembershipRecord(
            index_code="NIFTY_50",
            symbol="TCS",
            exchange=Exchange.NSE,
            effective_from=date(2024, 1, 1),
        ),
        IndexMembershipRecord(
            index_code="NIFTY_50",
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            effective_from=date(2024, 1, 1),
        ),
    ]
    ordered = normalize_membership(records)
    assert [r.symbol for r in ordered] == ["RELIANCE", "TCS"]


def test_normalize_actions_sorts_by_symbol_then_ex_date():
    from nmi.core.enums import CorporateActionType as CAT
    from nmi.ingestion.records import CorporateActionRecord

    recs = [
        CorporateActionRecord(
            symbol="RELIANCE",
            action_type=CAT.SPLIT,
            ex_date=date(2024, 6, 17),
            source="t",
            source_timestamp=None,
        ),
        CorporateActionRecord(
            symbol="TCS",
            action_type=CAT.DIVIDEND,
            ex_date=date(2024, 6, 21),
            source="t",
            source_timestamp=None,
        ),
    ]
    ordered = normalize_actions(recs)
    assert [r.symbol for r in ordered] == ["RELIANCE", "TCS"]
