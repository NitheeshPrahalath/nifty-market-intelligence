from __future__ import annotations

from datetime import date

import pytest

from nmi.indicators.fundamental import compute_fundamental_metrics


class Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _income(period_end, revenue, net_profit, eps, ebitda, shares=1000, extras=None):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        total_revenue=revenue,
        net_profit=net_profit,
        eps=eps,
        ebitda=ebitda,
        ebitda_margin_pct=None,
        net_margin_pct=None,
        shares_outstanding=shares,
        extras=extras,
    )


def _balance(period_end, net_worth, total_debt=50, net_debt=20, total_assets=500, ca=200, cl=100):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        total_assets=total_assets,
        total_debt=total_debt,
        net_debt=net_debt,
        net_worth=net_worth,
        current_assets=ca,
        current_liabilities=cl,
    )


def _cash(period_end, ocf=80, fcf=40):
    return Row(
        period_end=period_end,
        period_type="ANNUAL",
        operating_cash_flow=ocf,
        free_cash_flow=fcf,
    )


def _series():
    income = [
        _income(date(2021, 3, 31), 500, 60, 3.0, 120),
        _income(date(2022, 3, 31), 600, 72, 3.6, 150),
        _income(date(2023, 3, 31), 720, 90, 4.5, 190),
    ]
    balance = [
        _balance(date(2022, 3, 31), 400),
        _balance(date(2023, 3, 31), 500),
    ]
    cash = [
        _cash(date(2022, 3, 31), 80, 40),
        _cash(date(2023, 3, 31), 110, 60),
    ]
    return income, balance, cash


def test_growth_roe_and_margins():
    income, balance, cash = _series()
    rows = compute_fundamental_metrics(income, balance, cash)
    by = {(r["metric"], r["as_of"]): r["value"] for r in rows}

    assert by[("revenue_growth_pct", date(2023, 3, 31))] == pytest.approx(20.0)
    assert by[("eps_growth_pct", date(2023, 3, 31))] == pytest.approx(25.0)
    # FY23: roe = 90 / 500
    assert by[("roe_pct", date(2023, 3, 31))] == pytest.approx(18.0)
    assert by[("net_margin_pct", date(2023, 3, 31))] == pytest.approx(12.5)
    assert by[("debt_to_equity", date(2023, 3, 31))] == pytest.approx(0.04)

    # Every quantity is keyed "as-of" the statement period.
    assert {r["as_of"] for r in rows} == {date(2021, 3, 31), date(2022, 3, 31), date(2023, 3, 31)}
    assert all(r["source"] == "engine" for r in rows)


def test_quality_scores_and_overall():
    income, balance, cash = _series()
    rows = compute_fundamental_metrics(income, balance, cash)
    by = {(r["metric"], r["as_of"]): r["value"] for r in rows}
    assert 0 <= by[("profit_consistency_quality", date(2023, 3, 31))] <= 100
    assert by[("growth_consistency_quality", date(2023, 3, 31))] == 100.0
    assert by[("overall_quality_score", date(2023, 3, 31))] is not None


def test_missing_financials_yield_none_not_crash():
    income = [_income(date(2023, 3, 31), 100, None, None, None)]
    rows = compute_fundamental_metrics(income, [], [])
    assert rows
    assert all(r["value"] is None for r in rows)


def test_first_year_has_no_growth():
    income, balance, cash = _series()
    rows = compute_fundamental_metrics(income, balance, cash)
    by = {(r["metric"], r["as_of"]): r["value"] for r in rows}
    assert by[("revenue_growth_pct", date(2021, 3, 31))] is None
