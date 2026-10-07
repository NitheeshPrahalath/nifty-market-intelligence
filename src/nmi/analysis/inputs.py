"""As-of market inputs shared by the live pipeline and the backtester (Phase 6).

Every value a strategy or metric reads carries the date it was true for, and
this module is the single place that answers "what did we know on day D?":

* **Prices and technical rows** are read for ``trade_date``/``as_of`` <= D only.
* **Fundamentals** are step functions — the last ``as_of`` at or before D — so a
  quarter that had not been reported yet cannot leak into an earlier decision.
* **Index membership** is resolved through the ``[effective_from, effective_to]``
  intervals, so an instrument is only tradable while it was actually a member.

The live pipeline (:mod:`nmi.metrics.service`) and the Phase-6 backtester both
build their contexts from here, which is what keeps a backtest and the live
pipeline making the same decision on the same data.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from nmi.analysis.common import as_float
from nmi.core.models import (
    Company,
    DailyPrice,
    FundamentalMetric,
    Index,
    IndexMembership,
    Instrument,
    MomentumMetric,
    RelativeStrengthMetric,
    TechnicalIndicator,
    ValuationMetric,
)
from nmi.ingestion import store

TECHNICAL_FIELDS = (
    "sma20",
    "sma50",
    "sma200",
    "atr14",
    "rsi14",
    "macd_hist",
    "adx14",
    "roc10",
    "hist_vol_20",
    "hist_vol_60",
    "drawdown_pct",
    "dist_from_high_52w_pct",
    "dist_from_low_52w_pct",
    "volume_ratio",
    "trend_state",
    "breakout_52w",
    "high_52w",
)
MOMENTUM_FIELDS = ("return_1m", "return_3m", "return_6m", "return_12m")
RS_FIELDS = ("rs_1m", "rs_3m", "rs_6m", "rs_12m", "rs_trend")
VALUATION_FIELDS = (
    "pe",
    "pb",
    "pe_percentile_3y",
    "pb_percentile_3y",
    "ev_ebitda_percentile_3y",
    "valuation_label",
)


def row_dict(row, fields: Sequence[str]) -> dict:
    """Snapshot the requested columns, unwrapping enums to their values."""
    out = {}
    for field_name in fields:
        value = getattr(row, field_name, None)
        out[field_name] = getattr(value, "value", value)
    return out


def load_metric_rows(
    session: Session,
    model,
    instrument_ids: Sequence[int],
    start: date,
    end: date,
    calc_version: str,
    fields: Sequence[str],
    benchmark: str | None = None,
) -> dict[tuple[int, date], dict]:
    if not instrument_ids:
        return {}
    stmt = select(model).where(
        model.instrument_id.in_(instrument_ids),
        model.as_of >= start,
        model.as_of <= end,
        model.calc_version == calc_version,
    )
    if benchmark is not None:
        stmt = stmt.where(model.benchmark == benchmark)
    return {
        (row.instrument_id, row.as_of): row_dict(row, fields)
        for row in session.scalars(stmt).all()
    }


def load_closes(
    session: Session, instrument_ids: Sequence[int], start: date, end: date
) -> dict[tuple[int, date], float]:
    """Adjusted closes — the same basis every indicator is computed on."""
    if not instrument_ids:
        return {}
    stmt = select(
        DailyPrice.trade_date,
        DailyPrice.instrument_id,
        DailyPrice.close,
        DailyPrice.adjusted_close,
    ).where(
        DailyPrice.instrument_id.in_(instrument_ids),
        DailyPrice.trade_date >= start,
        DailyPrice.trade_date <= end,
    )
    return {
        (iid, d): _adjusted(close, adjusted)
        for d, iid, close, adjusted in session.execute(stmt).all()
        if close is not None
    }


def _adjusted(close, adjusted) -> float:
    """The tradable price for one bar, on the adjusted basis.

    Indicators are computed from ``adjusted_close`` when it exists; prices that
    travel with them (the context's ``close``, the backtester's bars) must use
    the same basis, or a price level computed from an ATR would be compared
    against a price from a different scale entirely.
    """
    if adjusted is not None:
        return float(adjusted)
    return float(close)


def _scale(value, factor: float) -> float | None:
    if value is None:
        return None
    return float(value) * factor


def load_bars(
    session: Session, instrument_ids: Sequence[int], start: date, end: date
) -> dict[tuple[int, date], dict]:
    """Open/high/low/close bars, needed to simulate fills and intraday stops.

    All four legs are returned on the adjusted basis, matching the indicators
    and the context's close, so a simulated fill sits on the same axis as the
    stop and target it is judged against.
    """
    if not instrument_ids:
        return {}
    stmt = select(
        DailyPrice.trade_date,
        DailyPrice.instrument_id,
        DailyPrice.open,
        DailyPrice.high,
        DailyPrice.low,
        DailyPrice.close,
        DailyPrice.adjusted_close,
    ).where(
        DailyPrice.instrument_id.in_(instrument_ids),
        DailyPrice.trade_date >= start,
        DailyPrice.trade_date <= end,
    )
    bars: dict[tuple[int, date], dict] = {}
    for trade_date, instrument_id, open_, high, low, close, adjusted in session.execute(
        stmt
    ).all():
        if close is None:
            continue
        basis = float(adjusted) / float(close) if adjusted is not None else 1.0
        bars[(instrument_id, trade_date)] = {
            "open": _scale(open_, basis),
            "high": _scale(high, basis),
            "low": _scale(low, basis),
            "close": _adjusted(close, adjusted),
        }
    return bars


def load_fundamentals(
    session: Session, company_ids: Sequence[int]
) -> dict[int, dict[str, list[tuple[date, float | None]]]]:
    if not company_ids:
        return {}
    stmt = select(
        FundamentalMetric.company_id,
        FundamentalMetric.metric,
        FundamentalMetric.as_of,
        FundamentalMetric.value,
    ).where(FundamentalMetric.company_id.in_(company_ids))
    grouped: dict[int, dict[str, list[tuple[date, float | None]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for company_id, metric, as_of, value in session.execute(stmt).all():
        grouped[company_id][metric].append((as_of, value))
    for metrics in grouped.values():
        for series in metrics.values():
            series.sort(key=lambda item: item[0])
    return {company_id: dict(metrics) for company_id, metrics in grouped.items()}


def membership_intervals(
    session: Session, index_codes: Sequence[str], start: date, end: date
) -> dict[int, list[tuple[date, date | None, int]]]:
    stmt = (
        select(IndexMembership)
        .join(Index, Index.id == IndexMembership.index_id)
        .where(
            Index.code.in_(index_codes),
            IndexMembership.effective_from <= end,
        )
    )
    out: dict[int, list[tuple[date, date | None, int]]] = defaultdict(list)
    for row in session.scalars(stmt).all():
        if row.effective_to is not None and row.effective_to < start:
            continue
        out[row.index_id].append((row.effective_from, row.effective_to, row.instrument_id))
    return dict(out)


def members_on(
    intervals: Sequence[tuple[date, date | None, int]], as_of: date
) -> list[int]:
    """Instrument ids whose membership interval covers ``as_of``."""
    return [
        instrument_id
        for effective_from, effective_to, instrument_id in intervals
        if effective_from <= as_of and (effective_to is None or effective_to >= as_of)
    ]


def member_instrument_ids(
    intervals: dict[int, list[tuple[date, date | None, int]]]
) -> set[int]:
    """Every instrument with a membership interval overlapping the window."""
    return {
        instrument_id
        for rows in intervals.values()
        for (_from, _to, instrument_id) in rows
    }


@dataclass(slots=True)
class AnalysisInputs:
    """Phase-2 metric rows loaded once and reused by every consumer."""

    index_ids: dict[str, int]
    intervals: dict[int, list[tuple[date, date | None, int]]]
    company_by_instrument: dict[int, int]
    sector_by_instrument: dict[int, int | None]
    technical: dict[tuple[int, date], dict]
    closes: dict[tuple[int, date], float]
    momentum: dict[tuple[int, date], dict]
    rs: dict[tuple[int, date], dict]
    valuation: dict[tuple[int, date], dict]
    fundamentals: dict[int, dict[str, list[tuple[date, float | None]]]]
    start: date
    end: date
    bars: dict[tuple[int, date], dict] = None  # type: ignore[assignment]
    benchmark: str | None = None

    def __post_init__(self) -> None:
        if self.bars is None:
            self.bars = {}

    def as_of_dates(self) -> list[date]:
        return sorted({as_of for _iid, as_of in self.technical})

    def technical_at(self, instrument_id: int, as_of: date) -> dict | None:
        row = self.technical.get((instrument_id, as_of))
        if row is None:
            return None
        return {
            **row,
            "as_of": as_of,
            "close": self.closes.get((instrument_id, as_of)),
        }

    def bar_at(self, instrument_id: int, as_of: date) -> dict | None:
        return self.bars.get((instrument_id, as_of))

    def fundamentals_at(self, company_id: int, as_of: date) -> dict:
        metrics = self.fundamentals.get(company_id, {})
        out: dict[str, float | None] = {}
        for metric, series in metrics.items():
            idx = bisect_right([d for d, _v in series], as_of) - 1
            if idx >= 0:
                out[metric] = series[idx][1]
        return out

    def is_member(self, instrument_id: int, as_of: date) -> bool:
        """True when the instrument belonged to one of the tracked indices."""
        return any(
            instrument_id in members_on(rows, as_of) for rows in self.intervals.values()
        )

    def primary_index(self, instrument_id: int, as_of: date) -> int | None:
        for index_id in self.index_ids.values():
            if instrument_id in members_on(self.intervals.get(index_id, []), as_of):
                return index_id
        return None

    def strategy_context(
        self,
        instrument_id: int,
        as_of: date,
        scoring: dict | None = None,
        regime: dict | None = None,
        sector: dict | None = None,
    ):
        """The flat strategy context for one instrument-day (Phase 4 input)."""
        from nmi.strategies import StrategyContext

        technical = self.technical_at(instrument_id, as_of)
        if technical is None:
            return None
        values: dict = {k: v for k, v in technical.items() if k != "as_of"}
        values.update(self.momentum.get((instrument_id, as_of)) or {})
        values.update(self.rs.get((instrument_id, as_of)) or {})
        values.update(self.valuation.get((instrument_id, as_of)) or {})
        company_id = self.company_by_instrument.get(instrument_id)
        if company_id is not None:
            values.update(self.fundamentals_at(company_id, as_of))
        if scoring:
            values.update(scoring)
        if regime:
            values.update(regime)
        if sector:
            values.update(sector)
        close = as_float(technical.get("close"))
        sma20 = as_float(technical.get("sma20"))
        if close is not None and sma20:
            values["dist_from_sma20_pct"] = (close / sma20 - 1) * 100
        return StrategyContext(
            instrument_id=instrument_id,
            as_of=as_of,
            values=values,
            close=close,
            atr14=as_float(technical.get("atr14")),
        )


def load_analysis_inputs(
    session: Session,
    index_codes: Sequence[str],
    start: date | None,
    end: date | None,
    calc_version: str,
    *,
    with_bars: bool = False,
) -> AnalysisInputs:
    """Load every Phase-2 metric row the Phase-3/4/6 engines need, once."""
    start = start or date(1900, 1, 1)
    end = end or date(2900, 1, 1)
    index_ids = store.ensure_indices(session, list(index_codes))
    intervals = membership_intervals(session, index_codes, start, end)
    instrument_ids = sorted({iid for rows in intervals.values() for (_f, _t, iid) in rows})

    company_by_instrument: dict[int, int] = {}
    sector_by_instrument: dict[int, int | None] = {}
    if instrument_ids:
        rows = session.execute(
            select(Instrument.id, Instrument.company_id, Company.sector_id)
            .join(Company, Company.id == Instrument.company_id)
            .where(Instrument.id.in_(instrument_ids))
        ).all()
        company_by_instrument = {iid: cid for iid, cid, _s in rows}
        sector_by_instrument = {iid: sector for iid, _c, sector in rows}

    from nmi.core.config import settings

    benchmark = settings.rs_benchmarks[0] if settings.rs_benchmarks else None
    return AnalysisInputs(
        index_ids=index_ids,
        intervals=intervals,
        company_by_instrument=company_by_instrument,
        sector_by_instrument=sector_by_instrument,
        technical=load_metric_rows(
            session,
            TechnicalIndicator,
            instrument_ids,
            start,
            end,
            calc_version,
            TECHNICAL_FIELDS,
        ),
        closes=load_closes(session, instrument_ids, start, end),
        momentum=load_metric_rows(
            session,
            MomentumMetric,
            instrument_ids,
            start,
            end,
            calc_version,
            MOMENTUM_FIELDS,
        ),
        rs=load_metric_rows(
            session,
            RelativeStrengthMetric,
            instrument_ids,
            start,
            end,
            calc_version,
            RS_FIELDS,
            benchmark=benchmark,
        ),
        valuation=load_metric_rows(
            session,
            ValuationMetric,
            instrument_ids,
            start,
            end,
            calc_version,
            VALUATION_FIELDS,
        ),
        fundamentals=load_fundamentals(session, sorted(set(company_by_instrument.values()))),
        start=start,
        end=end,
        bars=load_bars(session, instrument_ids, start, end) if with_bars else {},
        benchmark=benchmark,
    )


SCORING_FIELDS = (
    "composite_score",
    "score_label",
    "preferred_horizon",
    "trend_score",
    "momentum_score",
    "relative_strength_score",
    "quality_score",
    "growth_score",
    "valuation_score",
    "risk_score",
    "liquidity_score",
    "horizon_score",
    "sector_score",
)


def scoring_map(
    session: Session, instrument_ids: Sequence[int], start: date, end: date, calc_version: str
) -> dict[tuple[int, date], dict]:
    if not instrument_ids:
        return {}
    from nmi.core.models import ScoringSnapshot

    stmt = select(ScoringSnapshot).where(
        ScoringSnapshot.instrument_id.in_(list(instrument_ids)),
        ScoringSnapshot.as_of >= start,
        ScoringSnapshot.as_of <= end,
        ScoringSnapshot.calc_version == calc_version,
    )
    return {
        (row.instrument_id, row.as_of): row_dict(row, SCORING_FIELDS)
        for row in session.scalars(stmt).all()
    }


def regime_map(
    session: Session, start: date, end: date, calc_version: str
) -> dict[tuple[int, date], dict]:
    from nmi.core.models import MarketRegime

    stmt = select(MarketRegime).where(
        MarketRegime.as_of >= start,
        MarketRegime.as_of <= end,
        MarketRegime.calc_version == calc_version,
    )
    return {
        (row.index_id, row.as_of): row_dict(row, ("regime_score", "regime_label"))
        for row in session.scalars(stmt).all()
    }


def sector_state_map(
    session: Session, index_codes: Sequence[str], start: date, end: date
) -> dict[tuple[int, object, date], str]:
    from nmi.core.models import SectorMetric

    stmt = select(SectorMetric).where(
        SectorMetric.as_of >= start, SectorMetric.as_of <= end
    )
    if not index_codes:
        return {}
    return {
        (row.index_id, row.sector_id, row.as_of): row.sector_state.value
        for row in session.scalars(stmt).all()
    }


def benchmark_prices(session: Session, code: str) -> list[tuple[date, float]]:
    """Index closes for one index code, oldest first."""
    from nmi.core.models import IndexPrice

    stmt = (
        select(IndexPrice)
        .join(Index, Index.id == IndexPrice.index_id)
        .where(Index.code == code)
        .order_by(IndexPrice.trade_date)
    )
    return [(row.trade_date, float(row.close)) for row in session.scalars(stmt).all()]


def corporate_actions_between(
    session: Session, instrument_id: int, start: date, end: date
) -> list[dict]:
    """Corporate actions with an ex-date in ``(start, end]`` — as-of safe."""
    from nmi.core.models import CorporateAction

    stmt = (
        select(CorporateAction)
        .where(
            CorporateAction.instrument_id == instrument_id,
            CorporateAction.ex_date > start,
            CorporateAction.ex_date <= end,
        )
        .order_by(CorporateAction.ex_date)
    )
    return [
        {
            "action_type": row.action_type.value,
            "ex_date": row.ex_date,
            "description": row.description,
        }
        for row in session.scalars(stmt).all()
    ]
