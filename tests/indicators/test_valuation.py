from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nmi.indicators.valuation import (
    ValuationRules,
    compute_valuation_metrics,
)
from tests.conftest import make_candles


class Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Div:
    def __init__(self, ex_date, amount):
        self.ex_date = ex_date
        self.dividend_amount = amount


def _income(period_end, eps, net_profit, ebitda, shares=100):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        total_revenue=400,
        net_profit=net_profit,
        eps=eps,
        ebitda=ebitda,
        ebitda_margin_pct=None,
        net_margin_pct=None,
        shares_outstanding=shares,
    )


def _balance(period_end, net_worth=400, net_debt=20):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        total_assets=500,
        total_debt=100,
        net_debt=net_debt,
        net_worth=net_worth,
        current_assets=200,
        current_liabilities=100,
    )


def _cash(period_end, fcf=50):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        operating_cash_flow=80,
        free_cash_flow=fcf,
    )


def test_multiples_use_as_of_fundamentals_no_lookahead():
    income = [
        _income(date(2023, 3, 31), eps=2.0, net_profit=40, ebitda=90),
        _income(date(2024, 3, 31), eps=4.0, net_profit=80, ebitda=160),
    ]
    balance = [_balance(date(2024, 3, 31))]
    cash = [_cash(date(2024, 3, 31), fcf=50)]
    candles = make_candles("X", closes=[20.0] * 20, start=date(2023, 6, 1))
    candles += make_candles("X", closes=[30.0] * 20, start=date(2024, 6, 1))

    rows = compute_valuation_metrics(candles, income, balance, cash)
    day_2023 = next(r for r in rows if r["as_of"] < date(2024, 1, 1))
    day_2024 = next(r for r in rows if r["as_of"] > date(2024, 6, 1))

    assert day_2023["pe"] == pytest.approx(10.0)  # 20 / eps FY23(2)
    assert day_2024["pe"] == pytest.approx(7.5)  # 30 / eps FY24(4)


def test_basic_multiples_and_dividend_yield():
    income = [
        _income(date(2023, 3, 31), eps=3.0, net_profit=60, ebitda=120),
        _income(date(2024, 3, 31), eps=4.0, net_profit=80, ebitda=160),
    ]
    balance = [_balance(date(2024, 3, 31), net_worth=400, net_debt=20)]
    cash = [_cash(date(2024, 3, 31))]
    div = [Div(date(2024, 6, 20), Decimal("2.0"))]
    candles = make_candles("X", closes=[40.0] * 30, start=date(2024, 6, 1))

    rows = compute_valuation_metrics(candles, income, balance, cash, dividends=div)
    last = rows[-1]
    assert last["pe"] == pytest.approx(10.0)  # 40 / 4
    assert last["pb"] == pytest.approx(10.0)  # 40 / (400/100)
    assert last["ev_ebitda"] == pytest.approx(25.125)  # (4000+20)/160
    assert last["peg"] == pytest.approx(0.3)  # 10 / 33.3%
    assert last["fcf_yield"] == pytest.approx(1.25)
    assert last["dividend_yield"] == pytest.approx(5.0)


def test_label_uses_absolute_rule_when_no_percentile_history():
    income = [
        _income(date(2024, 3, 31), eps=4.0, net_profit=80, ebitda=160),
    ]
    balance = [_balance(date(2024, 3, 31))]
    candles = make_candles("X", closes=[40.0] * 5, start=date(2024, 6, 1))
    rules = ValuationRules(min_percentile_sample=10)
    rows = compute_valuation_metrics(candles, income, balance, [], rules=rules)
    assert rows[-1]["pe"] == 10.0
    assert rows[-1]["pe_percentile_3y"] is None
    assert rows[-1]["valuation_label"] == "CHEAP"


def test_label_uses_percentile_on_rising_pe_series():
    income = [_income(date(2024, 3, 31), eps=1.0, net_profit=20, ebitda=40)]
    balance = [_balance(date(2024, 3, 31))]
    # price runs 10 -> 60 in 45 sessions => pe runs 10 -> 60
    closes = [10 + (50 / 44) * i for i in range(45)]
    rows = compute_valuation_metrics(
        make_candles("X", closes, start=date(2024, 4, 1)),
        income,
        balance,
        [],
    )
    last = rows[-1]
    assert last["pe_percentile_3y"] == pytest.approx(100.0)
    assert last["valuation_label"] == "EXPENSIVE"
    early = rows[5]
    assert early["pe_percentile_3y"] is None  # not enough history yet


def test_no_fundamentals_defaults_to_fairly_valued():
    rows = compute_valuation_metrics(make_candles("X", [10.0] * 10), [], [], [])
    assert rows[-1]["pe"] is None
    assert rows[-1]["valuation_label"] == "FAIRLY_VALUED"
