from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from nmi.core.enums import RunStatus
from nmi.core.models import (
    BalanceSheet,
    CashFlow,
    CorporateAction,
    DailyPrice,
    IncomeStatement,
    IngestionError,
    IngestionRun,
    Instrument,
)
from nmi.ingestion.backfill import IngestionService


def _price_count(session, symbol: str) -> int:
    return session.scalar(
        select(func.count())
        .select_from(DailyPrice)
        .join(Instrument, Instrument.id == DailyPrice.instrument_id)
        .where(Instrument.symbol == symbol)
    )


def _price_row(session, symbol: str, trade_date: date) -> DailyPrice:
    return session.scalar(
        select(DailyPrice)
        .join(Instrument, Instrument.id == DailyPrice.instrument_id)
        .where(Instrument.symbol == symbol, DailyPrice.trade_date == trade_date)
    )


def test_backfill_prices_end_to_end(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()

    result = svc.backfill_prices(
        ["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28)
    )
    assert result.status == RunStatus.SUCCEEDED
    assert result.items_processed == 60  # 3 symbols x 20 days
    assert result.items_failed == 0
    assert {p["symbol"] for p in result.per_symbol} == {"RELIANCE", "TCS", "INFY"}

    for symbol in ("RELIANCE", "TCS", "INFY"):
        assert _price_count(sqlite_session, symbol) == 20


def test_split_adjustment_applied_and_persisted(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()
    svc.backfill_corporate_actions(["NIFTY_50"], date(2024, 6, 1), date(2024, 6, 30))
    svc.backfill_prices(["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28))

    pre = _price_row(sqlite_session, "RELIANCE", date(2024, 6, 14))
    assert pre.close == Decimal("2440")
    assert pre.adjusted_close == Decimal("1220")  # split 2:1 applied
    assert pre.adjustment_factor == Decimal("0.5")

    post = _price_row(sqlite_session, "RELIANCE", date(2024, 6, 17))
    assert post.close == Decimal("1220")
    assert post.adjustment_factor == Decimal("1")
    assert post.adjusted_close == Decimal("1220")


def test_dividend_adjustment_persisted(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()
    svc.backfill_corporate_actions(["NIFTY_50"], date(2024, 6, 1), date(2024, 6, 30))
    svc.backfill_prices(["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28))

    pre = _price_row(sqlite_session, "TCS", date(2024, 6, 20))  # day before ex-date
    assert pre.close == Decimal("3686.5000")
    assert pre.adjusted_close == Decimal("3677.5000")  # close - 9 dividend

    on_ex = _price_row(sqlite_session, "TCS", date(2024, 6, 21))
    assert on_ex.adjustment_factor == Decimal("1")


def test_source_and_quality_flags_persisted(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()
    svc.backfill_prices(["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28))

    row = _price_row(sqlite_session, "INFY", date(2024, 6, 3))
    assert row.source == "csv"
    assert row.source_timestamp is not None
    assert row.is_incomplete is False


def test_rerun_is_idempotent(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()
    svc.backfill_prices(["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28))
    second = svc.backfill_prices(["NIFTY_50"], date(2024, 6, 3), date(2024, 6, 28))
    assert second.status == RunStatus.SUCCEEDED
    assert _price_count(sqlite_session, "RELIANCE") == 20  # no duplicates
    runs = sqlite_session.scalars(select(IngestionRun)).all()
    assert len(runs) == 2
    assert all(r.status == RunStatus.SUCCEEDED for r in runs)


def test_actions_and_fundamentals_backfill(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()

    actions = svc.backfill_corporate_actions(
        ["NIFTY_50"], date(2024, 6, 1), date(2024, 6, 30)
    )
    assert actions.items_processed == 2
    assert sqlite_session.scalar(select(func.count()).select_from(CorporateAction)) == 2

    funds = svc.backfill_fundamentals()
    assert funds.status == RunStatus.SUCCEEDED
    assert sqlite_session.scalar(select(func.count()).select_from(IncomeStatement)) == 3
    assert sqlite_session.scalar(select(func.count()).select_from(BalanceSheet)) == 2
    assert sqlite_session.scalar(select(func.count()).select_from(CashFlow)) == 2


def test_incomplete_rows_still_stored_but_flagged(sqlite_session, pointed_at_fixtures):
    """Rows that fail validation are persisted with is_incomplete instead of being lost."""
    from decimal import Decimal

    from nmi.core.enums import Severity
    from nmi.ingestion.backfill import IngestionService
    from tests.conftest import make_candles

    svc = IngestionService(sqlite_session)
    svc.seed_universe()

    infy = sqlite_session.scalar(select(Instrument).where(Instrument.symbol == "INFY"))
    run = svc._start_run("test_incomplete", {"purpose": "incomplete-flag"})

    bad = make_candles("INFY", [Decimal(100)] * 3)
    bad[1].low = Decimal(999)  # violates low <= min(open, close)

    class BadProvider:
        name = "bad"

        def fetch_prices(self, **kwargs):
            return bad

    outcome = svc._ingest_one_instrument(
        run.id, infy, date(2024, 6, 3), date(2024, 6, 6), BadProvider()
    )
    assert outcome.stored == 3

    row = _price_row(sqlite_session, "INFY", bad[1].trade_date)
    assert row is not None
    assert row.is_incomplete is True

    errors = sqlite_session.scalars(
        select(IngestionError).where(IngestionError.run_id == run.id)
    ).all()
    assert any(e.code == "OHLC_INCONSISTENT" for e in errors)
    assert all(e.severity == Severity.ERROR for e in errors)
