"""Fundamental metrics & quality engine (Phase 2).

Takes raw annual statements for one company and derives growth, profitability,
leverage and quality metrics. Every output row is keyed on a statement
``period_end`` (the moment the data was knowable), so downstream
as-of calculations never peek into the future.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from nmi.indicators.types import BalanceLike, CashFlowLike, IncomeLike

_ANNUAL = ("ANNUAL", "FY")


def _dec(v, default=None) -> float | None:
    if v is None:
        return default
    return float(v)


def _annual_rows(rows: Sequence) -> list:
    return sorted(
        [r for r in rows if str(getattr(r, "period_type", "")).upper() in _ANNUAL],
        key=lambda r: r.period_end,
    )


def _latest_at_or_before(rows: Sequence, as_of: date):
    best = None
    for r in rows:
        if r.period_end <= as_of:
            best = r
        else:
            break
    return best


def _clamp(v: float | None, lo: float, hi: float) -> float | None:
    if v is None:
        return None
    return max(lo, min(hi, v))


def _pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur / prev - 1) * 100


def compute_fundamental_metrics(
    income: Sequence[IncomeLike],
    balance: Sequence[BalanceLike],
    cash: Sequence[CashFlowLike],
) -> list[dict]:
    inc = _annual_rows(income)
    bal = _annual_rows(balance)
    csh = _annual_rows(cash)
    if not inc:
        return []

    out: list[dict] = []
    revenue = [_dec(r.total_revenue) for r in inc]
    net_profit = [_dec(r.net_profit) for r in inc]
    eps = [_dec(r.eps) for r in inc]
    ebitda = [_dec(r.ebitda) for r in inc]
    ocf = [_dec(r.operating_cash_flow) for r in csh]
    fcf = [_dec(r.free_cash_flow) for r in csh]

    for i, row in enumerate(inc):
        pe = row.period_end
        yy = _clamp(_pct_change(revenue[i], revenue[i - 1]), -500, 500) if i >= 1 else None
        ey = _clamp(_pct_change(eps[i], eps[i - 1]), -1000, 1000) if i >= 1 else None

        b = _latest_at_or_before(bal, pe)
        net_worth = _dec(getattr(b, "net_worth", None)) if b else None
        total_assets = _dec(getattr(b, "total_assets", None)) if b else None
        total_debt = _dec(getattr(b, "total_debt", None)) if b else None
        net_debt = _dec(getattr(b, "net_debt", None)) if b else None
        ca = _dec(getattr(b, "current_assets", None)) if b else None
        cl = _dec(getattr(b, "current_liabilities", None)) if b else None

        if i >= 1 and net_worth is not None and net_worth > 0 and net_profit[i] is not None:
            roe = net_profit[i] / net_worth * 100
        else:
            roe = None
        roa = None
        if net_profit[i] is not None and total_assets:
            roa = net_profit[i] / total_assets * 100
        roce = None
        ce = (net_worth or 0) + (total_debt or 0)
        if ebitda[i] is not None and ce > 0:
            roce = ebitda[i] / ce * 100

        d_e = None
        if net_debt is not None and net_worth:
            d_e = net_debt / net_worth
        elif total_debt is not None and net_worth:
            d_e = total_debt / net_worth

        current_ratio = None
        if ca is not None and cl:
            current_ratio = ca / cl

        interest_score = None
        interest = (
            _dec(row.extras.get("interest_expense"))
            if getattr(row, "extras", None)
            else None
        )
        if interest:
            covered = _dec(getattr(row, "ebitda", None))
            if covered:
                ratio = covered / interest
                if ratio >= 8:
                    interest_score = 90.0
                elif ratio >= 3:
                    interest_score = 70.0
                elif ratio > 1:
                    interest_score = 40.0
                else:
                    interest_score = 15.0

        ocf_np = None
        if ocf and i < len(ocf) and ocf[i] is not None and net_profit[i]:
            ocf_np = ocf[i] / net_profit[i]

        fcf_growth = None
        if i >= 1 and fcf and i < len(fcf) and fcf[i] is not None:
            fcf_growth = _clamp(_pct_change(fcf[i], fcf[i - 1]), -1000, 1000)

        q_cash = None
        if ocf_np is not None:
            q_cash = _clamp(30 + min(ocf_np, 2.0) * 35, 0, 100)

        margins = [
            (rev and np_ and rev != 0 and (np_ / rev * 100)) or 0
            for rev, np_ in zip(revenue, net_profit, strict=False)
            if rev and np_
        ]
        q_margin = None
        if len(margins) >= 3:
            mean_m = sum(margins) / len(margins)
            if mean_m:
                cv = (max(margins) - min(margins)) / mean_m if mean_m else 0
                q_margin = _clamp(100 * (1 - cv / 0.8), 0, 100)

        q_profit = None
        profit_known = [p for p in net_profit if p is not None]
        if len(profit_known) >= 1:
            q_profit = sum(1 for p in profit_known if p > 0) / len(profit_known) * 100

        q_growth = None
        if revenue and len(revenue) >= 3:
            growths = [
                revenue[k] > revenue[k - 1]
                for k in range(1, len(revenue))
                if revenue[k] is not None and revenue[k - 1] is not None
            ]
            if growths:
                q_growth = sum(growths) / len(growths) * 100

        q_debt = None
        if d_e is not None and i >= 1:
            b_prev = _latest_at_or_before(bal, inc[i - 1].period_end)
            pd = None
            if b_prev:
                pnw = _dec(getattr(b_prev, "net_worth", None))
                ptd = _dec(getattr(b_prev, "total_debt", None))
                pnd = _dec(getattr(b_prev, "net_debt", None))
                if pnw and pnd is not None:
                    pd = pnd / pnw
                elif pnw and ptd is not None:
                    pd = ptd / pnw
            if pd is not None and pd > 0:
                q_debt = _clamp(100 - max(0, (d_e / pd - 1)) * 40, 5, 100)
            elif pd is not None and d_e <= pd:
                q_debt = 90.0

        q_overall = None
        qual = [
            v
            for v in (q_cash, q_margin, q_profit, q_growth, q_debt, interest_score)
            if v is not None
        ]
        if qual:
            q_overall = sum(qual) / len(qual)

        rev = revenue[i]
        np_ = net_profit[i]
        ebitda_margin = (
            float(row.ebitda_margin_pct)
            if row.ebitda_margin_pct is not None
            else (ebitda[i] / rev * 100 if (rev and ebitda[i] is not None) else None)
        )
        net_margin = (
            float(row.net_margin_pct)
            if row.net_margin_pct is not None
            else (np_ / rev * 100 if (rev and np_ is not None) else None)
        )

        metrics = {
            "revenue_growth_pct": yy,
            "eps_growth_pct": ey,
            "ebitda_margin_pct": _clamp(ebitda_margin, 0, 200),
            "net_margin_pct": _clamp(net_margin, -100, 200),
            "roe_pct": roe,
            "roa_pct": roa,
            "roce_pct": roce,
            "debt_to_equity": d_e,
            "interest_safety_score": interest_score,
            "current_ratio": current_ratio,
            "ocf_to_net_profit": ocf_np,
            "fcf_growth_pct": fcf_growth,
            "cash_conversion_quality": q_cash,
            "margin_stability_quality": q_margin,
            "profit_consistency_quality": q_profit,
            "growth_consistency_quality": q_growth,
            "debt_trend_quality": q_debt,
            "overall_quality_score": q_overall,
        }
        for metric, value in metrics.items():
            out.append(
                {
                    "metric": metric,
                    "value": value,
                    "as_of": pe,
                    "period_end": pe,
                    "source": "engine",
                }
            )
    return out
