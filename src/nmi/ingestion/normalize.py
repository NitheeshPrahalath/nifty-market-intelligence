"""Normalization of raw provider output into canonical, deduplicated series.

The pipeline contract: providers return *raw* records; normalization makes them
*canonical* (sorted, de-duplicated, standardised types); validation flags bad
data; only clean rows are persisted.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from nmi.ingestion.records import Candle


def normalize_candles(records: Iterable[Candle]) -> list[Candle]:
    """Deduplicate by (trade_date) and sort ascending.

    Later records win on equal dates (i.e., a re-fetch supersedes an earlier
    fetch for the same day). A count of dropped duplicates is returned via the
    caller comparing lengths if needed.
    """
    deduped: dict[date, Candle] = {}
    for candle in records:
        deduped[candle.trade_date] = candle
    return sorted(deduped.values(), key=lambda c: c.trade_date)


def count_duplicates(records: Iterable[Candle]) -> int:
    seen: dict[date, int] = {}
    for candle in records:
        seen[candle.trade_date] = seen.get(candle.trade_date, 0) + 1
    return sum(v - 1 for v in seen.values() if v > 1)


def normalize_membership(
    records: Iterable[object],
) -> list:
    records = list(records)
    # Deterministic ordering: index, symbol, effective_from.
    return sorted(
        records, key=lambda r: (r.index_code, r.symbol, r.effective_from)
    )


def normalize_actions(records: Iterable[object]) -> list:
    return sorted(
        records, key=lambda r: (r.symbol, r.ex_date, r.action_type.value)
    )
