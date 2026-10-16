from __future__ import annotations

import io
import zipfile
from datetime import date

import pandas as pd

from nmi.ingestion.providers import nse_provider
from nmi.ingestion.providers.base import get_provider
from nmi.ingestion.providers.nse_provider import (
    NSEBhavcopyPriceProvider,
    NSEIndexPriceProvider,
    _bhavcopy_urls,
    _normalise_bhavcopy,
    _normalise_index_close,
    _strip_zip,
)
from tests.conftest import FIXTURES

BHAVCOPY_DIR = FIXTURES / "bhavcopy"
INDEX_CLOSE_DIR = FIXTURES / "index_close"


# ------------------------------------------------------------------ bhavcopy
def test_bhavcopy_provider_reads_local_udiff_file():
    provider = NSEBhavcopyPriceProvider(BHAVCOPY_DIR, download=False)
    candles = provider.fetch_prices("RELIANCE", start=date(2024, 6, 3), end=date(2024, 6, 4))
    assert [c.trade_date for c in candles] == [date(2024, 6, 3), date(2024, 6, 4)]
    assert float(candles[0].close) == 2912.50
    assert float(candles[1].close) == 2930.00
    assert candles[0].volume == 4500000
    assert float(candles[0].turnover) == 13_100_000_000.00
    assert candles[0].source == "nse"


def test_bhavcopy_provider_honours_date_range():
    provider = NSEBhavcopyPriceProvider(BHAVCOPY_DIR, download=False)
    candles = provider.fetch_prices("TCS", start=date(2024, 6, 4), end=date(2024, 6, 4))
    assert len(candles) == 1
    assert candles[0].trade_date == date(2024, 6, 4)
    assert float(candles[0].close) == 3900.00


def test_bhavcopy_provider_unknown_symbol_is_empty():
    provider = NSEBhavcopyPriceProvider(BHAVCOPY_DIR, download=False)
    assert provider.fetch_prices("NOSUCH", start=date(2024, 6, 3), end=date(2024, 6, 4)) == []


def test_bhavcopy_provider_skips_missing_days_without_download():
    provider = NSEBhavcopyPriceProvider(BHAVCOPY_DIR, download=False)
    candles = provider.fetch_prices("RELIANCE", start=date(2024, 6, 1), end=date(2024, 6, 10))
    assert [c.trade_date for c in candles] == [date(2024, 6, 3), date(2024, 6, 4)]


def test_bhavcopy_provider_rejects_non_nse_exchange():
    from nmi.core.enums import Exchange

    provider = NSEBhavcopyPriceProvider(BHAVCOPY_DIR, download=False)
    assert provider.fetch_prices("RELIANCE", exchange=Exchange.BSE) == []


def test_normalise_legacy_bhavcopy_layout():
    df = pd.DataFrame(
        {
            "SYMBOL": ["RELIANCE"],
            "SERIES": ["EQ"],
            "OPEN": [2800.0],
            "HIGH": [2850.0],
            "LOW": [2790.0],
            "CLOSE": [2840.0],
            "TOTTRDQTY": [1000],
            "TOTTRDVAL": [2_840_000.0],
            "ISIN": ["INE002A01018"],
        }
    )
    out = _normalise_bhavcopy(df)
    assert out.iloc[0]["symbol"] == "RELIANCE"
    assert out.iloc[0]["close"] == 2840.0
    assert out.iloc[0]["series"] == "EQ"


def test_normalise_delivery_bhavcopy_converts_turnover_lacs():
    df = pd.DataFrame(
        {
            "SYMBOL": ["TCS"],
            "SERIES": ["EQ"],
            "OPEN_PRICE": [3900.0],
            "HIGH_PRICE": [3920.0],
            "LOW_PRICE": [3880.0],
            "CLOSE_PRICE": [3905.0],
            "TTL_TRD_QNTY": [2000],
            "TURNOVER_LACS": [78100.0],
            "ISIN": ["INE467B01029"],
        }
    )
    out = _normalise_bhavcopy(df)
    assert out.iloc[0]["turnover"] == 78100.0 * 100_000


def test_strip_zip_unwraps_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv", "a,b\n1,2\n")
    assert _strip_zip(buffer.getvalue()).startswith(b"a,b")


