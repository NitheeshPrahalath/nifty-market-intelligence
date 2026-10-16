"""Official NSE end-of-day file providers (free, no subscription).

NSE publishes two plain archive files that cover everything this platform needs
for daily analysis, at no cost:

* **Bhavcopy** — one row per listed security per session (OHLC, volume,
  turnover). Two generations of format are understood:

  - UDiFF (from 2024-07-08):
    ``BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip``
  - legacy (until 2024-07-05): ``cmDDMMMYYYYbhav.csv.zip``
  - delivery data: ``sec_bhavdata_full_DDMMYYYY.csv``

* **Index close** — ``ind_close_all_DDMMYYYY.csv`` holds the OHLC of every NSE
  index for one session.

For each trading day the adapter first looks for a local copy under
``settings.bhavcopy_dir`` / ``settings.index_close_dir`` (so a backfill is
reproducible offline) and only downloads over HTTP when a file is missing.
Downloaded files are written back to those directories and, for the lifetime of
the adapter, remembered in memory — so backfilling N symbols over D days costs
at most D requests, not N x D.

The network is only touched on the download path; unit tests exercise the
parsers against local fixture files and stub the HTTP layer.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from nmi.core.config import settings
from nmi.core.enums import Exchange
from nmi.ingestion.records import Candle, IndexPriceRecord

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_UDIFF_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
)
_LEGACY_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{mmm}/cm{ddmmmyyyy}bhav.csv.zip"
)
_INDEX_CLOSE_URL = (
    "https://nsearchives.nseindia.com/content/indices/"
    "ind_close_all_{ddmmyyyy}.csv"
)

# Platform index code -> the exact "Index Name" string NSE uses. Codes not listed
# here fall back to a title-cased form (which also covers most Nifty sub-indices)
# and can always be overridden via the ``INDEX_NAME_MAP`` setting.
_DEFAULT_INDEX_NAMES = {
    "NIFTY_50": "Nifty 50",
    "NIFTY_NEXT_50": "Nifty Next 50",
    "NIFTY_100": "Nifty 100",
    "NIFTY_200": "Nifty 200",
    "NIFTY_500": "Nifty 500",
    "NIFTY_BANK": "Nifty Bank",
    "NIFTY_IT": "Nifty IT",
    "NIFTY_MIDCAP_150": "Nifty Midcap 150",
    "NIFTY_SMALLCAP_250": "Nifty Smallcap 250",
    "INDIA_VIX": "India VIX",
}

_PREFERRED_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}


# --------------------------------------------------------------- HTTP helper
def _http_get(url: str) -> bytes:
    """Download ``url`` with a browser user-agent (NSE rejects bare clients)."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urllib.request.urlopen(  # noqa: S310 - fixed, hard-coded NSE hosts
        request, timeout=settings.nse_http_timeout_seconds
    ) as response:
        return response.read()


def _strip_zip(data: bytes) -> bytes:
    """Return the CSV payload, transparently unzipping a ``.zip`` archive."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = next(
                (n for n in archive.namelist() if n.lower().endswith(".csv")),
                archive.namelist()[0],
            )
            return archive.read(member)
    return data


# -------------------------------------------------------------- date helpers
def _business_days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _date_tokens(day: date) -> tuple[str, str, str]:
    return (
        day.strftime("%Y%m%d"),
        day.strftime("%d%m%Y"),
        day.strftime("%d%b%Y").upper(),
    )


def _find_local(directory: Path, names: list[str], tokens: tuple[str, ...]) -> Path | None:
    """Return the first matching file: exact preferred names, then a token scan."""
    if not directory.is_dir():
        return None
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix.lower() not in {".csv", ".zip"}:
            continue
        if any(token in entry.name for token in tokens):
            return entry
    return None


# ------------------------------------------------------------ bhavcopy parse
def _normalise_bhavcopy(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise any of the three bhavcopy generations to a common frame."""
    columns = {str(c).strip().upper(): c for c in df.columns}
    if "TCKRSYMB" in columns:
        rename = {
            "TCKRSYMB": "symbol",
            "SCTYSRS": "series",
            "OPNPRIC": "open",
            "HGHPRIC": "high",
            "LWPRIC": "low",
            "CLSPRIC": "close",
            "TTLTRADGVOL": "volume",
            "TTLTRFVAL": "turnover",
            "ISIN": "isin",
        }
        df = df.rename(columns={src: rename[key] for key, src in columns.items() if key in rename})
    elif "SYMBOL" in columns and "OPEN" in columns:
        rename = {
            "SYMBOL": "symbol",
            "SERIES": "series",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "CLOSE": "close",
            "TOTTRDQTY": "volume",
            "TOTTRDVAL": "turnover",
            "ISIN": "isin",
        }
        df = df.rename(columns={src: rename[key] for key, src in columns.items() if key in rename})
    elif "SYMBOL" in columns and "OPEN_PRICE" in columns:
        rename = {
            "SYMBOL": "symbol",
            "SERIES": "series",
            "OPEN_PRICE": "open",
            "HIGH_PRICE": "high",
            "LOW_PRICE": "low",
            "CLOSE_PRICE": "close",
            "TTL_TRD_QNTY": "volume",
            "TURNOVER_LACS": "turnover",
            "ISIN": "isin",
        }
        df = df.rename(columns={src: rename[key] for key, src in columns.items() if key in rename})
        if "turnover" in df.columns:
            df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce") * 100_000
    else:
        raise ValueError(
            "unrecognised bhavcopy layout; columns were: " + ", ".join(map(str, df.columns))
        )

    out = pd.DataFrame()
    out["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    out["series"] = (
        df["series"].astype(str).str.strip().str.upper()
        if "series" in df.columns
        else ""
    )
    for column in ("open", "high", "low", "close", "turnover"):
        out[column] = (
            pd.to_numeric(df[column], errors="coerce") if column in df.columns else None
        )
    out["volume"] = (
        pd.to_numeric(df["volume"], errors="coerce") if "volume" in df.columns else None
    )
    out["isin"] = df["isin"].astype(str).str.strip() if "isin" in df.columns else None
    return out


