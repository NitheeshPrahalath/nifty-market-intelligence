"""Typed response models for the API (Pydantic v2).

These are the contract the frontend and any external consumer code against, so
they are explicit rather than ``dict``: a field that disappears is a 500 in a
consumer, and a schema in the OpenAPI document is a compile-time error there
instead. Decimals leave the database as floats — these are read-only analytics,
and a JSON client cannot use a Decimal anyway.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    database: str


class IndexRef(BaseModel):
    code: str
    name: str


class RegimeView(BaseModel):
    as_of: date
    label: str
    score: float | None = None
    member_count: int = 0
    breadth_above_sma20_pct: float | None = None
    breadth_above_sma50_pct: float | None = None
    breadth_above_sma200_pct: float | None = None
    index_return_1m: float | None = None
    index_return_3m: float | None = None
    index_return_6m: float | None = None
    index_hist_vol_20: float | None = None
    index_hist_vol_60: float | None = None
    index_drawdown_pct: float | None = None
    sector_participation_pct: float | None = None


class SectorView(BaseModel):
    sector_id: int
    sector: str
    state: str
    score: float | None = None
    member_count: int = 0
    breadth_above_sma50_pct: float | None = None
    avg_return_1m: float | None = None
    avg_return_3m: float | None = None
    avg_rs_3m: float | None = None
    relative_to_index_pct: float | None = None


class Mover(BaseModel):
    symbol: str
    name: str
    close: float | None = None
    change_1m: float | None = None
    change_3m: float | None = None
    composite_score: float | None = None
    signal: str | None = None


class MarketOverview(BaseModel):
    index: IndexRef
    as_of: date
    regime: RegimeView | None = None
    sectors: list[SectorView]
    leaders: list[Mover]
    laggards: list[Mover]
    signal_counts: dict[str, int]
    member_count: int


class ScoreBreakdown(BaseModel):
    trend: float | None = None
    momentum: float | None = None
    relative_strength: float | None = None
    quality: float | None = None
    growth: float | None = None
    valuation: float | None = None
    risk: float | None = None
    liquidity: float | None = None
    horizon: float | None = None
    sector: float | None = None


class SignalView(BaseModel):
    strategy_code: str
    signal_type: str
    state: str
    confidence: float | None = None
    price: float | None = None
    risk_level: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    invalidation_price: float | None = None
    thesis: str | None = None


class ScreenerRow(BaseModel):
    instrument_id: int
    symbol: str
    name: str
    exchange: str
    sector: str | None = None
    industry: str | None = None
    as_of: date
    close: float | None = None
    composite_score: float | None = None
    score_label: str
    preferred_horizon: str
    scores: ScoreBreakdown
    signals: list[SignalView] = Field(default_factory=list)


class CompanyView(BaseModel):
    isin: str
    name: str
    currency: str
    sector: str | None = None
    industry: str | None = None
    listing_date: date | None = None


class PriceBar(BaseModel):
    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    adjusted_close: float | None = None
    volume: int | None = None


class TechnicalView(BaseModel):
    as_of: date
    close: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    adx14: float | None = None
    atr14: float | None = None
    hist_vol_20: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    drawdown_pct: float | None = None
    dist_from_high_52w_pct: float | None = None
    trend_state: str | None = None
    breakout_52w: bool = False
    volume_ratio: float | None = None


class MomentumView(BaseModel):
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None
    return_12m: float | None = None
    rs_1m: float | None = None
    rs_3m: float | None = None
    rs_6m: float | None = None
    rs_12m: float | None = None
    rs_trend: str | None = None
    benchmark: str | None = None


class ValuationView(BaseModel):
    pe: float | None = None
    pb: float | None = None
    ev_ebitda: float | None = None
    dividend_yield: float | None = None
    fcf_yield: float | None = None
    pe_percentile_3y: float | None = None
    pb_percentile_3y: float | None = None
    ev_ebitda_percentile_3y: float | None = None
    valuation_label: str | None = None


class HorizonView(BaseModel):
    short_term_score: float | None = None
    medium_term_score: float | None = None
    long_term_score: float | None = None
    preferred_horizon: str | None = None
    horizon_confidence: float | None = None


class StockDetail(BaseModel):
    instrument_id: int
    symbol: str
    exchange: str
    instrument_type: str
    is_active: bool
    company: CompanyView
    indices: list[IndexRef]
    as_of: date
    latest_price: PriceBar | None = None
    technical: TechnicalView | None = None
    momentum: MomentumView | None = None
    valuation: ValuationView | None = None
    horizon: HorizonView | None = None
    scoring: ScreenerRow | None = None


class RecommendationRow(BaseModel):
    id: int
    instrument_id: int
    symbol: str
    name: str
    strategy_code: str
    state: str
    horizon: str
    risk_level: str
    confidence: float | None = None
    current_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    invalidation_price: float | None = None
    thesis: str
    exit_reason: str | None = None
    latest_version: int
    first_as_of: date
    last_as_of: date
    active_since: date | None = None


class RecommendationVersionView(BaseModel):
    version: int
    as_of: date
    state: str
    signal_type: str
    price: float | None = None
    confidence: float | None = None
    composite_score: float | None = None
    change_summary: str | None = None
    reasons: list[str] = Field(default_factory=list)


class RecommendationEventView(BaseModel):
    as_of: date
    event_type: str
    mechanism: str | None = None
    previous_state: str | None = None
    new_state: str | None = None
    title: str
    message: str
    price: float | None = None
    recommendation_version: int | None = None


class ThesisFactorView(BaseModel):
    key: str
    label: str
    group: str
    direction: str
    before: float | None = None
    after: float | None = None
    before_text: str | None = None
    after_text: str | None = None


class ThesisView(BaseModel):
    as_of: date
    state: str
    price: float | None = None
    composite_score: float | None = None
    confidence: float | None = None
    weakened_count: int = 0
    improved_count: int = 0
    summary: str | None = None
    factors: list[ThesisFactorView] = Field(default_factory=list)


class RecommendationDetail(RecommendationRow):
    created_reason: str
    versions: list[RecommendationVersionView]
    events: list[RecommendationEventView]
    thesis: list[ThesisView]


class AlertView(BaseModel):
    id: int
    notification_type: str
    severity: str
    as_of: date
    title: str
    message: str
    reason: str
    symbol: str | None = None
    strategy_code: str | None = None
    recommendation_id: int | None = None
    payload: dict = Field(default_factory=dict)


class BacktestRunView(BaseModel):
    id: int
    name: str
    kind: str
    parent_run_id: int | None = None
    window_index: int | None = None
    index_codes: list[str]
    strategy_codes: list[str]
    start_date: date
    end_date: date
    trades_count: int
    initial_capital: float | None = None
    total_return: float | None = None
    cagr: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    excess_return: float | None = None
    status: str


class BacktestTradeView(BaseModel):
    id: int
    symbol: str
    strategy_code: str
    entry_signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    quantity: int
    net_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str


class EquityPointView(BaseModel):
    as_of: date
    equity: float
    cash: float
    positions: int
    exposure_pct: float
    drawdown_pct: float


class BacktestDetail(BacktestRunView):
    metrics: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


class AuditEntryView(BaseModel):
    id: int
    requested_at: datetime
    principal: str
    method: str
    path: str
    query: str | None = None
    status_code: int
    duration_ms: float
    cache_hit: bool
    client_ip: str | None = None
