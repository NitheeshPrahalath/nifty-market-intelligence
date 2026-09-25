"""Local-file (CSV) providers.

These adapters read a deterministic directory layout that mirrors a vendor's
export format, so the rest of the pipeline can be developed and tested without a
live vendor feed. They are also the canonical shape for vendor normalization.

Layout (under the settings ``*_dir`` roots):

    symbols.csv                     symbol,isin,company_name,exchange,sector,industry
    memberships/index_memberships.csv   index_code,symbol,effective_from,effective_to,exchange
    prices/{SYMBOL}.csv             date,open,high,low,close,volume,turnover
    corporate_actions/actions.csv   symbol,action_type,ex_date,ratio_numerator,
                                    ratio_denominator,dividend_amount,description,exchange
    fundamentals/income_statements.csv, balance_sheets.csv, cash_flows.csv
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from nmi.core.config import settings
from nmi.core.enums import CorporateActionType, Exchange, InstrumentType, PeriodType
from nmi.ingestion.records import (
    BalanceSheetRecord,
    Candle,
    CashFlowRecord,
    CorporateActionRecord,
    IncomeStatementRecord,
    IndexMembershipRecord,
    IndexPriceRecord,
    UniverseRow,
)

_NOW = datetime.utcnow


def _as_date(v) -> str | None:
    """Normalise pandas values that arrive as Timestamp/None/''/NaT."""
    if v is None:
        return None
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    return str(v).strip()


def _read(path: Path, required: bool = True) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"data file not found: {path}")
        return pd.DataFrame()
    return pd.read_csv(path)


class CSVPriceProvider:
    name = "csv"

    def __init__(self, prices_dir: Path | None = None):
        self.prices_dir = prices_dir or settings.prices_dir

    def fetch_prices(
        self,
        symbol: str,
        exchange: Exchange = Exchange.NSE,
        start: date | None = None,
        end: date | None = None,
        source_timestamp: datetime | None = None,
    ) -> list[Candle]:
        path = self.prices_dir / f"{symbol}.csv"
        df = _read(path)
        stamp = source_timestamp or _NOW()
        records: list[Candle] = []
        for _, row in df.iterrows():
            candle = Candle(
                symbol=symbol,
                exchange=exchange,
                trade_date=_as_date(row.get("date")),
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=_clean_int(row.get("volume")) if not pd.isna(row.get("volume")) else None,
                turnover=row.get("turnover"),
                source=self.name,
                source_timestamp=stamp,
            )
            if start and candle.trade_date < start:
                continue
            if end and candle.trade_date > end:
                continue
            records.append(candle)
        return records


class CSVIndexPriceProvider:
    """Reads benchmark index closes from per-index CSVs (e.g. ``NIFTY_50.csv``)."""

    name = "csv"

    def __init__(self, indexes_dir: Path | None = None):
        self.indexes_dir = indexes_dir or settings.indexes_dir

    def fetch_index_prices(
        self,
        index_code: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[IndexPriceRecord]:
        path = self.indexes_dir / f"{index_code}.csv"
        df = _read(path, required=False)
        records: list[IndexPriceRecord] = []
        stamp = _NOW()
        for _, row in df.iterrows():
            rec = IndexPriceRecord(
                index_code=index_code,
                trade_date=_as_date(row.get("date")),
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                close=row.get("close"),
                volume=_clean_int(row.get("volume")) if not pd.isna(row.get("volume")) else None,
                source=self.name,
                source_timestamp=stamp,
            )
            if start and rec.trade_date < start:
                continue
            if end and rec.trade_date > end:
                continue
            records.append(rec)
        return records


class CSVUniverseProvider:
    name = "csv"

    def __init__(self, symbols_file: Path | None = None):
        self.symbols_file = symbols_file or settings.data_dir / "symbols.csv"

    def fetch_universe(self) -> list[UniverseRow]:
        df = _read(self.symbols_file)
        rows: list[UniverseRow] = []
        for _, row in df.iterrows():
            rows.append(
                UniverseRow(
                    symbol=str(row["symbol"]).strip(),
                    isin=str(row["isin"]).strip(),
                    company_name=str(row["company_name"]).strip(),
                    exchange=Exchange(str(row.get("exchange") or Exchange.NSE.value)),
                    instrument_type=InstrumentType(
                        str(row.get("instrument_type") or InstrumentType.EQUITY.value)
                    ),
                    sector=_clean_str(row.get("sector")),
                    industry=_clean_str(row.get("industry")),
                )
            )
        return rows


class CSVMembershipProvider:
    name = "csv"

    def __init__(self, membership_dir: Path | None = None):
        self.membership_dir = membership_dir or settings.membership_dir
        if not isinstance(self.membership_dir, Path):
            self.membership_dir = Path(str(self.membership_dir))

    def fetch_membership(
        self, index_codes: Sequence[str] | None = None
    ) -> list[IndexMembershipRecord]:
        path = self.membership_dir / "index_memberships.csv"
        df = _read(path)
        wanted = set(index_codes) if index_codes else None
        records: list[IndexMembershipRecord] = []
        for _, row in df.iterrows():
            code = str(row["index_code"]).strip()
            if wanted and code not in wanted:
                continue
            records.append(
                IndexMembershipRecord(
                    index_code=code,
                    symbol=str(row["symbol"]).strip(),
                    exchange=Exchange(
                        str(row.get("exchange") or Exchange.NSE.value)
                    ),
                    effective_from=_as_date(row.get("effective_from")),
                    effective_to=_as_date(row.get("effective_to")) or None,
                )
            )
        return records


class CSVCorporateActionProvider:
    name = "csv"

    def __init__(self, actions_dir: Path | None = None):
        self.actions_dir = actions_dir or settings.corporate_actions_dir

    def fetch_actions(
        self,
        symbols: Sequence[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[CorporateActionRecord]:
        path = self.actions_dir / "actions.csv"
        df = _read(path)
        wanted = set(symbols) if symbols else None
        records: list[CorporateActionRecord] = []
        for _, row in df.iterrows():
            symbol = str(row["symbol"]).strip()
            if wanted and symbol not in wanted:
                continue
            rec = CorporateActionRecord(
                symbol=symbol,
                exchange=Exchange(str(row.get("exchange") or Exchange.NSE.value)),
                action_type=CorporateActionType(str(row["action_type"]).strip()),
                ex_date=_as_date(row.get("ex_date")),
                record_date=_as_date(row.get("record_date")),
                pay_date=_as_date(row.get("pay_date")),
                ratio_numerator=_clean_int(row.get("ratio_numerator")),
                ratio_denominator=_clean_int(row.get("ratio_denominator")),
                dividend_amount=row.get("dividend_amount"),
                description=_clean_str(row.get("description")),
                source=self.name,
                source_timestamp=_NOW(),
            )
            if start and rec.ex_date < start:
                continue
            if end and rec.ex_date > end:
                continue
            records.append(rec)
        return records


class CSVFundamentalProvider:
    name = "csv"

    def __init__(self, fundamentals_dir: Path | None = None):
        self.fundamentals_dir = fundamentals_dir or settings.fundamentals_dir

    def fetch_income_statements(
        self, isins: Sequence[str] | None = None
    ) -> list[IncomeStatementRecord]:
        path = self.fundamentals_dir / "income_statements.csv"
        df = _read(path)
        wanted = set(isins) if isins else None
        out: list[IncomeStatementRecord] = []
        for _, row in df.iterrows():
            isin = str(row["isin"]).strip()
            if wanted and isin not in wanted:
                continue
            out.append(self._income(row))
        return out

    def fetch_balance_sheets(
        self, isins: Sequence[str] | None = None
    ) -> list[BalanceSheetRecord]:
        path = self.fundamentals_dir / "balance_sheets.csv"
        df = _read(path)
        wanted = set(isins) if isins else None
        out: list[BalanceSheetRecord] = []
        for _, row in df.iterrows():
            isin = str(row["isin"]).strip()
            if wanted and isin not in wanted:
                continue
            out.append(self._balance(row))
        return out

    def fetch_cash_flows(
        self, isins: Sequence[str] | None = None
    ) -> list[CashFlowRecord]:
        path = self.fundamentals_dir / "cash_flows.csv"
        df = _read(path)
        wanted = set(isins) if isins else None
        out: list[CashFlowRecord] = []
        for _, row in df.iterrows():
            isin = str(row["isin"]).strip()
            if wanted and isin not in wanted:
                continue
            out.append(self._cash(row))
        return out

    @staticmethod
    def _income(row) -> IncomeStatementRecord:
        return IncomeStatementRecord(
            isin=str(row["isin"]).strip(),
            period_end=_as_date(row.get("period_end")),
            period_type=PeriodType(str(row.get("period_type") or "ANNUAL").upper()),
            fiscal_year=_clean_str(row.get("fiscal_year")),
            total_revenue=row.get("total_revenue"),
            operating_revenue=row.get("operating_revenue"),
            net_profit=row.get("net_profit"),
            eps=row.get("eps"),
            ebitda=row.get("ebitda"),
            ebitda_margin_pct=row.get("ebitda_margin_pct"),
            net_margin_pct=row.get("net_margin_pct"),
            shares_outstanding=_clean_int(row.get("shares_outstanding")),
            source="csv",
            source_timestamp=_NOW(),
        )

    @staticmethod
    def _balance(row) -> BalanceSheetRecord:
        return BalanceSheetRecord(
            isin=str(row["isin"]).strip(),
            period_end=_as_date(row.get("period_end")),
            period_type=PeriodType(str(row.get("period_type") or "ANNUAL").upper()),
            fiscal_year=_clean_str(row.get("fiscal_year")),
            total_assets=row.get("total_assets"),
            total_liabilities=row.get("total_liabilities"),
            total_debt=row.get("total_debt"),
            net_debt=row.get("net_debt"),
            net_worth=row.get("net_worth"),
            current_assets=row.get("current_assets"),
            current_liabilities=row.get("current_liabilities"),
            source="csv",
            source_timestamp=_NOW(),
        )

    @staticmethod
    def _cash(row) -> CashFlowRecord:
        return CashFlowRecord(
            isin=str(row["isin"]).strip(),
            period_end=_as_date(row.get("period_end")),
            period_type=PeriodType(str(row.get("period_type") or "ANNUAL").upper()),
            fiscal_year=_clean_str(row.get("fiscal_year")),
            operating_cash_flow=row.get("operating_cash_flow"),
            investing_cash_flow=row.get("investing_cash_flow"),
            financing_cash_flow=row.get("financing_cash_flow"),
            free_cash_flow=row.get("free_cash_flow"),
            source="csv",
            source_timestamp=_NOW(),
        )


def _clean_str(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _clean_int(v) -> int | None:
    if v is None or pd.isna(v):
        return None
    return int(v)