def test_bhavcopy_download_parses_and_caches(tmp_path, monkeypatch):
    payload = (
        b"TradDt,BizDt,Sgmt,Src,FinInstrmTp,ISIN,TckrSymb,SctySrs,"
        b"OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
        b"2024-06-03,2024-06-03,CM,NSE,STK,INE002A01018,RELIANCE,EQ,"
        b"2900,2925,2880,2912.50,2912.50,2895,4500000,13100000000\n"
    )
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        return payload

    monkeypatch.setattr(nse_provider, "_http_get", fake_get)
    provider = NSEBhavcopyPriceProvider(tmp_path, download=True)
    first = provider.fetch_prices("RELIANCE", start=date(2024, 6, 3), end=date(2024, 6, 3))
    second = provider.fetch_prices("RELIANCE", start=date(2024, 6, 3), end=date(2024, 6, 3))
    assert len(first) == 1 and len(second) == 1
    assert len(calls) == 1  # in-memory cache avoids a second request
    assert list(tmp_path.iterdir())  # downloaded file cached to disk


def test_bhavcopy_urls_prefer_udiff_then_legacy():
    urls = _bhavcopy_urls(date(2024, 6, 3))
    assert urls[0].endswith("BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv.zip")
    assert urls[1].endswith("cm03JUN2024bhav.csv.zip")


def test_bhavcopy_provider_reads_local_zip_archive(tmp_path):
    inner = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,ISIN,TckrSymb,SctySrs,"
        "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal\n"
        "2024-06-03,2024-06-03,CM,NSE,STK,INE002A01018,RELIANCE,EQ,"
        "2900,2925,2880,2912.50,2912.50,2895,4500000,13100000000\n"
    )
    archive = tmp_path / "BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv", inner)
    provider = NSEBhavcopyPriceProvider(tmp_path, download=False)
    candles = provider.fetch_prices("RELIANCE", start=date(2024, 6, 3), end=date(2024, 6, 3))
    assert len(candles) == 1
    assert float(candles[0].close) == 2912.50


# --------------------------------------------------------------- index prices
def test_index_price_provider_reads_local_file():
    provider = NSEIndexPriceProvider(INDEX_CLOSE_DIR, download=False)
    rows = provider.fetch_index_prices("NIFTY_50", start=date(2024, 6, 3), end=date(2024, 6, 4))
    assert [r.trade_date for r in rows] == [date(2024, 6, 3), date(2024, 6, 4)]
    assert float(rows[0].close) == 23263.90
    assert rows[0].source == "nse"


def test_index_price_provider_maps_sub_index_name():
    provider = NSEIndexPriceProvider(INDEX_CLOSE_DIR, download=False)
    rows = provider.fetch_index_prices("NIFTY_IT", start=date(2024, 6, 3), end=date(2024, 6, 3))
    assert len(rows) == 1
    assert float(rows[0].close) == 33600.00


def test_index_price_provider_unknown_index_is_empty():
    provider = NSEIndexPriceProvider(INDEX_CLOSE_DIR, download=False)
    rows = provider.fetch_index_prices(
        "NIFTY_NOTREAL", start=date(2024, 6, 3), end=date(2024, 6, 4)
    )
    assert rows == []


def test_index_price_download_parses_and_caches(tmp_path, monkeypatch):
    payload = (
        b"Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
        b"Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
        b"Nifty 50,03-06-2024,23263.80,23338.85,23161.20,23263.90,469.05,2.06,0,0,0,0,0\n"
    )
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        return payload

    monkeypatch.setattr(nse_provider, "_http_get", fake_get)
    provider = NSEIndexPriceProvider(tmp_path, download=True)
    rows = provider.fetch_index_prices("NIFTY_50", start=date(2024, 6, 3), end=date(2024, 6, 3))
    assert len(rows) == 1
    assert float(rows[0].close) == 23263.90
    assert len(calls) == 1
    assert list(tmp_path.iterdir())


def test_normalise_index_close_handles_dash_placeholders():
    df = pd.DataFrame(
        {
            "Index Name": ["Nifty 50 Futures TR Index"],
            "Index Date": ["18-10-2024"],
            "Open Index Value": ["-"],
            "High Index Value": ["-"],
            "Low Index Value": ["-"],
            "Closing Index Value": ["26803.67"],
            "Volume": ["-"],
        }
    )
    out = _normalise_index_close(df)
    assert out.iloc[0]["trade_date"] == date(2024, 10, 18)
    assert pd.isna(out.iloc[0]["open"])
    assert out.iloc[0]["close"] == 26803.67


# -------------------------------------------------------------------- factory
def test_factory_builds_nse_price_provider(monkeypatch):
    from nmi.core.config import settings

    monkeypatch.setattr(settings, "price_provider", "nse")
    assert get_provider("price").name == "nse"


def test_factory_builds_nse_index_price_provider(monkeypatch):
    from nmi.core.config import settings

    monkeypatch.setattr(settings, "index_price_provider", "nse")
    assert get_provider("index_price").name == "nse"
