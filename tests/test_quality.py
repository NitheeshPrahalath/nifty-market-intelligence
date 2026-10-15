"""Tests for the Phase-10 data-quality reconciliation layer.

Checks are pure functions of a :class:`CheckContext`, so most of these tests
build the smallest database that triggers a single finding and assert on that
finding alone. One integration test drives the full service to prove the run and
its issues are persisted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select

from nmi.core.enums import Exchange, InstrumentType, RunStatus, Severity
from nmi.core.models import (
    Company,
    DailyPrice,
    DataQualityIssue,
    DataQualityRun,
    Index,
    IndexMembership,
    IndexPrice,
    Instrument,
)
from nmi.quality import (
    CHECKS,
    CheckContext,
    QualityService,
    issues_for_run,
    latest_run,
)
from nmi.quality.checks import (
    check_adjustments,
    check_completeness,
    check_gaps,
    check_index_coverage,
    check_ohlc,
    check_price_presence,
    check_staleness,
)

AS_OF = date(2024, 6, 28)  # a Friday
START = date(2024, 6, 17)  # the preceding Monday
STAMP = datetime(2024, 6, 28, 12, 0, tzinfo=UTC)


def _instrument(session, symbol: str, *, isin: str | None = None) -> Instrument:
    company = Company(isin=isin or f"INE{symbol:0<9}"[:12], name=f"{symbol} Ltd")
    session.add(company)
    session.flush()
    instrument = Instrument(
        company_id=company.id,
        exchange=Exchange.NSE,
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _price(session, instrument: Instrument, day: date, **overrides) -> DailyPrice:
    values = {
        "close": 100,
        "open": 100,
        "high": 101,
        "low": 99,
        "source": "test",
        "source_timestamp": STAMP,
    }
    values.update(overrides)
    row = DailyPrice(instrument_id=instrument.id, trade_date=day, **values)
    session.add(row)
    return row


def _ctx(session, members, **overrides) -> CheckContext:
    values = {
        "session": session,
        "index_code": "NIFTY_50",
        "index_id": None,
        "members": members,
        "start": START,
        "end": AS_OF,
        "as_of": AS_OF,
        "stale_business_days": 4,
        "adjustment_tolerance_pct": 0.5,
    }
    values.update(overrides)
    return CheckContext(**values)


def test_price_presence_flags_only_the_missing_member(sqlite_session):
    session = sqlite_session
    present = _instrument(session, "AAA")
    missing = _instrument(session, "BBB")
    _price(session, present, AS_OF)
    session.flush()

    issues = check_price_presence(_ctx(session, [present, missing]))

    assert [i.check_code for i in issues] == ["PRICE_MISSING"]
    assert issues[0].severity == Severity.ERROR
    assert issues[0].scope == "BBB"


def test_staleness_flags_an_old_last_price(sqlite_session):
    session = sqlite_session
    fresh = _instrument(session, "AAA")
    stale = _instrument(session, "BBB")
    _price(session, fresh, AS_OF)
    _price(session, stale, date(2024, 6, 18))
    session.flush()

    issues = check_staleness(_ctx(session, [fresh, stale]))

    assert [i.scope for i in issues] == ["BBB"]
    assert issues[0].check_code == "PRICE_STALE"
    assert issues[0].severity == Severity.WARNING


def test_completeness_flags_incomplete_rows(sqlite_session):
    session = sqlite_session
    instrument = _instrument(session, "AAA")
    _price(session, instrument, AS_OF, is_incomplete=True)
    _price(session, instrument, date(2024, 6, 27), is_incomplete=True)
    _price(session, instrument, date(2024, 6, 26))
    session.flush()

    issues = check_completeness(_ctx(session, [instrument]))

    assert len(issues) == 1
    assert issues[0].check_code == "PRICE_INCOMPLETE"
    assert issues[0].details["incomplete_rows"] == 2


def test_gaps_flag_a_run_of_missing_business_days(sqlite_session):
    session = sqlite_session
    instrument = _instrument(session, "AAA")
    _price(session, instrument, date(2024, 6, 17))
    _price(session, instrument, date(2024, 6, 28))  # 8 business days missing
    session.flush()

    issues = check_gaps(_ctx(session, [instrument]))

    assert len(issues) == 1
    assert issues[0].check_code == "PRICE_GAP"
    assert issues[0].details["missing_count"] == 8


def test_adjustments_flag_drift_beyond_tolerance(sqlite_session):
    session = sqlite_session
    instrument = _instrument(session, "AAA")
    _price(session, instrument, AS_OF, close=100, adjustment_factor=2, adjusted_close=200)
    _price(
        session,
        instrument,
        date(2024, 6, 27),
        close=100,
        adjustment_factor=2,
        adjusted_close=150,  # 25% drift
    )
    session.flush()

    issues = check_adjustments(_ctx(session, [instrument]))

    assert len(issues) == 1
    assert issues[0].check_code == "ADJUSTMENT_MISMATCH"
    assert issues[0].details["count"] == 1


def test_ohlc_flags_inconsistent_bars(sqlite_session):
    session = sqlite_session
    instrument = _instrument(session, "AAA")
    _price(session, instrument, AS_OF, open=100, high=90, low=105, close=100)
    session.flush()

    issues = check_ohlc(_ctx(session, [instrument]))

    assert len(issues) == 1
    assert issues[0].check_code == "OHLC_INCONSISTENT"


def test_index_coverage_missing_then_stale(sqlite_session):
    session = sqlite_session
    index = Index(code="NIFTY_50", name="Nifty 50")
    session.add(index)
    session.flush()

    missing = check_index_coverage(_ctx(session, [], index_id=index.id))
    assert [i.check_code for i in missing] == ["INDEX_PRICE_MISSING"]

    session.add(
        IndexPrice(
            index_id=index.id,
            trade_date=date(2024, 6, 18),  # in-window but older than the cutoff
            close=22000,
            source="test",
            source_timestamp=STAMP,
        )
    )
    session.flush()

    stale = check_index_coverage(_ctx(session, [], index_id=index.id))
    assert [i.check_code for i in stale] == ["INDEX_PRICE_STALE"]
    assert stale[0].severity == Severity.WARNING


def test_service_reconcile_persists_a_run_and_issues(sqlite_session):
    session = sqlite_session
    index = Index(code="NIFTY_50", name="Nifty 50")
    session.add(index)
    session.flush()

    good = _instrument(session, "AAA")
    bad = _instrument(session, "BBB")  # no prices -> PRICE_MISSING
    session.add_all(
        [
            IndexMembership(
                index_id=index.id, instrument_id=good.id, effective_from=date(2024, 1, 1)
            ),
            IndexMembership(
                index_id=index.id, instrument_id=bad.id, effective_from=date(2024, 1, 1)
            ),
            IndexPrice(
                index_id=index.id,
                trade_date=AS_OF,
                close=22000,
                source="test",
                source_timestamp=STAMP,
            ),
        ]
    )
    for day in (date(2024, 6, 17), date(2024, 6, 18), AS_OF):
        _price(session, good, day)
    session.commit()

    result = QualityService(session).reconcile(["NIFTY_50"], as_of=AS_OF)

    assert result.status == RunStatus.SUCCEEDED
    assert result.checks_run == len(CHECKS)
    assert any(i.check_code == "PRICE_MISSING" for i in result.issues)
    assert not result.ok

    run = latest_run(session)
    assert run is not None
    assert run.id == result.run_id
    assert run.checks_failed >= 1
    persisted = issues_for_run(session, run.id)
    assert {i.check_code for i in persisted} == {i.check_code for i in result.issues}
    stored = session.scalar(select(func.count()).select_from(DataQualityIssue))
    assert stored == len(result.issues)


def test_service_handles_unknown_index(sqlite_session):
    session = sqlite_session
    _instrument(session, "AAA")
    session.commit()

    result = QualityService(session).reconcile(["NIFTY_50"], as_of=AS_OF)

    assert [i.check_code for i in result.issues] == ["INDEX_UNKNOWN"]
    assert result.checks_run == 0


def test_service_reports_no_price_data(sqlite_session):
    session = sqlite_session
    session.commit()

    result = QualityService(session).reconcile(["NIFTY_50"])

    assert result.as_of is None
    assert [i.check_code for i in result.issues] == ["NO_PRICE_DATA"]
    assert session.scalar(select(func.count()).select_from(DataQualityRun)) == 1
