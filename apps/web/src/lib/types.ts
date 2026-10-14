/**
 * Domain types mirroring the Phase 7 Pydantic schemas.
 *
 * They are hand-written rather than generated so the dashboard fails to compile
 * the moment a field it depends on changes shape — the API contract is enforced
 * at build time, not discovered at runtime. Optional fields are `| null` because
 * the API deliberately emits `null` (never a fabricated zero) for missing inputs.
 */

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  database: string;
}

export interface IndexRef {
  code: string;
  name: string;
}

export interface RegimeView {
  as_of: string;
  label: string;
  score: number | null;
  member_count: number;
  breadth_above_sma20_pct: number | null;
  breadth_above_sma50_pct: number | null;
  breadth_above_sma200_pct: number | null;
  index_return_1m: number | null;
  index_return_3m: number | null;
  index_return_6m: number | null;
  index_hist_vol_20: number | null;
  index_hist_vol_60: number | null;
  index_drawdown_pct: number | null;
  sector_participation_pct: number | null;
}

export interface SectorView {
  sector_id: number;
  sector: string;
  state: string;
  score: number | null;
  member_count: number;
  breadth_above_sma50_pct: number | null;
  avg_return_1m: number | null;
  avg_return_3m: number | null;
  avg_rs_3m: number | null;
  relative_to_index_pct: number | null;
}

export interface Mover {
  symbol: string;
  name: string;
  close: number | null;
  change_1m: number | null;
  change_3m: number | null;
  composite_score: number | null;
  signal: string | null;
}

export interface MarketOverview {
  index: IndexRef;
  as_of: string;
  regime: RegimeView | null;
  sectors: SectorView[];
  leaders: Mover[];
  laggards: Mover[];
  signal_counts: Record<string, number>;
  member_count: number;
}

export interface ScoreBreakdown {
  trend: number | null;
  momentum: number | null;
  relative_strength: number | null;
  quality: number | null;
  growth: number | null;
  valuation: number | null;
  risk: number | null;
  liquidity: number | null;
  horizon: number | null;
  sector: number | null;
}

export interface SignalView {
  strategy_code: string;
  signal_type: string;
  state: string;
  confidence: number | null;
  price: number | null;
  risk_level: string | null;
  entry_low: number | null;
  entry_high: number | null;
  target_low: number | null;
  target_high: number | null;
  invalidation_price: number | null;
  thesis: string | null;
}

export interface ScreenerRow {
  instrument_id: number;
  symbol: string;
  name: string;
  exchange: string;
  sector: string | null;
  industry: string | null;
  as_of: string;
  close: number | null;
  composite_score: number | null;
  score_label: string;
  preferred_horizon: string;
  scores: ScoreBreakdown;
  signals: SignalView[];
}

export interface CompanyView {
  isin: string;
  name: string;
  currency: string;
  sector: string | null;
  industry: string | null;
  listing_date: string | null;
}

export interface PriceBar {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  adjusted_close: number | null;
  volume: number | null;
}

export interface TechnicalView {
  as_of: string;
  close: number | null;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  rsi14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  adx14: number | null;
  atr14: number | null;
  hist_vol_20: number | null;
  high_52w: number | null;
  low_52w: number | null;
  drawdown_pct: number | null;
  dist_from_high_52w_pct: number | null;
  trend_state: string | null;
  breakout_52w: boolean;
  volume_ratio: number | null;
}

export interface MomentumView {
  return_1m: number | null;
  return_3m: number | null;
  return_6m: number | null;
  return_12m: number | null;
  rs_1m: number | null;
  rs_3m: number | null;
  rs_6m: number | null;
  rs_12m: number | null;
  rs_trend: string | null;
  benchmark: string | null;
}

export interface ValuationView {
  pe: number | null;
  pb: number | null;
  ev_ebitda: number | null;
  dividend_yield: number | null;
  fcf_yield: number | null;
  pe_percentile_3y: number | null;
  pb_percentile_3y: number | null;
  ev_ebitda_percentile_3y: number | null;
  valuation_label: string | null;
}

export interface HorizonView {
  short_term_score: number | null;
  medium_term_score: number | null;
  long_term_score: number | null;
  preferred_horizon: string | null;
  horizon_confidence: number | null;
}

export interface StockDetail {
  instrument_id: number;
  symbol: string;
  exchange: string;
  instrument_type: string;
  is_active: boolean;
  company: CompanyView;
  indices: IndexRef[];
  as_of: string;
  latest_price: PriceBar | null;
  technical: TechnicalView | null;
  momentum: MomentumView | null;
  valuation: ValuationView | null;
  horizon: HorizonView | null;
  scoring: ScreenerRow | null;
}

export interface RecommendationRow {
  id: number;
  instrument_id: number;
  symbol: string;
  name: string;
  strategy_code: string;
  state: string;
  horizon: string;
  risk_level: string;
  confidence: number | null;
  current_price: number | null;
  entry_low: number | null;
  entry_high: number | null;
  target_low: number | null;
  target_high: number | null;
  invalidation_price: number | null;
  thesis: string;
  exit_reason: string | null;
  latest_version: number;
  first_as_of: string;
  last_as_of: string;
  active_since: string | null;
}

export interface RecommendationVersionView {
  version: number;
  as_of: string;
  state: string;
  signal_type: string;
  price: number | null;
  confidence: number | null;
  composite_score: number | null;
  change_summary: string | null;
  reasons: string[];
}

export interface RecommendationEventView {
  as_of: string;
  event_type: string;
  mechanism: string | null;
  previous_state: string | null;
  new_state: string | null;
  title: string;
  message: string;
  price: number | null;
  recommendation_version: number | null;
}

export interface ThesisFactorView {
  key: string;
  label: string;
  group: string;
  direction: string;
  before: number | null;
  after: number | null;
  before_text: string | null;
  after_text: string | null;
}

export interface ThesisView {
  as_of: string;
  state: string;
  price: number | null;
  composite_score: number | null;
  confidence: number | null;
  weakened_count: number;
  improved_count: number;
  summary: string | null;
  factors: ThesisFactorView[];
}

export interface RecommendationDetail extends Omit<RecommendationRow, "thesis"> {
  created_reason: string;
  versions: RecommendationVersionView[];
  events: RecommendationEventView[];
  thesis: ThesisView[];
}

export interface AlertView {
  id: number;
  notification_type: string;
  severity: string;
  as_of: string;
  title: string;
  message: string;
  reason: string;
  symbol: string | null;
  strategy_code: string | null;
  recommendation_id: number | null;
  payload: Record<string, unknown>;
}
