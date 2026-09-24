from __future__ import annotations

from datetime import date

from nmi.core.enums import CorporateActionType
from nmi.ingestion.providers.csv_provider import (
    CSVCorporateActionProvider,
    CSVFundamentalProvider,
    CSVMembershipProvider,
    CSVPriceProvider,
    CSVUniverseProvider,
)
from tests.conftest import FIXTURES


def test_csv_price_provider_reads_history(pointed_at_fixtures):
    provider = CSVPriceProvider(prices_dir=FIXTURES / "prices")
    candles = provider.fetch_prices("RELIANCE")
    assert len(candles) == 20
    assert candles[0].trade_date == date(2024, 6, 3)
    assert candles[-1].trade_date == date(2024, 6, 28)
    closes = [c.close for c in candles]
    assert max(closes, default=0) >= 2440  # pre-split zone present


def test_csv_price_provider_honours_date_range(pointed_at_fixtures):
    provider = CSVPriceProvider(prices_dir=FIXTURES / "prices")
    candles = provider.fetch_prices(
        "RELIANCE", start=date(2024, 6, 10), end=date(2024, 6, 14)
    )
    assert len(candles) == 5
    assert all(date(2024, 6, 10) <= c.trade_date <= date(2024, 6, 14) for c in candles)


def test_csv_membership_provider(pointed_at_fixtures):
    provider = CSVMembershipProvider(membership_dir=FIXTURES / "memberships")
    all_rows = provider.fetch_membership()
    assert len(all_rows) == 5
    filtered = provider.fetch_membership(index_codes=["NIFTY_50"])
    assert {r.symbol for r in filtered} == {"RELIANCE", "TCS", "INFY"}
    tft = [
        r
        for r in all_rows
        if r.symbol == "TORNTPOWER"
        and r.index_code == "NIFTY_MIDCAP_150"
        and r.effective_to is not None
    ]
    assert tft and tft[0].effective_to == date(2024, 12, 31)


def test_csv_universe_provider(pointed_at_fixtures):
    rows = CSVUniverseProvider(symbols_file=FIXTURES / "symbols.csv").fetch_universe()
    assert len(rows) == 4
    isins = {r.isin for r in rows}
    assert "INE002A01018" in isins


def test_csv_corporate_action_provider(pointed_at_fixtures):
    provider = CSVCorporateActionProvider(actions_dir=FIXTURES / "corporate_actions")
    actions = provider.fetch_actions(symbols=["RELIANCE", "TCS"])
    by_symbol = {a.symbol: a for a in actions}
    assert by_symbol["RELIANCE"].action_type == CorporateActionType.SPLIT
    assert by_symbol["RELIANCE"].ratio_numerator == 2
    assert by_symbol["TCS"].dividend_amount == 9
    assert by_symbol["TCS"].source_timestamp is not None


def test_csv_fundamental_provider(pointed_at_fixtures):
    provider = CSVFundamentalProvider(fundamentals_dir=FIXTURES / "fundamentals")
    income = provider.fetch_income_statements(isins=["INE002A01018"])
    assert len(income) == 2
    assert income[0].period_end == date(2024, 3, 31)
    assert income[0].total_revenue is not None
    balance = provider.fetch_balance_sheets(isins=["INE002A01018"])
    assert len(balance) == 1
    cash = provider.fetch_cash_flows(isins=["INE002A01018"])
    assert len(cash) == 1
