"""Valuation engine (Phase 2).

Computes per-day valuation multiples for a single instrument using strict
as-of fundamentals (no look-ahead): income/balance/cash rows with period_end
before the valuation date, and dividends already ex-dated. Multiples are
reported raw plus historical (3y lookback) percentile/median context, which
drives the ValuationLabel via explicit, configurable rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from nmi.indicators.fundamental import _annual_rows, _latest_at_or_before
from nmi.indicators.types import BalanceLike, CashFlowLike, IncomeLike

_LOOKBACK_DAYS = 365 * 3


class ValuationRules:
    def __init__(
        self,
        cheap_below: float = 0.20,
        fairly_below: float = 0.40,
        moderately_below: float = 0.70,
        cheap_pe_below: float = 15.0,
        fairly_pe_below: float = 25.0,
        moderately_pe_below: float = 35.0,
        min_percentile_sample: int = 10,
    ) -> None:
        self.cheap_below = cheap_below
        self.fairly_below = fairly_below
        self.moderately_below = moderately_below
        self.cheap_pe_below = cheap_pe_below
        self.fairly_pe_below = fairly_pe_below
        self.moderately_pe_below = moderately_pe_below
        self.min_percentile_sample = min_percentile_sample


def _label(pe: float | None, pe_pct: float | None, rules: ValuationRules) -> str:
    if pe_pct is not None:
        if pe_pct < rules.cheap_below:
            return "CHEAP"
        if pe_pct < rules.fairly_below:
            return "FAIRLY_VALUED"
        if pe_pct < rules.moderately_below:
            return "MODERATELY_EXPENSIVE"
        return "EXPENSIVE"
    if pe is not None:
        if pe < rules.cheap_pe_below:
            return "CHEAP"
        if pe < rules.fairly_pe_below:
            return "FAIRLY_VALUED"
        if pe < rules.moderately_pe_below:
            return "MODERATELY_EXPENSIVE"
        return "EXPENSIVE"
    return "FAIRLY_VALUED"


def _percentile(value: float, history: list[float], min_sample: int) -> float | None:
    if len(history) < min_sample:
        return None
    return (sum(1 for v in history if v <= value) / len(history)) * 100


def compute_valuation_metrics(
    candles: Sequence,
    income: Sequence[IncomeLike],
    balance: Sequence[BalanceLike],
    cash: Sequence[CashFlowLike],
    dividends: Sequence | None = None,
    rules: ValuationRules | None = None,
) -> list[dict]:
    rules = rules or ValuationRules()
    if not candles:
        return []

    inc = _annual_rows(income)
    bal = _annual_rows(balance)
    csh = _annual_rows(cash)
    divs = [
        (d.ex_date, float(d.dividend_amount))
        for d in (dividends or [])
        if getattr(d, "dividend_amount", None) is not None
    ]
    divs.sort(key=lambda x: (x[0], x[1]))

    closes = sorted(
        [
            (
                c.trade_date,
                float(c.adjusted_close)
                if getattr(c, "adjusted_close", None) is not None
                else float(c.close),
            )
            for c in candles
        ],
        key=lambda x: x[0],
    )

    def _window(history: list[float], as_of) -> list[float]:
        cutoff = as_of - timedelta(days=_LOOKBACK_DAYS)
        return [v for (d, v) in history if d >= cutoff]

    pe_history: list[tuple[datetime, float]] = []
    pb_history: list[tuple[datetime, float]] = []
    ev_history: list[tuple[datetime, float]] = []

    rows: list[dict] = []
    for as_of, price in closes:
        inc_row = _latest_at_or_before(inc, as_of)
        eps = (
            float(inc_row.eps) if inc_row is not None and getattr(inc_row, "eps", None)
            else None
        )
        net_profit = (
            float(inc_row.net_profit)
            if inc_row is not None and getattr(inc_row, "net_profit", None)
            else None
        )
        ebitda = (
            float(inc_row.ebitda)
            if inc_row is not None and getattr(inc_row, "ebitda", None)
            else None
        )
        shares = (
            int(inc_row.shares_outstanding)
            if inc_row is not None and getattr(inc_row, "shares_outstanding", None)
            else None
        )
        if shares is None and eps and net_profit:
            shares = net_profit / eps

        bal_row = _latest_at_or_before(bal, as_of) if bal else None
        net_worth = (
            float(bal_row.net_worth)
            if bal_row is not None and getattr(bal_row, "net_worth", None)
            else None
        )
        net_debt = (
            float(bal_row.net_debt)
            if bal_row is not None and getattr(bal_row, "net_debt", None)
            else None
        )
        if net_debt is None and bal_row is not None and getattr(bal_row, "total_debt", None):
            net_debt = float(bal_row.total_debt)

        csh_row = _latest_at_or_before(csh, as_of) if csh else None
        fcf = (
            float(csh_row.free_cash_flow)
            if csh_row is not None and getattr(csh_row, "free_cash_flow", None)
            else None
        )

        mcap = price * shares if shares else None
        bps = net_worth / shares if (net_worth is not None and shares) else None
        ev = (mcap + net_debt) if (mcap is not None and net_debt is not None) else None

        pe = price / eps if (eps and eps > 0) else None
        pb = price / bps if (bps and bps > 0) else None
        ev_ebitda = ev / ebitda if (ev is not None and ebitda and ebitda > 0) else None

        peg = None
        if inc_row is not None and len(inc) >= 2:
            prev_inc = _latest_at_or_before(inc, inc_row.period_end - timedelta(days=1))
            if prev_inc is not None and getattr(prev_inc, "eps", None) and eps and pe:
                g = (eps / float(prev_inc.eps) - 1) * 100
                if g > 0:
                    peg = pe / g

        recent_div = sum(v for d, v in divs if as_of - timedelta(days=365) <= d <= as_of)
        div_yield = recent_div / price * 100 if price > 0 else None
        fcf_yield = fcf / mcap * 100 if (fcf is not None and mcap) else None

        # Percentile compute must happen BEFORE appending this day.
        pe_pct = None
        pe_median = None
        pe_dev = None
        if pe is not None:
            hist = _window(pe_history, as_of)
            pe_pct = _percentile(pe, hist, rules.min_percentile_sample)
            if pe_pct is not None:
                s = sorted(hist)
                pe_median = s[len(s) // 2]
                if pe_median:
                    pe_dev = (pe / pe_median - 1) * 100
        pb_pct = (
            _percentile(pb, _window(pb_history, as_of), rules.min_percentile_sample)
            if pb is not None
            else None
        )
        ev_pct = (
            _percentile(ev_ebitda, _window(ev_history, as_of), rules.min_percentile_sample)
            if ev_ebitda is not None
            else None
        )

        if pe is not None:
            pe_history.append((as_of, pe))
        if pb is not None:
            pb_history.append((as_of, pb))
        if ev_ebitda is not None:
            ev_history.append((as_of, ev_ebitda))

        rows.append(
            {
                "as_of": as_of,
                "pe": pe,
                "pb": pb,
                "ev_ebitda": ev_ebitda,
                "peg": peg,
                "dividend_yield": div_yield,
                "fcf_yield": fcf_yield,
                "pe_median_3y": pe_median,
                "pe_percentile_3y": pe_pct,
                "pe_deviation_pct": pe_dev,
                "pb_percentile_3y": pb_pct,
                "ev_ebitda_percentile_3y": ev_pct,
                "valuation_label": _label(pe, pe_pct, rules),
            }
        )
    return rows
