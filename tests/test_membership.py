from __future__ import annotations

from datetime import date

from sqlalchemy import select

from nmi.core.models import Index, IndexMembership, Instrument
from nmi.ingestion.backfill import IngestionService, resolve_members


def test_seed_builds_universe_and_memberships(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    result = svc.seed_universe()

    assert result["instruments"] == 4
    assert set(result["indices"]) == {"NIFTY_50", "NIFTY_MIDCAP_150"}
    assert result["memberships_applied"] == 5

    instrument = sqlite_session.scalar(
        select(Instrument).join(Instrument.company).where(Instrument.symbol == "TORNTPOWER")
    )
    assert instrument is not None


def test_effective_dates_close_superseded_interval(
    sqlite_session, pointed_at_fixtures
):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()

    nifty_mid = sqlite_session.scalar(
        select(Index).where(Index.code == "NIFTY_MIDCAP_150")
    )
    torrent = sqlite_session.scalar(
        select(Instrument).where(Instrument.symbol == "TORNTPOWER")
    )
    memberships = list(
        sqlite_session.scalars(
            select(IndexMembership).where(
                IndexMembership.index_id == nifty_mid.id,
                IndexMembership.instrument_id == torrent.id,
            )
        )
    )
    # Two intervals: closed (2024 -> 2024-12-31) and re-opened (2025 -> present).
    assert len(memberships) == 2
    earlier = min(memberships, key=lambda m: m.effective_from)
    later = max(memberships, key=lambda m: m.effective_from)
    assert earlier.effective_from == date(2024, 1, 1)
    assert earlier.effective_to == date(2024, 12, 31)
    assert earlier.is_defunct is True
    assert later.effective_from == date(2025, 1, 1)
    assert later.effective_to is None
    assert later.is_defunct is False


def test_resolve_members_overlaps_interval(sqlite_session, pointed_at_fixtures):
    svc = IngestionService(sqlite_session)
    svc.seed_universe()

    during = resolve_members(
        sqlite_session, ["NIFTY_MIDCAP_150"], date(2024, 6, 1), date(2024, 6, 30)
    )
    assert {i.symbol for i in during} == {"TORNTPOWER"}

    after_drop = resolve_members(
        sqlite_session, ["NIFTY_MIDCAP_150"], date(2025, 6, 1), date(2025, 6, 30)
    )
    assert {i.symbol for i in after_drop} == {"TORNTPOWER"}

    nifty50 = resolve_members(
        sqlite_session, ["NIFTY_50"], date(2024, 6, 1), date(2024, 6, 30)
    )
    assert {i.symbol for i in nifty50} == {"RELIANCE", "TCS", "INFY"}
