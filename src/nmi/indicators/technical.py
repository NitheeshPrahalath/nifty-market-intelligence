"""Technical indicator engine (Phase 2).

Pure, deterministic calculations over a daily OHLCV series (already adjusted
for corporate actions upstream). Every window is warm-up aware: values are None
until enough history exists, so no silent look-ahead bias is possible for an
as-of calculation. Output rows match the ``technical_indicators`` table columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from nmi.indicators.types import CandleLike

_TREND_UP = "UPTREND"
_TREND_DOWN = "DOWNTREND"
_TREND_FLAT = "CONSOLIDATION"


def _prices(candles: Sequence[CandleLike]) -> np.ndarray:
    return np.asarray(
        [
            float(c.adjusted_close) if getattr(c, "adjusted_close", None) is not None
            else float(c.close)
            for c in candles
        ],
        dtype=float,
    )


def _highs(candles: Sequence[CandleLike]) -> np.ndarray:
    return np.asarray([float(c.high) for c in candles], dtype=float)


def _lows(candles: Sequence[CandleLike]) -> np.ndarray:
    return np.asarray([float(c.low) for c in candles], dtype=float)


def _volumes(candles: Sequence[CandleLike]) -> np.ndarray:
    return np.asarray([float(c.volume or 0.0) for c in candles], dtype=float)


def _sma(a: np.ndarray, n: int, min_periods: int = 0) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    if a.shape[0] < n:
        return out
    cs = np.cumsum(np.insert(a, 0, 0.0))
    out[n - 1 :] = (cs[n:] - cs[:-n]) / n
    return out


def _ema(a: np.ndarray, span: int) -> np.ndarray:
    """Exponential MA seeded with an SMA of the first ``span`` valid entries."""
    out = np.full(a.shape, np.nan)
    finite = np.flatnonzero(np.isfinite(a))
    if finite.size < span:
        return out
    start = int(finite[0])
    alpha = 2.0 / (span + 1.0)
    out[start + span - 1] = np.mean(a[start : start + span])
    for i in range(start + span, a.shape[0]):
        out[i] = alpha * a[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    if a.shape[0] <= n:
        return out
    out[n - 1] = np.sum(a[:n])
    for i in range(n, a.shape[0]):
        out[i] = out[i - 1] - out[i - 1] / n + a[i]
    return out


def _rolling(a: np.ndarray, n: int, fn, min_periods: int = 1) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    for i in range(len(a)):
        lo = max(0, i - n + 1)
        window = a[lo : i + 1]
        if len(window) < min_periods:
            continue
        out[i] = fn(window)
    return out


def rsi14(close: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if close.shape[0] <= n:
        return out
    delta = np.diff(close)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    avg_gain = _wilder(gain, n)
    avg_loss = _wilder(loss, n)
    rs = np.divide(
        avg_gain,
        avg_loss,
        out=np.full_like(avg_gain, np.nan),
        where=avg_loss > 0,
    )
    rs = np.where(np.isnan(rs) & (avg_gain > 0), np.inf, rs)
    vals = 100 - 100 / (1 + rs)
    out[n:] = vals[n - 1 :]
    return out


def macd(close: np.ndarray, fast=12, slow=26, signal=9):
    line = _ema(close, fast) - _ema(close, slow)
    sig = _ema(line, signal)
    hist = line - sig
    return line, sig, hist


def atr14(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if close.shape[0] <= n:
        return out
    prev = close[:-1]
    tr = np.empty(close.shape[0] - 1)
    tr[0] = high[1] - low[1]
    for i in range(1, close.shape[0] - 1):
        h = high[i + 1]
        low_i = low[i + 1]
        c = prev[i - 1]
        tr[i] = max(h - low_i, abs(h - c), abs(low_i - c))
    smoothed = _wilder(tr, n)
    out[n:] = smoothed[n - 1 :]
    return out


def adx14(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14):
    length = close.shape[0]
    plus_di = np.full(length, np.nan)
    minus_di = np.full(length, np.nan)
    out = np.full(length, np.nan)
    if length <= n:
        return out, plus_di, minus_di

    up_move = np.diff(high)
    down_move = -np.diff(low)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = np.empty(length - 1)
    for i in range(length - 1):
        h, low_i, c = high[i + 1], low[i + 1], close[i]
        tr[i] = max(h - low_i, abs(h - c), abs(low_i - c))

    atr_s = _wilder(tr, n)
    pd_s = _wilder(plus_dm, n)
    md_s = _wilder(minus_dm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(atr_s > 0, 100 * pd_s / atr_s, np.nan)
        mdi = np.where(atr_s > 0, 100 * md_s / atr_s, np.nan)
        dx = np.where(pdi + mdi > 0, 100 * np.abs(pdi - mdi) / (pdi + mdi), np.nan)

    adx = _wilder(np.nan_to_num(dx, nan=0.0), n)
    plus_di[n:] = pdi[n - 1 :]
    minus_di[n:] = mdi[n - 1 :]
    out[n + n :] = adx[2 * n - 1 : length - 1]
    return out, plus_di, minus_di


def bollinger(close: np.ndarray, window: int = 20, k: float = 2.0):
    mid = _sma(close, window)
    std = _rolling(close, window, lambda w: np.nan if w.shape[0] < 2 else np.std(w, ddof=1))
    upper = mid + k * std
    lower = mid - k * std
    width = np.divide(
        upper - lower,
        mid,
        out=np.full_like(mid, np.nan),
        where=(mid > 0) & ~np.isnan(mid),
    )
    spread = upper - lower
    percent_b = np.divide(
        close - lower,
        spread,
        out=np.full_like(close, np.nan),
        where=(spread > 0) & ~np.isnan(spread),
    )
    percent_b = np.where(
        (spread <= 0) & ~np.isnan(mid), 0.5, percent_b
    )
    return upper, mid, lower, width, percent_b


def hist_vol(close: np.ndarray, window: int = 20) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if close.shape[0] <= window:
        return out
    log_ret = np.diff(np.log(close))

    def std_pct(w: np.ndarray) -> float:
        if w.shape[0] < 2:
            return np.nan
        return np.std(w, ddof=1) * np.sqrt(252) * 100

    vol = _rolling(log_ret, window - 1, std_pct)
    out[1:] = vol
    return out


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    out = np.zeros(close.shape[0])
    for i in range(1, close.shape[0]):
        if close[i] > close[i - 1]:
            out[i] = out[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - volume[i]
        else:
            out[i] = out[i - 1]
    return out


def compute_technical_indicators(
    candles: Sequence[CandleLike], volume_spike_multiple: float = 2.5
) -> list[dict]:
    """Compute per-day technical snapshots. Returns dicts (one per trading day)."""
    if not candles:
        return []

    close = _prices(candles)
    high = _highs(candles)
    low = _lows(candles)
    volume = _volumes(candles)
    n = len(candles)

    sma20 = _sma(close, 20)
    sma50 = _sma(close, 50)
    sma100 = _sma(close, 100)
    sma200 = _sma(close, 200)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    rsi = rsi14(close)
    macd_line, macd_sig, macd_hist = macd(close)
    roc = np.full(n, np.nan)
    roc[10:] = (close[10:] / close[:-10] - 1) * 100

    ll = _rolling(low, 14, np.min)
    hh = _rolling(high, 14, np.max)
    rng = hh - ll
    stoch_k = np.divide(close - ll, rng, out=np.full_like(close, np.nan), where=rng > 0) * 100
    stoch_d = _sma(np.nan_to_num(stoch_k, nan=50.0), 3)
    williams_r = np.divide(hh - close, rng, out=np.full_like(close, np.nan), where=rng > 0) * -100.0

    tp = (high + low + close) / 3.0
    tp_sma = _sma(tp, 20)
    ad = _rolling(tp, 20, lambda w: np.mean(np.abs(w - np.mean(w))))
    cci = np.divide(
        tp - tp_sma,
        0.015 * ad,
        out=np.full_like(tp, np.nan),
        where=(ad > 0) & ~np.isnan(tp_sma),
    )

    adx, pdi, mdi = adx14(high, low, close)
    atr = atr14(high, low, close)
    hv20 = hist_vol(close, 20)
    hv60 = hist_vol(close, 60)
    bu, bm, bl, bw, pb = bollinger(close)

    vsma20 = _sma(volume, 20)
    vratio = np.divide(volume, vsma20, out=np.full_like(volume, np.nan), where=vsma20 > 0)
    obv_values = obv(close, volume)

    high_52w = _rolling(high, 252, np.max)
    low_52w = _rolling(low, 252, np.min)
    dist_high = (
        np.divide(
            close - high_52w,
            high_52w,
            out=np.full_like(close, np.nan),
            where=high_52w > 0,
        )
        * 100
    )
    dist_low = (
        np.divide(
            close - low_52w,
            low_52w,
            out=np.full_like(close, np.nan),
            where=low_52w > 0,
        )
        * 100
    )

    drawdown = np.empty(n)
    recovery = np.empty(n)
    peak = -np.inf
    trough = np.inf
    for i in range(n):
        if high[i] > peak:
            peak = high[i]
            trough = low[i]
            recovery[i] = 100.0
        else:
            trough = min(trough, low[i])
            if peak > trough:
                recovery[i] = (close[i] - trough) / (peak - trough) * 100
            else:
                recovery[i] = np.nan
        if peak > 0:
            drawdown[i] = (close[i] / peak - 1) * 100
        else:
            drawdown[i] = np.nan

    breakout = np.zeros(n, dtype=bool)
    breakdown = np.zeros(n, dtype=bool)
    for i in range(1, n):
        lo = max(0, i - 252)
        prior_high = float(np.max(high[lo:i])) if i - lo > 0 else np.nan
        prior_low = float(np.min(low[lo:i])) if i - lo > 0 else np.nan
        if i - lo >= 20 and not np.isnan(prior_high):
            breakout[i] = close[i] > prior_high
            breakdown[i] = close[i] < prior_low

    rows: list[dict] = []
    for i, candle in enumerate(candles):
        trend = _TREND_FLAT
        if not np.isnan(ema20[i]) and not np.isnan(ema50[i]) and not np.isnan(ema200[i]):
            if ema20[i] > ema50[i] > ema200[i]:
                trend = _TREND_UP
            elif ema20[i] < ema50[i] < ema200[i]:
                trend = _TREND_DOWN
        rows.append(
            {
                "as_of": candle.trade_date,
                "sma20": sma20[i],
                "sma50": sma50[i],
                "sma100": sma100[i],
                "sma200": sma200[i],
                "ema20": ema20[i],
                "ema50": ema50[i],
                "ema200": ema200[i],
                "rsi14": rsi[i],
                "macd": macd_line[i],
                "macd_signal": macd_sig[i],
                "macd_hist": macd_hist[i],
                "roc10": roc[i],
                "stoch_k": stoch_k[i],
                "stoch_d": stoch_d[i],
                "williams_r": williams_r[i],
                "cci20": cci[i],
                "adx14": adx[i],
                "plus_di14": pdi[i],
                "minus_di14": mdi[i],
                "atr14": atr[i],
                "hist_vol_20": hv20[i],
                "hist_vol_60": hv60[i],
                "bollinger_upper": bu[i],
                "bollinger_middle": bm[i],
                "bollinger_lower": bl[i],
                "bollinger_width": bw[i],
                "bollinger_percent_b": pb[i],
                "volume_sma20": int(vsma20[i]) if vsma20[i] == vsma20[i] else None,
                "volume_ratio": vratio[i],
                "volume_spike": bool(
                    vratio[i] >= volume_spike_multiple and not np.isnan(vratio[i])
                ),
                "obv": obv_values[i],
                "high_52w": high_52w[i],
                "low_52w": low_52w[i],
                "dist_from_high_52w_pct": dist_high[i],
                "dist_from_low_52w_pct": dist_low[i],
                "drawdown_pct": drawdown[i],
                "recovery_pct": recovery[i],
                "breakout_52w": bool(breakout[i]),
                "breakdown_52w": bool(breakdown[i]),
                "trend_state": trend,
            }
        )
    return [
        {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
        for r in rows
        if r["as_of"] is not None
    ]
