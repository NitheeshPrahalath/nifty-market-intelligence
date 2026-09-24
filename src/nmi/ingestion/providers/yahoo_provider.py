"""Yahoo Finance price provider (free fallback source).

Network-dependent and therefore never used in unit tests (those skip when the
package or connectivity is absent). It exists purely as a secondary feed, not as
the primary source of truth for the Indian market.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from nmi.core.config import settings
from nmi.core.enums import Exchange
from nmi.ingestion.records import Candle

log = logging.getLogger(__name__)


def _normalise_date(v) -> str:
    if isinstance(v, (datetime, date)):
        return str(v.date() if isinstance(v, datetime) else v)
    return str(v)


class YahooPriceProvider:
    name = "yahoo"

    def __init__(self, symbol_suffix: str | None = None):
        self.symbol_suffix = symbol_suffix or settings.market_data_symbol_suffix

    def fetch_prices(
        self,
        symbol: str,
        exchange: Exchange = Exchange.NSE,
        start: date | None = None,
        end: date | None = None,
        source_timestamp: datetime | None = None,
    ) -> list[Candle]:
        import pandas as pd
        import yfinance as yf

        ticker = f"{symbol}{self.symbol_suffix}"
        period_start = (start or date.today() - timedelta(days=365 * 5)).isoformat()
        period_end = (end or date.today()).isoformat()
        raw = yf.download(
            ticker, start=period_start, end=period_end, auto_adjust=False, progress=False
        )
        if raw is None or raw.empty:
            log.warning("yahoo returned no rows for %s", ticker)
            return []

        frame: pd.DataFrame = raw.dropna(subset=["Close"])
        cap = (
            pd.Timestamp(period_end) + pd.Timedelta(days=1)
            if not raw.empty
            else None
        )
        records: list[Candle] = []
        for idx, row in frame.iterrows():
            if cap is not None and pd.Timestamp(idx) >= cap:
                continue
            close = float(row["Close"])
            if close <= 0:
                continue
            volume = int(row["Volume"]) if not pd.isna(row["Volume"]) else None
            records.append(
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    trade_date=_normalise_date(idx),
                    open=_maybe(row, "Open"),
                    high=_maybe(row, "High"),
                    low=_maybe(row, "Low"),
                    close=close if close else None,  # type: ignore[arg-type]
                    volume=volume,
                    turnover=None,
                    source=self.name,
                    source_timestamp=source_timestamp or datetime.utcnow(),
                )
            )
        return records


class YahooUniverseProvider:
    """Universe/membership is reference data — never sourced from Yahoo.

    This adapter deliberately returns nothing; use the CSV membership provider
    (or a Kite/NSE adapter) for index constituents.
    """

    name = "yahoo"

    def __init__(self, *args, **kwargs):
        pass

    def fetch_universe(self):
        log.warning("YahooUniverseProvider returns no rows; supply membership via CSV")
        return []


def _maybe(row, col) -> float | None:
    import pandas as pd

    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return float(v)