def _candidate_bhavcopy_names(day: date) -> list[str]:
    ymd, ddmmyyyy, ddmmmyyyy = _date_tokens(day)
    return [
        f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv",
        f"BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip",
        f"cm{ddmmmyyyy}bhav.csv",
        f"cm{ddmmmyyyy}bhav.csv.zip",
        f"sec_bhavdata_full_{ddmmyyyy}.csv",
    ]


def _bhavcopy_urls(day: date) -> list[str]:
    ymd, _, ddmmmyyyy = _date_tokens(day)
    return [
        _UDIFF_BHAVCOPY_URL.format(ymd=ymd),
        _LEGACY_BHAVCOPY_URL.format(
            yyyy=day.strftime("%Y"), mmm=day.strftime("%b").upper(), ddmmmyyyy=ddmmmyyyy
        ),
    ]


# ----------------------------------------------------------- index-close parse
def _normalise_index_close(df: pd.DataFrame) -> pd.DataFrame:
    columns = {str(c).strip(): c for c in df.columns}
    df = df.rename(
        columns={
            columns[k]: out
            for k, out in {
                "Index Name": "index_name",
                "Index Date": "trade_date",
                "Open Index Value": "open",
                "High Index Value": "high",
                "Low Index Value": "low",
                "Closing Index Value": "close",
                "Volume": "volume",
            }.items()
            if k in columns
        }
    )
    out = pd.DataFrame()
    out["index_name"] = df["index_name"].astype(str).str.strip()
    out["trade_date"] = pd.to_datetime(
        df["trade_date"], format="%d-%m-%Y", errors="coerce"
    ).dt.date
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = (
            pd.to_numeric(df[column], errors="coerce") if column in df.columns else None
        )
    return out


def _index_candidate_names(index_code: str) -> list[str]:
    mapped = settings.index_name_map.get(index_code)
    if mapped:
        return [mapped]
    if index_code in _DEFAULT_INDEX_NAMES:
        return [_DEFAULT_INDEX_NAMES[index_code]]
    return [index_code.replace("_", " ").title()]


