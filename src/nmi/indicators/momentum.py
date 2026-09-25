"""Momentum & relative-strength engine (Phase 2).

Returns: trailing-window price performance for the stock, plus relative
strength (excess return) against a benchmark's as-of closes. Relative strength
series look at the stock and benchmark independently to guarantee no
look-ahead: at every stock as-of date we use the latest benchmark close <= that
date.
"""

from __future__ import annotations

from collections.abc import Sequence

from nmi.indicators.types import CandleLike

MOMENTUM_WINDOWS = {"return_1m": 21, "return_3m": 63, "return_6m": 126, "return_12m": 252}

_RS_WINDOWS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}


def _as_closes(candles: Sequence[CandleLike]) -> list[tuple[object, float]]:
    out = []
    for c in candles:
        price = c.adjusted_close if getattr(c, "adjusted_close", None) is not None else c.close
        out.append((c.trade_date, float(price)))
    out.sort(key=lambda x: x[0])
    return out


def _window_return(closes: list[tuple[object, float]], window: int, idx: int) -> float | None:
    if idx - window < 0:
        return None
    base = closes[idx - window][1]
    if base <= 0:
        return None
    return (closes[idx][1] / base - 1) * 100


def compute_momentum(candles: Sequence[CandleLike]) -> list[dict]:
    """Trailing returns per trading day, one dict per day, as-of indexed."""
    closes = _as_closes(candles)
    rows = []
    for i, (as_of, _) in enumerate(closes):
        row = {
            "as_of": as_of,
            "return_1m": None,
            "return_3m": None,
            "return_6m": None,
            "return_12m": None,
        }
        for field, window in MOMENTUM_WINDOWS.items():
            row[field] = _window_return(closes, window, i)
        rows.append(row)
    return rows


def compute_relative_strength(
    candles: Sequence[CandleLike],
    benchmark_candles: Sequence[CandleLike],
    trend_threshold_pp: float = 0.5,
    trend_lookback_days: int = 63,
) -> list[dict]:
    """Excess returns and a simple trend label per stock trading day."""
    closes = _as_closes(candles)
    bench = _as_closes(benchmark_candles)
    if not closes or not bench:
        return []

    # Per-day excess return for each window.
    rows = []
    for i, (as_of, _price) in enumerate(closes):
        row = {
            "as_of": as_of,
            "rs_1m": None,
            "rs_3m": None,
            "rs_6m": None,
            "rs_12m": None,
        }
        b_idx = None
        for j, (bd, _bp) in enumerate(bench):
            if bd > as_of:
                break
            b_idx = j
        if b_idx is not None:
                for field, window in _RS_WINDOWS.items():
                    s_ret = _window_return(closes, window, i)
                    b_ret = _window_return(bench, window, b_idx)
                    if s_ret is not None and b_ret is not None:
                        row[field] = s_ret - b_ret
        rows.append(row)

    # Trend from the 1m relative-strength series vs its recent average.
    rs1m = [r["rs_1m"] for r in rows]
    for i, row in enumerate(rows):
        cur = row["rs_1m"]
        if cur is None or i < trend_lookback_days:
            row["rs_trend"] = "STABLE"
            continue
        hist = [v for v in rs1m[i - trend_lookback_days : i] if v is not None]
        if len(hist) < 20:
            row["rs_trend"] = "STABLE"
            continue
        avg = sum(hist) / len(hist)
        if cur > avg + trend_threshold_pp:
            row["rs_trend"] = "IMPROVING"
        elif cur < avg - trend_threshold_pp:
            row["rs_trend"] = "DETERIORATING"
        else:
            row["rs_trend"] = "STABLE"
    return rows
