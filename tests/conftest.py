from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import nmi.core.models  # noqa: F401 - register tables
from nmi.core.db import Base

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _silence_third_party_logs():
    import logging

    logging.getLogger("urllib3").setLevel(logging.WARNING)


@pytest.fixture()
def sqlite_session():
    """Fresh in-memory SQLite database with the full schema per test."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def pointed_at_fixtures():
    """Point every CSV-defaulted setting at the test fixture directory."""
    from nmi.core.config import settings

    original = {
        "data_dir": settings.data_dir,
        "membership_dir": settings.membership_dir,
        "corporate_actions_dir": settings.corporate_actions_dir,
        "fundamentals_dir": settings.fundamentals_dir,
    }
    settings.data_dir = FIXTURES
    settings.membership_dir = FIXTURES / "memberships"
    settings.corporate_actions_dir = FIXTURES / "corporate_actions"
    settings.fundamentals_dir = FIXTURES / "fundamentals"
    try:
        yield settings
    finally:
        settings.data_dir = original["data_dir"]
        settings.membership_dir = original["membership_dir"]
        settings.corporate_actions_dir = original["corporate_actions_dir"]
        settings.fundamentals_dir = original["fundamentals_dir"]


def _bizdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_candles(
    symbol: str,
    closes: list[Decimal | float | int],
    opens: list | None = None,
    highs: list | None = None,
    lows: list | None = None,
    volumes: list | None = None,
    start: date = date(2024, 6, 3),
    source: str = "test",
    source_timestamp=None,
):
    """Build valid ``Candle`` rows on consecutive business days."""
    from nmi.core.enums import Exchange
    from nmi.ingestion.records import Candle

    dates = _bizdays(start, len(closes))
    puts = []
    for i, close in enumerate(closes):
        puts.append(
            Candle(
                symbol=symbol,
                exchange=Exchange.NSE,
                trade_date=dates[i],
                open=opens[i] if opens else close,
                high=highs[i] if highs else close,
                low=lows[i] if lows else close,
                close=Decimal(str(close)),
                volume=(volumes[i] if volumes else 100000),
                turnover=None,
                source=source,
                source_timestamp=source_timestamp or datetime.utcnow(),
            )
        )
    return puts


def make_split_action(symbol, ex_date, num=2, den=1, kind="SPLIT"):
    from nmi.core.enums import CorporateActionType as CAT
    from nmi.ingestion.records import CorporateActionRecord

    return CorporateActionRecord(
        symbol=symbol,
        action_type=CAT(kind),
        ex_date=ex_date,
        ratio_numerator=num,
        ratio_denominator=den,
        source="test",
        source_timestamp=datetime.utcnow(),
    )


def make_dividend(symbol, ex_date, amount: Decimal):
    from nmi.core.enums import CorporateActionType as CAT
    from nmi.ingestion.records import CorporateActionRecord

    return CorporateActionRecord(
        symbol=symbol,
        action_type=CAT.DIVIDEND,
        ex_date=ex_date,
        dividend_amount=amount,
        source="test",
        source_timestamp=datetime.utcnow(),
    )