# --------------------------------------------------------------- converters
def _to_int(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _read_frame(path: Path) -> pd.DataFrame:
    """Read a bhavcopy/close file, unzipping transparently when needed."""
    return pd.read_csv(io.BytesIO(_strip_zip(path.read_bytes())))


# ------------------------------------------------------------------ providers
class NSEBhavcopyPriceProvider:
    """Daily EOD prices from NSE's published bhavcopy files (free)."""

    name = "nse"

    def __init__(self, bhavcopy_dir: Path | None = None, *, download: bool = True):
        self.bhavcopy_dir = Path(bhavcopy_dir or settings.bhavcopy_dir)
        self.download = download
        self._cache: dict[date, pd.DataFrame | None] = {}

    def fetch_prices(
        self,
        symbol: str,
        exchange: Exchange = Exchange.NSE,
        start: date | None = None,
        end: date | None = None,
        source_timestamp: datetime | None = None,
    ) -> list[Candle]:
        if exchange != Exchange.NSE:
            log.warning("NSE bhavcopy only serves NSE securities, not %s", exchange)
            return []
        if end is None:
            end = date.today()
        if start is None:
            start = end
        stamp = source_timestamp or datetime.utcnow()
        wanted = symbol.strip().upper()
        records: list[Candle] = []
        for day in _business_days(start, end):
            frame = self._frame_for(day)
            if frame is None:
                continue
            rows = frame[frame["symbol"] == wanted]
            if rows.empty:
                continue
            preferred = rows[rows["series"].isin(_PREFERRED_SERIES)]
            row = preferred.iloc[0] if not preferred.empty else rows.iloc[0]
            close = row["close"]
            if close is None or pd.isna(close):
                continue
            records.append(
                Candle(
                    symbol=symbol,
                    exchange=Exchange.NSE,
                    trade_date=day,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=close,
                    volume=_to_int(row["volume"]),
                    turnover=row["turnover"],
                    source=self.name,
                    source_timestamp=stamp,
                )
            )
        return records

    def _frame_for(self, day: date) -> pd.DataFrame | None:
        if day in self._cache:
            return self._cache[day]
        frame: pd.DataFrame | None = None
        path = _find_local(
            self.bhavcopy_dir, _candidate_bhavcopy_names(day), _date_tokens(day)
        )
        if path is not None:
            frame = _normalise_bhavcopy(_read_frame(path))
        elif self.download:
            frame = self._download(day)
        self._cache[day] = frame
        return frame

    def _download(self, day: date) -> pd.DataFrame | None:
        last_error: Exception | None = None
        for url in _bhavcopy_urls(day):
            try:
                data = _http_get(url)
            except Exception as exc:  # noqa: BLE001 - try the next generation
                last_error = exc
                continue
            self._persist(day, data, url)
            _throttle()
            return _normalise_bhavcopy(
                pd.read_csv(io.BytesIO(_strip_zip(data)))
            )
        log.warning("no bhavcopy available for %s (%s)", day, last_error)
        return None

    def _persist(self, day: date, data: bytes, url: str) -> None:
        name = Path(url.split("?", 1)[0]).name
        try:
            self.bhavcopy_dir.mkdir(parents=True, exist_ok=True)
            (self.bhavcopy_dir / name).write_bytes(data)
        except OSError as exc:  # noqa: BLE001 - caching is best-effort
            log.debug("could not cache bhavcopy %s: %s", day, exc)


class NSEIndexPriceProvider:
    """Benchmark index EOD prices from NSE's ``ind_close_all`` files (free)."""

    name = "nse"

    def __init__(self, index_close_dir: Path | None = None, *, download: bool = True):
        self.index_close_dir = Path(index_close_dir or settings.index_close_dir)
        self.download = download
        self._cache: dict[date, pd.DataFrame | None] = {}

    def fetch_index_prices(
        self,
        index_code: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[IndexPriceRecord]:
        if end is None:
            end = date.today()
        if start is None:
            start = end - timedelta(days=365)
        names = _index_candidate_names(index_code)
        stamp = datetime.utcnow()
        records: list[IndexPriceRecord] = []
        for day in _business_days(start, end):
            frame = self._frame_for(day)
            if frame is None:
                continue
            rows = frame[frame["index_name"].isin(names)]
            if rows.empty:
                continue
            row = rows.iloc[0]
            close = row["close"]
            if close is None or pd.isna(close):
                continue
            records.append(
                IndexPriceRecord(
                    index_code=index_code,
                    trade_date=day,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=close,
                    volume=_to_int(row["volume"]),
                    source=self.name,
                    source_timestamp=stamp,
                )
            )
        return records

    def _frame_for(self, day: date) -> pd.DataFrame | None:
        if day in self._cache:
            return self._cache[day]
        frame: pd.DataFrame | None = None
        name = f"ind_close_all_{day.strftime('%d%m%Y')}.csv"
        path = _find_local(self.index_close_dir, [name], _date_tokens(day))
        if path is not None:
            frame = _normalise_index_close(_read_frame(path))
        elif self.download:
            frame = self._download(day, name)
        self._cache[day] = frame
        return frame

    def _download(self, day: date, name: str) -> pd.DataFrame | None:
        url = _INDEX_CLOSE_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))
        try:
            data = _http_get(url)
        except Exception as exc:  # noqa: BLE001 - a holiday simply has no file
            log.warning("no index-close file for %s (%s)", day, exc)
            return None
        try:
            self.index_close_dir.mkdir(parents=True, exist_ok=True)
            (self.index_close_dir / name).write_bytes(data)
        except OSError as exc:  # noqa: BLE001 - caching is best-effort
            log.debug("could not cache index close %s: %s", day, exc)
        _throttle()
        return _normalise_index_close(pd.read_csv(io.BytesIO(_strip_zip(data))))


def _throttle() -> None:
    """Space out consecutive live downloads to stay a polite archive client."""
    if settings.nse_download_sleep_seconds > 0:
        time.sleep(settings.nse_download_sleep_seconds)
