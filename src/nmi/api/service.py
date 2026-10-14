"""Read-only query service backing the API.

Every method here is a plain function over a ``Session`` returning typed schema
objects: no HTTP, no global state, no caching. That keeps the query layer
directly unit-testable and lets the gateway own the cross-cutting concerns
(auth, caching, limiting) without the two knowing about each other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nmi.api.schemas import (
    AlertView,
    AuditEntryView,
    BacktestDetail,
    BacktestRunView,
    BacktestTradeView,
    CompanyView,
    EquityPointView,
    ExplanationBulletView,
    ExplanationCitationView,
    ExplanationView,
    HorizonView,
    IndexRef,
    MarketOverview,
    MomentumView,
    Mover,
    PriceBar,
    RecommendationDetail,
    RecommendationEventView,
    RecommendationRow,
    RecommendationVersionView,
    RegimeView,
    ScoreBreakdown,
    ScreenerRow,
    SectorView,
    SignalView,
    StockDetail,
    TechnicalView,
    ThesisFactorView,
    ThesisView,
    ValuationView,
)
from nmi.core.config import settings
from nmi.core.models import (
    Company,
    DailyPrice,
    HorizonMetric,
    Index,
    IndexMembership,
    Industry,
    Instrument,
    MarketRegime,
    MomentumMetric,
    Notification,
    Recommendation,
    RecommendationEvent,
    RecommendationVersion,
    RelativeStrengthMetric,
    ScoringSnapshot,
    Sector,
    SectorMetric,
    Signal,
    Strategy,
    StrategyVersion,
    TechnicalIndicator,
    ThesisSnapshot,
    ValuationMetric,
)


def f(value) -> float | None:
    """Decimal|int|None -> float|None, the only numeric shape the API emits."""
    if value is None:
        return None
    if isinstance(value, Decimal | float | int):
        return float(value)
    return value


def _enum(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _max_as_of(session: Session, column, *conditions) -> date | None:
    stmt = select(func.max(column)).where(*conditions) if conditions else select(func.max(column))
    return session.scalar(stmt)


# --------------------------------------------------------------------- indices


def get_index(session: Session, code: str) -> Index | None:
    return session.scalar(select(Index).where(Index.code == code))


def index_ref(index: Index) -> IndexRef:
    return IndexRef(code=index.code, name=index.name)


def members_on(session: Session, index_id: int, as_of: date) -> list[int]:
    """Instrument ids in the index on ``as_of`` (``effective_to`` inclusive)."""
    stmt = select(IndexMembership.instrument_id).where(
        IndexMembership.index_id == index_id,
        IndexMembership.effective_from <= as_of,
        or_(
            IndexMembership.effective_to.is_(None),
            IndexMembership.effective_to >= as_of,
        ),
    )
    return list(session.scalars(stmt).all())


def indices_for_instrument(session: Session, instrument_id: int, as_of: date) -> list[IndexRef]:
    stmt = (
        select(Index.code, Index.name)
        .join(IndexMembership, IndexMembership.index_id == Index.id)
        .where(
            IndexMembership.instrument_id == instrument_id,
            IndexMembership.effective_from <= as_of,
            or_(
                IndexMembership.effective_to.is_(None),
                IndexMembership.effective_to >= as_of,
            ),
        )
        .order_by(Index.code)
    )
    return [IndexRef(code=code, name=name) for code, name in session.execute(stmt).all()]


# ----------------------------------------------------------------------- market


def market_overview(
    session: Session, index_code: str, as_of: date | None = None, movers: int = 5
) -> MarketOverview | None:
    index = get_index(session, index_code)
    if index is None:
        return None
    resolved = as_of or _max_as_of(session, MarketRegime.as_of, MarketRegime.index_id == index.id)
    if resolved is None:
        resolved = _max_as_of(session, ScoringSnapshot.as_of)
    if resolved is None:
        return MarketOverview(
            index=index_ref(index),
            as_of=date.today(),
            sectors=[],
            leaders=[],
            laggards=[],
            signal_counts={},
            member_count=0,
        )

    regime_row = session.scalar(
        select(MarketRegime).where(
            MarketRegime.index_id == index.id, MarketRegime.as_of == resolved
        )
    )
    regime = _regime_view(regime_row) if regime_row else None

    sectors = _sector_views(session, index.id, resolved)
    member_ids = members_on(session, index.id, resolved)
    leaders, laggards = _movers(session, member_ids, resolved, movers)
    signal_counts = _signal_counts(session, member_ids, resolved)
    return MarketOverview(
        index=index_ref(index),
        as_of=resolved,
        regime=regime,
        sectors=sectors,
        leaders=leaders,
        laggards=laggards,
        signal_counts=signal_counts,
        member_count=len(member_ids),
    )


def _regime_view(row: MarketRegime) -> RegimeView:
    return RegimeView(
        as_of=row.as_of,
        label=_enum(row.regime_label) or "UNKNOWN",
        score=f(row.regime_score),
        member_count=row.member_count,
        breadth_above_sma20_pct=f(row.breadth_above_sma20_pct),
        breadth_above_sma50_pct=f(row.breadth_above_sma50_pct),
        breadth_above_sma200_pct=f(row.breadth_above_sma200_pct),
        index_return_1m=f(row.index_return_1m),
        index_return_3m=f(row.index_return_3m),
        index_return_6m=f(row.index_return_6m),
        index_hist_vol_20=f(row.index_hist_vol_20),
        index_hist_vol_60=f(row.index_hist_vol_60),
        index_drawdown_pct=f(row.index_drawdown_pct),
        sector_participation_pct=f(row.sector_participation_pct),
    )


def _sector_views(session: Session, index_id: int, as_of: date) -> list[SectorView]:
    stmt = (
        select(SectorMetric, Sector.name)
        .join(Sector, Sector.id == SectorMetric.sector_id)
        .where(SectorMetric.index_id == index_id, SectorMetric.as_of == as_of)
        .order_by(SectorMetric.sector_score.desc())
    )
    views = []
    for row, sector_name in session.execute(stmt).all():
        views.append(
            SectorView(
                sector_id=row.sector_id,
                sector=sector_name,
                state=_enum(row.sector_state) or "NEUTRAL",
                score=f(row.sector_score),
                member_count=row.member_count,
                breadth_above_sma50_pct=f(row.breadth_above_sma50_pct),
                avg_return_1m=f(row.avg_return_1m),
                avg_return_3m=f(row.avg_return_3m),
                avg_rs_3m=f(row.avg_rs_3m),
                relative_to_index_pct=f(row.relative_to_index_pct),
            )
        )
    return views


def _movers(
    session: Session, member_ids: list[int], as_of: date, count: int
) -> tuple[list[Mover], list[Mover]]:
    if not member_ids or count <= 0:
        return [], []
    base = (
        select(
            Instrument.symbol,
            Company.name,
            MomentumMetric.return_1m,
            MomentumMetric.return_3m,
            ScoringSnapshot.composite_score,
            DailyPrice.adjusted_close,
            DailyPrice.close,
        )
        .join(Instrument, Instrument.id == MomentumMetric.instrument_id)
        .join(Company, Company.id == Instrument.company_id)
        .outerjoin(
            ScoringSnapshot,
            (ScoringSnapshot.instrument_id == Instrument.id) & (ScoringSnapshot.as_of == as_of),
        )
        .outerjoin(
            DailyPrice,
            (DailyPrice.instrument_id == Instrument.id) & (DailyPrice.trade_date == as_of),
        )
        .where(MomentumMetric.instrument_id.in_(member_ids), MomentumMetric.as_of == as_of)
    )

    def _rows(ascending: bool):
        stmt = (
            base.where(MomentumMetric.return_1m.is_not(None))
            .order_by(
                MomentumMetric.return_1m.asc() if ascending else MomentumMetric.return_1m.desc()
            )
            .limit(count)
        )
        out = []
        for symbol, name, r1, r3, score, adjusted, close in session.execute(stmt).all():
            out.append(
                Mover(
                    symbol=symbol,
                    name=name,
                    close=f(adjusted if adjusted is not None else close),
                    change_1m=f(r1),
                    change_3m=f(r3),
                    composite_score=f(score),
                )
            )
        return out

    return _rows(ascending=False), _rows(ascending=True)


def _signal_counts(session: Session, member_ids: list[int], as_of: date) -> dict[str, int]:
    if not member_ids:
        return {}
    stmt = (
        select(Signal.signal_type, func.count())
        .where(Signal.instrument_id.in_(member_ids), Signal.as_of == as_of)
        .group_by(Signal.signal_type)
    )
    counts: dict[str, int] = {}
    for signal_type, count in session.execute(stmt).all():
        key = _enum(signal_type) or "UNKNOWN"
        counts[key] = counts.get(key, 0) + int(count)
    return counts


# --------------------------------------------------------------------- screener


def _signal_views(
    session: Session, instrument_ids: list[int], as_of: date
) -> dict[int, list[SignalView]]:
    if not instrument_ids:
        return {}
    stmt = (
        select(Signal, Strategy.code)
        .join(StrategyVersion, StrategyVersion.id == Signal.strategy_version_id)
        .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
        .where(
            Signal.instrument_id.in_(instrument_ids),
            Signal.as_of == as_of,
            Signal.calc_version == settings.calc_version,
        )
        .order_by(Signal.instrument_id, Signal.id)
    )
    grouped: dict[int, list[SignalView]] = {}
    for signal, strategy_code in session.execute(stmt).all():
        grouped.setdefault(signal.instrument_id, []).append(
            SignalView(
                strategy_code=strategy_code,
                signal_type=_enum(signal.signal_type) or "UNKNOWN",
                state=_enum(signal.state) or "UNKNOWN",
                confidence=f(signal.confidence),
                price=f(signal.price),
                risk_level=_enum(signal.risk_level),
                entry_low=f(signal.entry_low),
                entry_high=f(signal.entry_high),
                target_low=f(signal.target_low),
                target_high=f(signal.target_high),
                invalidation_price=f(signal.invalidation_price),
                thesis=signal.thesis,
            )
        )
    return grouped


def _scores(row: ScoringSnapshot) -> ScoreBreakdown:
    return ScoreBreakdown(
        trend=f(row.trend_score),
        momentum=f(row.momentum_score),
        relative_strength=f(row.relative_strength_score),
        quality=f(row.quality_score),
        growth=f(row.growth_score),
        valuation=f(row.valuation_score),
        risk=f(row.risk_score),
        liquidity=f(row.liquidity_score),
        horizon=f(row.horizon_score),
        sector=f(row.sector_score),
    )


def _closes(session: Session, instrument_ids: list[int], as_of: date) -> dict[int, float]:
    if not instrument_ids:
        return {}
    stmt = select(DailyPrice.instrument_id, DailyPrice.adjusted_close, DailyPrice.close).where(
        DailyPrice.instrument_id.in_(instrument_ids),
        DailyPrice.trade_date == as_of,
    )
    return {
        iid: f(adjusted if adjusted is not None else close)
        for iid, adjusted, close in session.execute(stmt).all()
    }


def screen(
    session: Session,
    *,
    index_code: str | None,
    as_of: date | None,
    horizon: str | None,
    label: str | None,
    sector: str | None,
    query: str | None,
    min_score: float | None,
    max_score: float | None,
    sort: str,
    order: str,
    offset: int,
    limit: int,
) -> tuple[list[ScreenerRow], int, date | None]:
    resolved = as_of or _max_as_of(session, ScoringSnapshot.as_of)
    if resolved is None:
        return [], 0, None

    member_ids: list[int] | None = None
    if index_code:
        index = get_index(session, index_code)
        if index is None:
            return [], 0, resolved
        member_ids = members_on(session, index.id, resolved)

    conditions = [
        ScoringSnapshot.as_of == resolved,
        ScoringSnapshot.calc_version == settings.calc_version,
    ]
    if member_ids is not None:
        conditions.append(ScoringSnapshot.instrument_id.in_(member_ids))
    if horizon:
        conditions.append(ScoringSnapshot.preferred_horizon == horizon)
    if label:
        conditions.append(ScoringSnapshot.score_label == label)
    if min_score is not None:
        conditions.append(ScoringSnapshot.composite_score >= min_score)
    if max_score is not None:
        conditions.append(ScoringSnapshot.composite_score <= max_score)

    base = (
        select(ScoringSnapshot, Instrument, Company, Sector, Industry)
        .join(Instrument, Instrument.id == ScoringSnapshot.instrument_id)
        .join(Company, Company.id == Instrument.company_id)
        .outerjoin(Sector, Sector.id == Company.sector_id)
        .outerjoin(Industry, Industry.id == Company.industry_id)
        .where(*conditions)
    )
    if sector:
        base = base.where(or_(Sector.name == sector, Sector.code == sector))
    if query:
        like = f"%{query.lower()}%"
        base = base.where(
            or_(
                func.lower(Instrument.symbol).like(like),
                func.lower(Company.name).like(like),
            )
        )

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0

    sort_columns = {
        "composite": ScoringSnapshot.composite_score,
        "trend": ScoringSnapshot.trend_score,
        "momentum": ScoringSnapshot.momentum_score,
        "relative_strength": ScoringSnapshot.relative_strength_score,
        "quality": ScoringSnapshot.quality_score,
        "growth": ScoringSnapshot.growth_score,
        "valuation": ScoringSnapshot.valuation_score,
        "risk": ScoringSnapshot.risk_score,
        "liquidity": ScoringSnapshot.liquidity_score,
        "horizon": ScoringSnapshot.horizon_score,
        "sector": ScoringSnapshot.sector_score,
        "symbol": Instrument.symbol,
    }
    column = sort_columns.get(sort, ScoringSnapshot.composite_score)
    base = base.order_by(column.asc() if order == "asc" else column.desc())
    base = base.offset(offset).limit(limit)

    rows = session.execute(base).all()
    instrument_ids = [instrument.id for _snap, instrument, _co, _se, _in in rows]
    closes = _closes(session, instrument_ids, resolved)
    signals = _signal_views(session, instrument_ids, resolved)

    items = [
        _screener_row(
            snap, instrument, company, sector_row, industry_row, resolved, closes, signals
        )
        for snap, instrument, company, sector_row, industry_row in rows
    ]
    return items, int(total), resolved


def _screener_row(
    snap: ScoringSnapshot,
    instrument: Instrument,
    company: Company,
    sector: Sector | None,
    industry: Industry | None,
    as_of: date,
    closes: dict[int, float],
    signals: dict[int, list[SignalView]],
) -> ScreenerRow:
    return ScreenerRow(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        name=company.name,
        exchange=_enum(instrument.exchange) or "",
        sector=sector.name if sector else None,
        industry=industry.name if industry else None,
        as_of=as_of,
        close=closes.get(instrument.id),
        composite_score=f(snap.composite_score),
        score_label=_enum(snap.score_label) or "NEUTRAL",
        preferred_horizon=_enum(snap.preferred_horizon) or "",
        scores=_scores(snap),
        signals=signals.get(instrument.id, []),
    )


# ------------------------------------------------------------------------ stock


def get_instrument(session: Session, symbol: str) -> Instrument | None:
    return session.scalar(
        select(Instrument)
        .where(func.upper(Instrument.symbol) == symbol.upper())
        .order_by(Instrument.is_active.desc(), Instrument.id)
    )


def _price_bar(row: DailyPrice) -> PriceBar:
    return PriceBar(
        trade_date=row.trade_date,
        open=f(row.open),
        high=f(row.high),
        low=f(row.low),
        close=f(row.close) or 0.0,
        adjusted_close=f(row.adjusted_close),
        volume=row.volume,
    )


def latest_price(session: Session, instrument_id: int, as_of: date) -> PriceBar | None:
    row = session.scalar(
        select(DailyPrice)
        .where(DailyPrice.instrument_id == instrument_id, DailyPrice.trade_date <= as_of)
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    )
    return _price_bar(row) if row else None


def price_history(
    session: Session,
    instrument_id: int,
    start: date | None,
    end: date | None,
    offset: int,
    limit: int,
) -> tuple[list[PriceBar], int]:
    conditions = [DailyPrice.instrument_id == instrument_id]
    if start:
        conditions.append(DailyPrice.trade_date >= start)
    if end:
        conditions.append(DailyPrice.trade_date <= end)
    total = session.scalar(select(func.count()).select_from(DailyPrice).where(*conditions)) or 0
    stmt = (
        select(DailyPrice)
        .where(*conditions)
        .order_by(DailyPrice.trade_date.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_price_bar(row) for row in session.scalars(stmt).all()], int(total)


def stock_detail(session: Session, symbol: str, as_of: date | None = None) -> StockDetail | None:
    instrument = get_instrument(session, symbol)
    if instrument is None:
        return None
    company = session.get(Company, instrument.company_id)
    if company is None:  # pragma: no cover - FK guarantees this
        return None

    resolved = (
        as_of
        or _max_as_of(
            session, TechnicalIndicator.as_of, TechnicalIndicator.instrument_id == instrument.id
        )
        or _max_as_of(session, DailyPrice.trade_date, DailyPrice.instrument_id == instrument.id)
    )
    if resolved is None:
        resolved = date.today()

    sector = session.get(Sector, company.sector_id) if company.sector_id else None
    industry = session.get(Industry, company.industry_id) if company.industry_id else None

    technical = session.scalar(
        select(TechnicalIndicator).where(
            TechnicalIndicator.instrument_id == instrument.id,
            TechnicalIndicator.as_of == resolved,
            TechnicalIndicator.calc_version == settings.calc_version,
        )
    )
    momentum_row = session.scalar(
        select(MomentumMetric).where(
            MomentumMetric.instrument_id == instrument.id,
            MomentumMetric.as_of == resolved,
            MomentumMetric.calc_version == settings.calc_version,
        )
    )
    benchmark = settings.rs_benchmarks[0] if settings.rs_benchmarks else None
    rs_row = None
    if benchmark:
        rs_row = session.scalar(
            select(RelativeStrengthMetric).where(
                RelativeStrengthMetric.instrument_id == instrument.id,
                RelativeStrengthMetric.as_of == resolved,
                RelativeStrengthMetric.benchmark == benchmark,
                RelativeStrengthMetric.calc_version == settings.calc_version,
            )
        )
    valuation_row = session.scalar(
        select(ValuationMetric).where(
            ValuationMetric.instrument_id == instrument.id,
            ValuationMetric.as_of == resolved,
            ValuationMetric.calc_version == settings.calc_version,
        )
    )
    horizon_row = session.scalar(
        select(HorizonMetric).where(
            HorizonMetric.instrument_id == instrument.id,
            HorizonMetric.as_of == resolved,
            HorizonMetric.calc_version == settings.calc_version,
        )
    )
    snapshot = session.scalar(
        select(ScoringSnapshot).where(
            ScoringSnapshot.instrument_id == instrument.id,
            ScoringSnapshot.as_of == resolved,
            ScoringSnapshot.calc_version == settings.calc_version,
        )
    )

    close = None
    if technical is None:
        bar = latest_price(session, instrument.id, resolved)
        close = bar.close if bar else None
    scoring_row = None
    if snapshot is not None:
        signals = _signal_views(session, [instrument.id], resolved)
        scoring_row = _screener_row(
            snapshot,
            instrument,
            company,
            sector,
            industry,
            resolved,
            {instrument.id: close} if close else {},
            signals,
        )

    return StockDetail(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        exchange=_enum(instrument.exchange) or "",
        instrument_type=_enum(instrument.instrument_type) or "",
        is_active=instrument.is_active,
        company=CompanyView(
            isin=company.isin,
            name=company.name,
            currency=company.currency,
            sector=sector.name if sector else None,
            industry=industry.name if industry else None,
            listing_date=company.listing_date,
        ),
        indices=indices_for_instrument(session, instrument.id, resolved),
        as_of=resolved,
        latest_price=latest_price(session, instrument.id, resolved),
        technical=_technical_view(technical, close) if technical else None,
        momentum=_momentum_view(momentum_row, rs_row, benchmark) if momentum_row else None,
        valuation=_valuation_view(valuation_row) if valuation_row else None,
        horizon=_horizon_view(horizon_row) if horizon_row else None,
        scoring=scoring_row,
    )


def _technical_view(row: TechnicalIndicator, close: float | None) -> TechnicalView:
    return TechnicalView(
        as_of=row.as_of,
        close=close,
        sma20=f(row.sma20),
        sma50=f(row.sma50),
        sma200=f(row.sma200),
        rsi14=f(row.rsi14),
        macd=f(row.macd),
        macd_signal=f(row.macd_signal),
        macd_hist=f(row.macd_hist),
        adx14=f(row.adx14),
        atr14=f(row.atr14),
        hist_vol_20=f(row.hist_vol_20),
        high_52w=f(row.high_52w),
        low_52w=f(row.low_52w),
        drawdown_pct=f(row.drawdown_pct),
        dist_from_high_52w_pct=f(row.dist_from_high_52w_pct),
        trend_state=row.trend_state,
        breakout_52w=bool(row.breakout_52w),
        volume_ratio=f(row.volume_ratio),
    )


def _momentum_view(row: MomentumMetric, rs, benchmark: str | None) -> MomentumView:
    return MomentumView(
        return_1m=f(row.return_1m),
        return_3m=f(row.return_3m),
        return_6m=f(row.return_6m),
        return_12m=f(row.return_12m),
        rs_1m=f(rs.rs_1m) if rs else None,
        rs_3m=f(rs.rs_3m) if rs else None,
        rs_6m=f(rs.rs_6m) if rs else None,
        rs_12m=f(rs.rs_12m) if rs else None,
        rs_trend=_enum(rs.rs_trend) if rs else None,
        benchmark=benchmark,
    )


def _valuation_view(row: ValuationMetric) -> ValuationView:
    return ValuationView(
        pe=f(row.pe),
        pb=f(row.pb),
        ev_ebitda=f(row.ev_ebitda),
        dividend_yield=f(row.dividend_yield),
        fcf_yield=f(row.fcf_yield),
        pe_percentile_3y=f(row.pe_percentile_3y),
        pb_percentile_3y=f(row.pb_percentile_3y),
        ev_ebitda_percentile_3y=f(row.ev_ebitda_percentile_3y),
        valuation_label=_enum(row.valuation_label),
    )


def _horizon_view(row: HorizonMetric) -> HorizonView:
    return HorizonView(
        short_term_score=f(row.short_term_score),
        medium_term_score=f(row.medium_term_score),
        long_term_score=f(row.long_term_score),
        preferred_horizon=_enum(row.preferred_horizon),
        horizon_confidence=f(row.horizon_confidence),
    )


# ------------------------------------------------------------- recommendations


def _recommendation_row(
    rec: Recommendation, instrument: Instrument, company: Company
) -> RecommendationRow:
    return RecommendationRow(
        id=rec.id,
        instrument_id=rec.instrument_id,
        symbol=instrument.symbol,
        name=company.name,
        strategy_code=rec.strategy_code,
        state=_enum(rec.state) or "UNKNOWN",
        horizon=_enum(rec.horizon) or "",
        risk_level=_enum(rec.risk_level) or "",
        confidence=f(rec.confidence),
        current_price=f(rec.current_price),
        entry_low=f(rec.entry_low),
        entry_high=f(rec.entry_high),
        target_low=f(rec.target_low),
        target_high=f(rec.target_high),
        invalidation_price=f(rec.invalidation_price),
        thesis=rec.thesis,
        exit_reason=rec.exit_reason,
        latest_version=rec.latest_version,
        first_as_of=rec.first_as_of,
        last_as_of=rec.last_as_of,
        active_since=rec.active_since,
    )


def list_recommendations(
    session: Session,
    *,
    index_code: str | None,
    state: str | None,
    strategy_code: str | None,
    horizon: str | None,
    as_of: date | None,
    offset: int,
    limit: int,
) -> tuple[list[RecommendationRow], int]:
    conditions = []
    if index_code:
        index = get_index(session, index_code)
        if index is None:
            return [], 0
        member_ids = members_on(
            session, index.id, as_of or _max_as_of(session, ScoringSnapshot.as_of) or date.today()
        )
        conditions.append(Recommendation.instrument_id.in_(member_ids))
    if state:
        conditions.append(Recommendation.state == state)
    if strategy_code:
        conditions.append(Recommendation.strategy_code == strategy_code)
    if horizon:
        conditions.append(Recommendation.horizon == horizon)
    if as_of:
        conditions.append(Recommendation.first_as_of <= as_of)
        conditions.append(Recommendation.last_as_of >= as_of)

    total = session.scalar(select(func.count()).select_from(Recommendation).where(*conditions)) or 0
    stmt = (
        select(Recommendation, Instrument, Company)
        .join(Instrument, Instrument.id == Recommendation.instrument_id)
        .join(Company, Company.id == Instrument.company_id)
        .where(*conditions)
        .order_by(Recommendation.last_as_of.desc(), Recommendation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [
        _recommendation_row(rec, instrument, company)
        for rec, instrument, company in session.execute(stmt).all()
    ]
    return items, int(total)


def recommendation_explanation(
    session: Session, recommendation_id: int, kind: str | None = None
) -> ExplanationView | None:
    """The latest stored AI explanation for a recommendation, if any."""
    from nmi.explain import service as explain_service

    explanation = explain_service.get_latest_explanation(session, recommendation_id, kind)
    if explanation is None:
        return None
    return ExplanationView(
        recommendation_id=explanation.recommendation_id,
        kind=explanation.kind,
        as_of=explanation.as_of,
        headline=explanation.headline,
        summary=explanation.summary,
        bullets=[
            ExplanationBulletView(text=bullet.text, citations=bullet.citations)
            for bullet in explanation.bullets
        ],
        risks=[
            ExplanationBulletView(text=risk.text, citations=risk.citations)
            for risk in explanation.risks
        ],
        citations=[
            ExplanationCitationView(
                key=citation.key,
                label=citation.label,
                value=citation.value,
                group=citation.group,
                source=citation.source,
            )
            for citation in explanation.citations
        ],
        provider=explanation.provider,
        model=explanation.model,
        prompt_version=explanation.prompt_version,
        source_hash=explanation.source_hash,
        generated_at=explanation.generated_at,
        fallback_reason=explanation.fallback_reason,
        valid=explanation.valid,
        validation_errors=explanation.validation_errors,
    )


def recommendation_detail(session: Session, recommendation_id: int) -> RecommendationDetail | None:
    rec = session.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    instrument = session.get(Instrument, rec.instrument_id)
    company = session.get(Company, instrument.company_id) if instrument else None
    if instrument is None or company is None:  # pragma: no cover
        return None
    row = _recommendation_row(rec, instrument, company)

    versions = _recommendation_versions(session, rec.id)
    events = _recommendation_events(session, rec.id)
    thesis = _thesis_views(session, rec.id)
    return RecommendationDetail(
        **row.model_dump(),
        created_reason=rec.created_reason,
        versions=versions,
        events=events,
        thesis=thesis,
    )


def _recommendation_versions(
    session: Session, recommendation_id: int
) -> list[RecommendationVersionView]:
    stmt = (
        select(RecommendationVersion)
        .where(RecommendationVersion.recommendation_id == recommendation_id)
        .order_by(RecommendationVersion.version)
    )
    return [
        RecommendationVersionView(
            version=row.version,
            as_of=row.as_of,
            state=_enum(row.state) or "",
            signal_type=_enum(row.signal_type) or "",
            price=f(row.price),
            confidence=f(row.confidence),
            composite_score=f(row.composite_score),
            change_summary=row.change_summary,
            reasons=list(row.reasons or []),
        )
        for row in session.scalars(stmt).all()
    ]


def _recommendation_events(
    session: Session, recommendation_id: int
) -> list[RecommendationEventView]:
    stmt = (
        select(RecommendationEvent)
        .where(RecommendationEvent.recommendation_id == recommendation_id)
        .order_by(RecommendationEvent.as_of, RecommendationEvent.id)
    )
    return [
        RecommendationEventView(
            as_of=row.as_of,
            event_type=_enum(row.event_type) or "",
            mechanism=_enum(row.mechanism),
            previous_state=_enum(row.previous_state),
            new_state=_enum(row.new_state),
            title=row.title,
            message=row.message,
            price=f(row.price),
            recommendation_version=row.recommendation_version,
        )
        for row in session.scalars(stmt).all()
    ]


def _thesis_views(session: Session, recommendation_id: int) -> list[ThesisView]:
    stmt = (
        select(ThesisSnapshot)
        .where(ThesisSnapshot.recommendation_id == recommendation_id)
        .order_by(ThesisSnapshot.as_of)
    )
    out = []
    for row in session.scalars(stmt).all():
        factors = []
        for key, item in (row.factors or {}).items():
            if not isinstance(item, dict):
                continue
            factors.append(
                ThesisFactorView(
                    key=key,
                    label=item.get("label", key),
                    group=item.get("group", "score"),
                    direction=item.get("direction", "UNCHANGED"),
                    before=f(item.get("before")),
                    after=f(item.get("after")),
                    before_text=item.get("before_text"),
                    after_text=item.get("after_text"),
                )
            )
        out.append(
            ThesisView(
                as_of=row.as_of,
                state=_enum(row.state) or "",
                price=f(row.price),
                composite_score=f(row.composite_score),
                confidence=f(row.confidence),
                weakened_count=row.weakened_count,
                improved_count=row.improved_count,
                summary=row.summary,
                factors=factors,
            )
        )
    return out


# --------------------------------------------------------------------- alerts


def list_alerts(
    session: Session,
    *,
    notification_type: str | None,
    severity: str | None,
    since: date | None,
    index_code: str | None,
    symbol: str | None,
    offset: int,
    limit: int,
) -> tuple[list[AlertView], int]:
    conditions = []
    if notification_type:
        conditions.append(Notification.notification_type == notification_type)
    if severity:
        conditions.append(Notification.severity == severity)
    if since:
        conditions.append(Notification.as_of >= since)
    if index_code:
        index = get_index(session, index_code)
        if index is None:
            return [], 0
        conditions.append(Notification.index_id == index.id)
    if symbol:
        instrument = get_instrument(session, symbol)
        if instrument is None:
            return [], 0
        conditions.append(Notification.instrument_id == instrument.id)

    total = session.scalar(select(func.count()).select_from(Notification).where(*conditions)) or 0
    stmt = (
        select(Notification, Instrument.symbol)
        .outerjoin(Instrument, Instrument.id == Notification.instrument_id)
        .where(*conditions)
        .order_by(Notification.as_of.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [
        AlertView(
            id=notif.id,
            notification_type=_enum(notif.notification_type) or "",
            severity=_enum(notif.severity) or "",
            as_of=notif.as_of,
            title=notif.title,
            message=notif.message,
            reason=notif.reason,
            symbol=symbol_value,
            strategy_code=notif.strategy_code,
            recommendation_id=notif.recommendation_id,
            payload=dict(notif.payload or {}),
        )
        for notif, symbol_value in session.execute(stmt).all()
    ]
    return items, int(total)


# ------------------------------------------------------------------ backtests


def list_backtest_runs_db(
    session: Session, offset: int, limit: int
) -> tuple[list[BacktestRunView], int]:
    from nmi.core.models import BacktestRun

    total = session.scalar(select(func.count()).select_from(BacktestRun)) or 0
    stmt = select(BacktestRun).order_by(BacktestRun.id.desc()).offset(offset).limit(limit)
    return [_backtest_run_view(row) for row in session.scalars(stmt).all()], int(total)


def _backtest_run_view(row) -> BacktestRunView:
    metrics = row.metrics or {}
    return BacktestRunView(
        id=row.id,
        name=row.name,
        kind=_enum(row.kind) or "",
        parent_run_id=row.parent_run_id,
        window_index=row.window_index,
        index_codes=list(row.index_codes or []),
        strategy_codes=list(row.strategy_codes or []),
        start_date=row.start_date,
        end_date=row.end_date,
        trades_count=row.trades_count,
        initial_capital=f(row.initial_capital),
        total_return=metrics.get("total_return"),
        cagr=metrics.get("cagr"),
        sharpe=metrics.get("sharpe"),
        max_drawdown=metrics.get("max_drawdown"),
        win_rate=metrics.get("win_rate"),
        excess_return=metrics.get("excess_return"),
        status=_enum(row.status) or "",
    )


def backtest_detail(session: Session, run_id: int) -> BacktestDetail | None:
    from nmi.core.models import BacktestRun

    row = session.get(BacktestRun, run_id)
    if row is None:
        return None
    view = _backtest_run_view(row)
    return BacktestDetail(
        **view.model_dump(),
        metrics=dict(row.metrics or {}),
        config=dict(row.config or {}),
    )


def backtest_trades(
    session: Session, run_id: int, offset: int, limit: int
) -> tuple[list[BacktestTradeView], int]:
    from nmi.core.models import BacktestTrade

    total = (
        session.scalar(
            select(func.count()).select_from(BacktestTrade).where(BacktestTrade.run_id == run_id)
        )
        or 0
    )
    stmt = (
        select(BacktestTrade)
        .where(BacktestTrade.run_id == run_id)
        .order_by(BacktestTrade.entry_date, BacktestTrade.id)
        .offset(offset)
        .limit(limit)
    )
    items = [
        BacktestTradeView(
            id=row.id,
            symbol=row.symbol,
            strategy_code=row.strategy_code,
            entry_signal_date=row.entry_signal_date,
            entry_date=row.entry_date,
            entry_price=f(row.entry_price) or 0.0,
            exit_date=row.exit_date,
            exit_price=f(row.exit_price) or 0.0,
            quantity=row.quantity,
            net_pnl=f(row.net_pnl) or 0.0,
            return_pct=f(row.return_pct) or 0.0,
            holding_days=row.holding_days,
            exit_reason=_enum(row.exit_reason) or "",
        )
        for row in session.scalars(stmt).all()
    ]
    return items, int(total)


def backtest_equity(session: Session, run_id: int) -> list[EquityPointView]:
    from nmi.core.models import BacktestEquityPoint

    stmt = (
        select(BacktestEquityPoint)
        .where(BacktestEquityPoint.run_id == run_id)
        .order_by(BacktestEquityPoint.as_of)
    )
    return [
        EquityPointView(
            as_of=row.as_of,
            equity=f(row.equity) or 0.0,
            cash=f(row.cash) or 0.0,
            positions=row.positions,
            exposure_pct=f(row.exposure_pct) or 0.0,
            drawdown_pct=f(row.drawdown_pct) or 0.0,
        )
        for row in session.scalars(stmt).all()
    ]


# ----------------------------------------------------------------------- audit


def list_audit(
    session: Session,
    *,
    principal: str | None,
    path: str | None,
    status_code: int | None,
    since: date | None,
    offset: int,
    limit: int,
) -> tuple[list[AuditEntryView], int]:
    from nmi.core.models import ApiRequest

    conditions = []
    if principal:
        conditions.append(ApiRequest.principal == principal)
    if path:
        conditions.append(ApiRequest.path.like(f"%{path}%"))
    if status_code is not None:
        conditions.append(ApiRequest.status_code == status_code)
    if since:
        conditions.append(func.date(ApiRequest.requested_at) >= since)

    total = session.scalar(select(func.count()).select_from(ApiRequest).where(*conditions)) or 0
    stmt = (
        select(ApiRequest)
        .where(*conditions)
        .order_by(ApiRequest.requested_at.desc(), ApiRequest.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = [
        AuditEntryView(
            id=row.id,
            requested_at=row.requested_at,
            principal=row.principal,
            method=row.method,
            path=row.path,
            query=row.query,
            status_code=row.status_code,
            duration_ms=f(row.duration_ms) or 0.0,
            cache_hit=row.cache_hit,
            client_ip=row.client_ip,
        )
        for row in session.scalars(stmt).all()
    ]
    return items, int(total)


def prune_audit(session: Session, older_than_days: int) -> int:
    """Delete audit rows older than the retention window; return the count."""
    from datetime import timedelta

    from nmi.core.models import ApiRequest

    cutoff = date.today() - timedelta(days=max(older_than_days, 1))
    rows = session.scalars(
        select(ApiRequest).where(func.date(ApiRequest.requested_at) < cutoff)
    ).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)
