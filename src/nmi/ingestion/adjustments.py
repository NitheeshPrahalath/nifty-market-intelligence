"""Corporate-action aware price adjustment.

Produces horizontally-comparable price series: every historical price is
adjusted so the entire history reflects current share count / dividend basis.

Convention
----------
A corporate action has an *ex-date*. Prices *on and after* the ex-date already
reflect the action (as-traded); prices *before* the ex-date are scaled.
Cumulative factors are composable:

* SPLIT/BONUS/RIGHTS (share ratio ``num`` new per ``den`` held, i.e.
  shares_after/shares_before = num/den): factor = den/num.
* DIVIDEND of ``D`` per share: factor = (prev_close - D) / prev_close, where
  prev_close is the close on the last trading day before the ex-date.
* MERGER/DEMERGER/OTHER: only adjusted when an explicit share ratio is given,
  otherwise flagged as unadjusted.

Every factor is persisted per row (``adjustment_factor``) so the adjustment is
auditable and reversible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from nmi.core.enums import CorporateActionType
from nmi.ingestion.records import Candle


@dataclass(slots=True)
class AdjustedCandle:
    symbol: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    adjusted_open: Decimal | None
    adjusted_high: Decimal | None
    adjusted_low: Decimal | None
    adjusted_close: Decimal
    adjustment_factor: Decimal
    volume: int | None = None
    turnover: Decimal | None = None
    warnings: list[str] = field(default_factory=list)


# Actions whose factor is determined purely by share ratio.
_SHARE_RATIO_ACTIONS = {
    CorporateActionType.SPLIT,
    CorporateActionType.BONUS,
    CorporateActionType.RIGHTS,
    CorporateActionType.MERGER,
    CorporateActionType.DEMERGER,
}


def adjustment_factors(
    candles: list[Candle], actions: list
) -> tuple[dict[date, Decimal], list[str]]:
    """Compute per-trade-date cumulative adjustment factors.

    Returns ``(factors, warnings)`` where ``factors[date]`` is the multiplier
    that converts a raw as-traded price into the adjusted basis.
    """
    dates = sorted({c.trade_date for c in candles})
    factors: dict[date, Decimal] = {d: Decimal(1) for d in dates}
    warnings: list[str] = []

    closes_by_date = {c.trade_date: c.close for c in candles}
    sorted_dates = sorted(dates)

    for action in sorted(actions, key=lambda a: a.ex_date):
        # Find the close on the last available trading day strictly before ex_date.
        prev_close = _previous_close(sorted_dates, action.ex_date, closes_by_date)
        f = _factor_for(action, prev_close, warnings)
        if f is None or f <= 0:
            warnings.append(
                f"{action.action_type.value} ex {action.ex_date.isoformat()} "
                f"for {action.symbol}: NOT adjusted (missing ratio or price); "
                "prices will be inconsistent across this event"
            )
            continue
        for d in dates:
            if d < action.ex_date:
                factors[d] *= f
    return factors, warnings


def apply_adjustments(
    candles: list[Candle], actions: list
) -> list[AdjustedCandle]:
    """Return candles with adjusted OHLC and per-row factor."""
    factors, warnings = adjustment_factors(candles, actions)
    factor_by_date = factors
    out: list[AdjustedCandle] = []
    for candle in candles:
        f = factor_by_date[candle.trade_date]
        out.append(
            AdjustedCandle(
                symbol=candle.symbol,
                trade_date=candle.trade_date,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                adjusted_open=_mul(candle.open, f),
                adjusted_high=_mul(candle.high, f),
                adjusted_low=_mul(candle.low, f),
                adjusted_close=_mul(candle.close, f),
                adjustment_factor=f,
                volume=candle.volume,
                turnover=candle.turnover,
                warnings=[f"corporate-action adjustments: {w}" for w in warnings],
            )
        )
    return out


def _factor_for(action, prev_close: Decimal | None, warnings: list[str]) -> Decimal | None:
    if action.action_type in _SHARE_RATIO_ACTIONS:
        ratio = _share_ratio(action)
        if ratio is None or ratio <= 0:
            warnings.append(
                f"{action.action_type.value} {action.symbol} ex {action.ex_date}: "
                "missing/invalid share ratio"
            )
            return None
        return Decimal(1) / ratio
    if action.action_type == CorporateActionType.DIVIDEND:
        if action.dividend_amount is None or action.dividend_amount <= 0:
            warnings.append(
                f"DIVIDEND {action.symbol} ex {action.ex_date}: missing amount"
            )
            return None
        if prev_close is None or prev_close <= 0:
            warnings.append(
                f"DIVIDEND {action.symbol} ex {action.ex_date}: no reliable "
                "pre-ex close to build a ratio"
            )
            return None
        return (prev_close - action.dividend_amount) / prev_close
    warnings.append(
        f"{action.action_type.value} {action.symbol} ex {action.ex_date}: "
        "unsupported for automatic adjustment"
    )
    return None


def _share_ratio(action) -> Decimal | None:
    if action.ratio_numerator is None or action.ratio_denominator is None:
        return None
    den = Decimal(action.ratio_denominator)
    if den == 0:
        return None
    return Decimal(action.ratio_numerator) / den


def _previous_close(
    dates: list[date], ex_date: date, closes: dict[date, Decimal]
) -> Decimal | None:
    for d in reversed(dates):
        if d < ex_date:
            return closes[d]
    return None


def _mul(v: Decimal | None, factor: Decimal) -> Decimal | None:
    if v is None:
        return None
    return (v * factor).quantize(Decimal("0.0001"))
