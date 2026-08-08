export type Direction = 'AUTO' | 'LONG' | 'SHORT';
export type Stage = 'TRADE_READY' | 'WATCH' | 'ON_RADAR' | 'SKIP';
export type IVAssessment = 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN';
export type SetupMaturity = 'EARLY' | 'DEVELOPING' | 'READY';

export interface ScoringBreakdown {
  price_structure: number;
  momentum_volume: number;
  index_fo_context: number;
  stock_fo: number;
  market_context: number;
}

export interface AnalysisResult {
  stage: Stage;
  direction: 'LONG' | 'SHORT';
  conviction_score: number;
  setup_type: string;
  setup_maturity: SetupMaturity;
  entry_zone_low: number;
  entry_zone_high: number;
  underlying_stop: number;
  underlying_target_1: number;
  underlying_target_2: number;
  option_type: 'CE' | 'PE';
  strike: number;
  expiry_date: string;
  entry_premium_low: number;
  entry_premium_high: number;
  stop_loss_premium: number;
  target_1_premium: number;
  target_2_premium: number;
  lots: number;
  lot_size: number;
  max_risk_inr: number;
  risk_reward: number;
  iv_assessment: IVAssessment;
  scoring_breakdown: ScoringBreakdown;
  signals_contributing: string[];
  claude_full_rationale: string;
  mentor_explanation: string;
  why_could_be_wrong: string;
  skip_reason: string | null;
  rr_reasoning: string | null;
}

// ── Dashboard API types (from trade_setups DB records) ─────────────────────

export interface TradeSetup {
  id: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  stage: Stage;
  setup_type: string | null;
  setup_maturity: SetupMaturity | null;
  conviction_score: number;
  strike: number | null;
  option_type: 'CE' | 'PE' | null;
  expiry_date: string | null;
  entry_zone_low: number | null;   // stores option premium low
  entry_zone_high: number | null;  // stores option premium high
  stop_loss_premium: number | null;
  target_1_premium: number | null;
  target_2_premium: number | null;
  lots: number | null;
  lot_size: number | null;
  max_risk_inr: number | null;
  risk_reward: number | null;
  iv_at_flag: number | null;
  iv_assessment: IVAssessment | null;
  scoring_breakdown: ScoringBreakdown | null;
  claude_full_rationale: string | null;
  mentor_explanation: string | null;
  why_could_be_wrong: string | null;
  market_regime: string | null;
  setup_date: string;
  // paper trade fields
  entry_triggered: boolean;
  paper_outcome: string | null;
  paper_pnl_inr: number | null;
  rr_reasoning: string | null;
}

export interface MarketContext {
  regime: string | null;
  nifty_close: number | null;
  vix_close: number | null;
  fii_net_flow_cr: number | null;
  session_date: string;
}

export interface SessionInfo {
  session_id: string | null;
  status: string | null;
  completed_at: string | null;
  hours_since_run: number | null;
  cost_usd: number | null;
  trade_ready_count: number | null;
  watch_count: number | null;
}

export interface TodayResponse {
  stale: boolean;
  market_context: MarketContext | null;
  trade_ready: TradeSetup[];
  watch: TradeSetup[];
  session_info: SessionInfo | null;
}

export interface WatchlistEntry {
  symbol: string;
  current_stage: string | null;
  days_in_stage: number | null;
  direction_bias: string | null;
  last_analysis_notes: string | null;
  lot_size: number | null;
}

export interface SessionTurn {
  turn_number: number;
  turn_type: string;
  symbol: string | null;
  input_tokens: number;
  output_tokens: number;
  input_cost_usd: number;
  output_cost_usd: number;
  total_cost_usd: number;
}

export interface SessionTotals {
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  total_cost_inr: number;
  monthly_budget_usd: number;
  monthly_spent_usd: number;
  monthly_remaining_usd: number;
  sessions_remaining_estimate: number;
}

export interface ContextQuality {
  prescan_data_complete: boolean;
  deep_data_complete: boolean;
  oi_data_available: boolean;
  iv_data_available: boolean;
  fii_data_source: string;
  missing_data_flags: string[];
}

export interface CostInfo {
  monthly_spent_usd: number | null;
  budget_usd: number | null;
  budget_pct: number | null;
  last_session_cost: number | null;
  session_turns: SessionTurn[] | null;
  session_totals: SessionTotals | null;
  context_quality: ContextQuality | null;
  regime: string | null;
}

export interface SystemStatus {
  database: { connected: boolean };
  kite_token: { valid: boolean; expires_at: string | null; hours_remaining: number | null; error_message?: string | null };
  last_pipeline: {
    session_id: string | null;
    session_date: string | null;
    status: string | null;
    completed_at: string | null;
    hours_since_run: number | null;
    cost_usd: number | null;
  };
  cost: CostInfo | null;
  scheduler_jobs: { id: string; name: string; next_run: string | null }[];
  server_time_ist: string;
}

export interface DeepAnalysisTurn {
  turn_number: number;
  turn_type: 'market_context' | 'deep_analysis';
  symbol: string | null;
  completed_at: string;
  analysis: any; // Flexible for market_context or deep_analysis JSON
}

export interface DeepAnalysisResponse {
  turns: DeepAnalysisTurn[];
  session_id: string;
  session_date: string;
}

export interface AnalyseResponse {
  symbol:             string;
  session_date:       string;
  is_nifty50:         boolean;
  custom_symbol_note: string | null;
  analysis:           any;   // Turn 3 schema — see pipeline/claude_session.py
  estimated_cost_usd: number;
  data_quality_notes: string[];
  duration_seconds:   number;
  is_cached:          boolean;
  setup_id:           string | null;
}

export interface IndicatorValRow {
  system: number | null;
  tradingview: number | null;
  diff_pct: number | null;
}

export interface IndicatorValidation {
  symbol: string;
  date: string;
  indicators: {
    [key: string]: IndicatorValRow;
  };
  computation_method: string;
  warnings: string[];
  note: string;
}

export interface KitePosition {
  symbol: string;
  qty: number;
  avg: number;
  ltp: number;
  pnl: number;
  unrealised?: number;
  realised?: number;
  product?: string;
  exchange?: string;
}

export interface KiteHolding {
  symbol: string;
  qty: number;
  free_qty?: number;
  t1_qty?: number;
  collateral_qty?: number;
  collateral_type?: string;
  avg: number;
  ltp: number;
  close?: number;
  pnl: number;
  current_value?: number;
  investment_value?: number;
}

export interface ActiveTradesResponse {
  turns: DeepAnalysisTurn[];
  holdings: Record<string, KiteHolding>;
  positions: Record<string, KitePosition | KitePosition[]>;
  session_id: string | null;
  session_date: string | null;
}

export interface DeepAnalysisStatus {
  trading_day: string;
  session_id: string | null;
  session_status?: string | null;
  already_analyzed: boolean;
}

