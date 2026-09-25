from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from nmi.core.enums import RunStatus
from nmi.core.models import (
    FundamentalMetric,
    IndexPrice,
    IngestionRun,
    Instrument,
    MomentumMetric,
    RelativeStrengthMetric,
    TechnicalIndicator,
    ValuationMetric,
)
from nmi.ingestion.backfill import IngestionService
from nmi.metrics.service import MetricsService, _benchmark_prices

START = date(2024, 1, 1)
END = date(2024, 6, 28)


def _sessions(start: date, end: date) -> int:
    n = (end - start).days + 1
    return sum(1 for i in range(n) if (start + timedelta(days=i)).weekday() < 5)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _stock_rows(session, model, symbol: str):
    return session.scalars(
        select(model)
        .join(Instrument, Instrument.id == model.instrument_id)
        .where(Instrument.symbol == symbol)
        .order_by(model.as_of)
    ).all()


def _seed_backtest(session) -> None:
    svc = IngestionService(session)
    svc.seed_universe()
    svc.backfill_corporate_actions(["NIFTY_50"], START, END)
    svc.backfill_prices(["NIFTY_50"], START, END)
    svc.backfill_fundamentals()


def test_metrics_e2e_populates_all_tables(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)

    idx = msvc.backfill_index_prices(["NIFTY_50"])
    assert idx.status == RunStatus.SUCCEEDED
    assert _count(sqlite_session, IndexPrice) == _sessions(date(2023, 7, 13), date(2024, 6, 28))

    fund = msvc.compute_fundamentals(["NIFTY_50"])
    assert fund.status == RunStatus.SUCCEEDED
    assert fund.items_failed == 0
    assert _count(sqlite_session, FundamentalMetric) == 198  # 11 income years x 18 metrics

    sessions = _sessions(START, END)
    tech = msvc.compute_technical(["NIFTY_50"], START, END)
    assert tech.status == RunStatus.SUCCEEDED
    assert tech.items_failed == 0
    assert tech.items_processed == 3 * sessions
    assert _count(sqlite_session, TechnicalIndicator) == 3 * sessions

    mom = msvc.compute_momentum(["NIFTY_50"], START, END)
    assert mom.status == RunStatus.SUCCEEDED
    assert mom.items_failed == 0
    assert _count(sqlite_session, MomentumMetric) == 3 * sessions
    assert _count(sqlite_session, RelativeStrengthMetric) == 3 * sessions

    val = msvc.compute_valuation(["NIFTY_50"], START, END)
    assert val.status == RunStatus.SUCCEEDED
    assert val.items_failed == 0
    assert _count(sqlite_session, ValuationMetric) == 3 * sessions


def test_metrics_as_of_values_and_no_lookahead(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])
    msvc.compute_fundamentals(["NIFTY_50"])
    msvc.compute_technical(["NIFTY_50"], START, END)
    msvc.compute_momentum(["NIFTY_50"], START, END)
    msvc.compute_valuation(["NIFTY_50"], START, END)

    tech = _stock_rows(sqlite_session, TechnicalIndicator, "RELIANCE")[-1]
    assert tech.as_of == END
    assert tech.sma20 is not None
    assert tech.rsi14 is not None

    mom = _stock_rows(sqlite_session, MomentumMetric, "RELIANCE")
    assert mom[-1].as_of == END
    assert mom[-1].return_1m is not None
    assert mom[-1].return_12m is None  # 252-session window > history available

    rs = _stock_rows(sqlite_session, RelativeStrengthMetric, "RELIANCE")
    assert rs[-1].benchmark == "NIFTY_50"
    assert rs[-1].rs_1m is not None

    bench_ret = _benchmark_prices(sqlite_session, "NIFTY_50")
    base = bench_ret[-1 - 21].close
    expected_rs = mom[-1].return_1m - (bench_ret[-1].close / base - 1) * 100
    assert rs[-1].rs_1m == pytest.approx(expected_rs, abs=1e-4)  # stored with 4dp scale

    val = _stock_rows(sqlite_session, ValuationMetric, "RELIANCE")
    assert val[-1].as_of == END
    assert val[-1].pe is not None
    assert val[-1].valuation_label in (
        "CHEAP",
        "FAIRLY_VALUED",
        "MODERATELY_EXPENSIVE",
        "EXPENSIVE",
    )


def test_compute_eod_chains_and_rerun_is_idempotent(sqlite_session, pointed_at_long_fixtures):
    _seed_backtest(session=sqlite_session)
    msvc = MetricsService(sqlite_session)
    msvc.backfill_index_prices(["NIFTY_50"])

    results = msvc.compute_eod(["NIFTY_50"], START, END)
    assert len(results) == 4
    assert all(r.status == RunStatus.SUCCEEDED for r in results)

    tech_before = _count(sqlite_session, TechnicalIndicator)
    mom_before = _count(sqlite_session, MomentumMetric)
    val_before = _count(sqlite_session, ValuationMetric)
    assert tech_before > 0 and mom_before > 0 and val_before > 0

    rerun = msvc.compute_eod(["NIFTY_50"], START, END)
    assert all(r.status == RunStatus.SUCCEEDED for r in rerun)
    assert _count(sqlite_session, TechnicalIndicator) == tech_before
    assert _count(sqlite_session, MomentumMetric) == mom_before
    assert _count(sqlite_session, ValuationMetric) == val_before

    runs = sqlite_session.scalars(select(IngestionRun)).all()
    assert all(r.status == RunStatus.SUCCEEDED for r in runs)
