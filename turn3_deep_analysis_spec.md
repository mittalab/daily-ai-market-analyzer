# Turn 3+ Deep Analysis — Complete Specification Document

**Version:** 1.0  
**Date:** 2026-07-04  
**System:** Post-Market F&O Swing Trading Analysis  
**Author:** Designed in collaboration with trading system architect  

---

## Overview

Turn 3+ is the deep analysis layer of the nightly pipeline. It runs once per forwarded stock, producing a complete trade setup with conviction scoring, trade parameters, instrument recommendation, and educational narrative. Each stock is an independent Claude API call.

### Pipeline Position

```
Turn 1 (Market Context)
  → Turn 2 (Pre-Scan, filters universe)
    → Turn 3+ (Deep Analysis, one call per stock)
      → Python Post-Processing (ranking, correlation)
        → Morning Brief (Telegram 7 AM)
```

### Key Design Principles

- One Claude call per stock — quality does not degrade with more stocks
- Claude receives all data, decides what is relevant
- Python enforces hard rules (RR gate, sector correlation, capital)
- Output has two layers: summary (collapsed UI) and detail (expanded UI)
- Every narrative must cite actual prices and dates — chart-verifiable

---

## Conviction Scoring Framework

### 4 Dimensions — 100 Points Total

```
Dimension 1: Price Structure          55 pts (55%)
Dimension 2: Risk/Reward              25 pts (25%)
Dimension 3: Market + Sector Context  15 pts (15%)
Dimension 4: Stock F&O Context         5 pts  (5%)
```

### Dimension 1: Price Structure (55 pts)

| Sub-Component | Points | What Claude Evaluates |
|---|---|---|
| S/R Zones | 15 | Price-based swing highs/lows + EMA20/50/180 dynamic levels. Confluence zones where multiple sources align. |
| Chart Patterns | 13 | 10 patterns: Bull/Bear Flag, Pennant, Ascending/Descending Triangle, Rectangle, H&S, Inverse H&S, Double Top/Bottom, Wedge. Quality, completion status, mechanical target. |
| Buyer/Seller Analysis | 12 | Body size vs range, wick analysis, close position within range, gap behaviour, sequence of last 5 candles. |
| Candlestick Patterns | 8 | 12 named patterns: Hammer/Hanging Man, Shooting Star/Inverted Hammer, Engulfing, Morning/Evening Star, Marubozu, Three Soldiers/Crows, Harami, Doji, Spinning Top, Inside Bar, Piercing/Dark Cloud, Pin Bar. Location and context matter. |
| RSI + MACD | 4 | RSI divergence detection only (not overbought/oversold). MACD momentum direction and histogram growing/shrinking. |
| Volume | 3 | Supporting evidence only. Volume trend, key volume days, today's context. Not a gate. |

**Scoring Guide for S/R Zones (15 pts):**
- 14-15: Major confluence — EMA + price S/R + chart pattern all at same zone
- 10-13: Clear S/R from 2 sources (e.g. EMA50 + prior swing low)
- 6-9: Single identifiable S/R level
- 2-5: Weak or ambiguous levels
- 0-1: No identifiable S/R — trade has no structural basis

**Scoring Guide for Chart Patterns (13 pts):**
- 12-13: Complete, clean, textbook pattern with mechanical target computable
- 8-11: Clear pattern, slightly imperfect boundaries
- 4-7: Partial/forming pattern, needs confirmation
- 1-3: Ambiguous, multiple interpretations
- 0: No pattern (does not disqualify — scores 0 here only)

**Scoring Guide for Buyer/Seller Analysis (12 pts):**
- 11-12: Last 5 candles show clear one-sided control, progressive conviction
- 7-10: Directional bias visible but not overwhelming
- 4-6: Mixed, contested sessions
- 1-3: Signs of opposing pressure building
- 0: Clear distribution/absorption by opposite side

**Scoring Guide for Candlestick Patterns (8 pts):**
- 7-8: High significance pattern at key S/R level
- 5-6: Medium significance at reasonable location
- 3-4: Low significance or less important level
- 1-2: Pattern conflicting with other analysis
- 0: No named pattern (does not disqualify)

**Scoring Guide for RSI + MACD (4 pts):**
- 4: Both confirming, no divergence, momentum building
- 3: One confirming, one neutral
- 2: Both neutral
- 1: One showing mild divergence warning
- 0: Divergence opposite to trade direction

**Scoring Guide for Volume (3 pts):**
- 3: Volume clearly confirming
- 2: Volume neutral or comparable
- 1: Slightly contradicting
- 0: Clearly contradicting

---

### Dimension 2: Risk/Reward (25 pts)

| Sub-Component | Points | What Claude Evaluates |
|---|---|---|
| Stop Loss Quality | 10 | Structural placement, ATR validation (0.5x-2x ATR sweet spot), clarity of invalidation logic |
| Target Logic | 8 | Both T1 and T2 at real S/R levels, realistic for 2-5 day timeframe, T2 achieves minimum 1:1.5 RR |
| Entry Zone Quality | 5 | Confluence at entry, zone tightness, structural basis |
| RR Ratio Score | 2 | Mathematical outcome of above three |

**SL Scoring (10 pts):**
- 9-10: Major structural confluence, ATR 0.75x-1.5x, crystal clear invalidation
- 6-8: Clear structural level, single source, ATR acceptable
- 3-5: Reasonable but less precise
- 1-2: Questionable placement
- 0: No structural SL → HARD GATE → REJECT

**Target Scoring (8 pts):**
- 7-8: Both targets at major S/R, realistic timeframe, T2 > 1:1.5 RR
- 5-6: Identifiable, one major one minor level
- 3-4: Present but less precise
- 1-2: Vague or poorly justified
- 0: RR < 1:1.5 → HARD GATE → REJECT

**Entry Zone Scoring (5 pts):**
- 5: Strong confluence, tight zone (< 1% price range)
- 3-4: Single clear level, acceptable width
- 1-2: Wide or vague
- 0: No clear entry logic

**RR Ratio Scoring (2 pts):**
- 2: RR >= 1:2.5
- 1.5: RR >= 1:2
- 1: RR >= 1:1.5
- 0: RR < 1:1.5 → HARD GATE → REJECT

**Hard Gates in Dimension 2:**
```
GATE 1: No structural SL → REJECT
GATE 2: RR < 1:1.5 → REJECT
```

---

### Dimension 3: Market + Sector Context (15 pts)

| Sub-Component | Points | Source |
|---|---|---|
| Index Context | 8 | Turn 1 trading_implication.index_bias + conviction_adjustment |
| Sector Context | 7 | Turn 1 sector_pictures for this stock's sector |

**Index Context Scoring (8 pts):**
- SUPPORTIVE: 7-8 pts (apply ADD_2 = cap at 8)
- NEUTRAL: 4-5 pts (SUBTRACT_2 = 2-3 pts)
- RESISTANT: 1-2 pts (SUBTRACT_5 = 0 pts floor)

**Sector Context Scoring (7 pts):**
- TAILWIND STRONG: 6-7 pts
- TAILWIND MODERATE: 4-5 pts
- NEUTRAL: 3 pts
- HEADWIND MODERATE: 1-2 pts
- HEADWIND STRONG: 0 pts

**Relative Strength Adjustment (within sector scoring):**
- Stock outperforming sector by 2%+: +1 pt
- Stock underperforming sector by 2%+: -1 pt

---

### Dimension 4: Stock F&O Context (5 pts)

| Sub-Component | Points | What Claude Evaluates |
|---|---|---|
| Futures Basis | 2 | Positive/growing = bullish carry, negative = warning |
| PCR Context | 2 | Contrarian indicator, thin OI noted as caveat |
| Rollover + DTE | 1 | DTE < 6 trading days = HARD GATE for options |

**Hard Gate in Dimension 4:**
```
GATE 3: DTE < 6 trading days (options instrument) → REJECT options
        (FUT trade may still be valid)
```

---

### Score Thresholds

```
TRADE_READY : adjusted_score >= 72
WATCH       : adjusted_score 52-71
ON_RADAR    : adjusted_score 35-51
REJECT      : adjusted_score < 35 OR any hard gate triggered

adjusted_score = raw_score × conviction_multiplier
conviction_multiplier from Turn 1 (0.70-1.10)

Example:
  Raw score: 78
  conviction_multiplier: 0.92
  adjusted_score: 78 × 0.92 = 71.8 → WATCH (not TRADE_READY)
```

---

## Hard Gates Summary

```
GATE 1: No structural SL identified → REJECT (any instrument)
GATE 2: RR < 1:1.5 at Target 2 → REJECT (any instrument)
GATE 3: DTE < 6 trading days → REJECT options only (FUT still valid)
GATE 4: Chart structure directly contradicts Turn 2 direction
        AND Claude finds no alternative valid direction → REJECT
```

---

## Data Package Per Stock

### Section 1: Stock Identity

```json
{
  "symbol": "HDFCBANK",
  "session_date": "2026-07-04",
  "is_mandatory": false,
  "preliminary_direction": "LONG",
  "preliminary_reason": "Banking TAILWIND STRONG, price above EMA20/50, volume 1.24x confirming",
  "previous_setups": [
    {
      "setup_date": "2026-06-25",
      "direction": "LONG",
      "conviction_score": 74,
      "stage": "WATCH",
      "setup_type": "Bull Flag",
      "paper_outcome": null
    }
  ]
}
```

### Section 2: Price History

```json
{
  "ohlcv_180d": [
    {"date": "2026-01-05", "open": 1680.0, "high": 1695.5, "low": 1672.0, "close": 1688.5, "volume": 12500000},
    "... 179 more rows ..."
  ],
  "volume_ratio_20d": 1.24
}
```

### Section 3: Pre-Computed Indicators (via pandas-ta)

```json
{
  "ema20": 1842.35,
  "ema50": 1798.12,
  "ema180": 1756.44,
  "atr14": 24.30,
  "atr_pct": 1.32,
  "rsi14": 44.20,
  "macd_line": 12.45,
  "macd_signal": 10.23,
  "macd_histogram": 2.22,
  "macd_histogram_direction": "GROWING",
  "rsi_last_20": [52.1, 49.3, 46.8, 44.2, 41.0, 38.5, 40.2, 42.8, 44.2, 43.1, 41.8, 40.5, 39.2, 38.8, 39.5, 40.8, 42.1, 43.5, 43.9, 44.2],
  "macd_hist_last_20": [-1.4, -1.2, -1.0, -0.8, -0.6, -0.4, -0.2, 0.1, 0.4, 0.8, 1.2, 1.6, 2.0, 2.2, 2.1, 2.0, 1.9, 2.1, 2.2, 2.22],
  "price_vs_ema20": "above",
  "price_vs_ema50": "above",
  "price_vs_ema180": "above",
  "ema_arrangement": "BULLISH"
}
```

### Section 4: Futures Data

```json
{
  "futures_available": true,
  "futures_30d": [
    {"date": "2026-06-04", "futures_price": 1690.5, "futures_open": 1685.0, "futures_high": 1695.0, "futures_low": 1682.0, "futures_volume": 850000, "basis": 2.5, "near_month_oi": 45000},
    "... 29 more rows ..."
  ],
  "basis_current": 2.50,
  "basis_trend": "EXPANDING",
  "rollover_phase": "NORMAL",
  "days_to_expiry": 18,
  "near_month_oi_trend": "INCREASING"
}
```

### Section 5: Options Data

```json
{
  "options_available": true,
  "pcr_near": 1.12,
  "max_pain": 1840.0,
  "atm_strike": 1840.0,
  "iv_available": false,
  "iv_atm": null,
  "options_note": "IV unavailable — Kite fallback used. OI data available but IV null. Use VIX as vol proxy.",
  "ce_walls": [
    {"strike": 1860.0, "oi": 45000},
    {"strike": 1900.0, "oi": 38000},
    {"strike": 1920.0, "oi": 32000}
  ],
  "pe_walls": [
    {"strike": 1840.0, "oi": 42000},
    {"strike": 1820.0, "oi": 35000},
    {"strike": 1800.0, "oi": 28000}
  ]
}
```

### Section 6: Sector Context

```json
{
  "sector_known": true,
  "stock_sector": "BANKING",
  "sector_picture": {
    "trend": "UPTREND",
    "momentum": "DECELERATING",
    "stance": "TAILWIND",
    "strength": "STRONG",
    "structure": "UPTREND_PULLBACK",
    "key_levels": {
      "support": 57078,
      "resistance": 58706,
      "breakout_above": 58706,
      "breakdown_below": 57078
    },
    "momentum_note": "Steady deceleration after breakout — controlled digestion not distribution",
    "character": "Banking broke from 53643 to high of 58706, consistent higher highs/lows",
    "trading_note": "Favour LONG setups — strongest sector tailwind tonight at +5.2% vs Nifty"
  }
}
```

**Note:** If sector_known = false, `all_sector_pictures` contains all 11 sectors from Turn 1.

### Section 7: Market Context (From Turn 1)

```json
{
  "market_trend": "SIDEWAYS",
  "market_volatility": "LOW",
  "execution_bias": "CAUTIOUS",
  "session_risk_level": "MEDIUM",
  "conviction_multiplier": 0.92,
  "nifty_price_structure": {
    "overall_structure": "RECOVERY",
    "trend_quality": "CONFLICTING",
    "trading_implication": {
      "summary": "Nifty structure mildly resistant tonight — at resistance zone with above-average volume rejection.",
      "index_bias": "RESISTANT",
      "conviction_adjustment": "SUBTRACT_2",
      "key_condition_to_watch": "Close above 24261 with volume > 500M upgrades index_bias to NEUTRAL"
    }
  },
  "vix_assessment": {
    "current": 13.61,
    "trend": "FALLING",
    "options_implication": "Option premiums compressed — cheap entry but moves must be sharp and timely"
  },
  "fii_dii_assessment": {
    "fii_20d_character": "FII aggressive net sellers for most of 20 days, cumulative -60,000 Cr",
    "recent_shift": "YES",
    "key_insight": "FII selling exhausted — oscillating near zero last 5 sessions"
  },
  "prescan_guidance": {
    "prefer_directions": ["LONG"],
    "prioritise_sectors": ["BANKING", "PHARMA", "FINSERV"],
    "deprioritise_sectors": ["IT", "METAL", "ENERGY"],
    "special_instructions": "Session is month-end — focus on multi-day setups not single-session spikes",
    "expiry_note": "Current week Nifty options expire tomorrow — irrelevant for stock option trades"
  }
}
```

### Section 8: Turn 2 Context

```json
{
  "turn2_assessment": {
    "symbol": "HDFCBANK",
    "preliminary_direction": "LONG",
    "reason": "Banking TAILWIND STRONG, price above EMA20/50, volume 1.24x confirming",
    "claude_forward_decision": "FORWARD",
    "is_mandatory": false,
    "inclusion_reason": "CLAUDE_FORWARD"
  }
}
```

---

## Output Schema Per Stock

### Block 1: Summary (Collapsed UI)

```json
{
  "symbol": "HDFCBANK",
  "direction": "LONG",
  "stage": "TRADE_READY",
  "conviction_score": 78,
  "adjusted_score": 71.8,
  "conviction_multiplier_applied": 0.92,

  "setup_summary": {
    "pattern_name": "Bull Flag",
    "pattern_status": "COMPLETE",
    "key_candle": "Hammer at support",
    "key_candle_location": "AT_SUPPORT",
    "key_candle_significance": "HIGH"
  },

  "key_levels": {
    "support_zone_low": 1838,
    "support_zone_high": 1845,
    "support_basis": "EMA20 + prior swing low confluence",
    "resistance_1": 1895,
    "resistance_1_basis": "Prior swing high May 15",
    "resistance_2": 1983,
    "resistance_2_basis": "Bull Flag mechanical target",
    "stop_loss": 1828,
    "stop_loss_basis": "Below confluence support zone"
  },

  "trade_parameters": {
    "entry_low": 1838,
    "entry_high": 1852,
    "entry_mid": 1845,
    "target_1": 1895,
    "target_2": 1983,
    "rr_t1": 2.9,
    "rr_t2": 8.1
  },

  "options_setup": {
    "strike": 1860,
    "option_type": "CE",
    "expiry": "2026-07-29",
    "days_to_expiry": 18,
    "entry_premium_low": 28,
    "entry_premium_high": 35,
    "sl_pct": 40,
    "sl_premium": 17,
    "target_1_premium": 58,
    "target_2_premium": 125,
    "iv_note": "IV unavailable — VIX 13.61 implies cheap premiums"
  },

  "fut_setup": {
    "entry_low": 1838,
    "entry_high": 1852,
    "stop_loss": 1828,
    "target_1": 1895,
    "target_2": 1983,
    "lots": 1,
    "lot_size": 550,
    "risk_inr": 9350,
    "risk_pct_capital": 1.87
  },

  "instrument_recommendation": "OPTIONS",
  "instrument_reason": "VIX 13.61 makes CE premium cheap at ₹28-35; defined risk suits RESISTANT index backdrop",

  "hard_gate_triggered": false,
  "hard_gate_reason": null,

  "scoring_breakdown": {
    "dimension_1": {"score": 42, "max": 55, "pct": 76},
    "dimension_2": {"score": 20, "max": 25, "pct": 80},
    "dimension_3": {"score": 12, "max": 15, "pct": 80},
    "dimension_4": {"score": 4, "max": 5, "pct": 80},
    "raw_total": 78,
    "adjusted_total": 71.8
  }
}
```

### Block 2: Detail (Expanded UI)

```json
{
  "dimension_1_narrative": "Full price structure narrative — all 6 sub-components with actual prices and dates. S/R zones with basis, chart pattern with pole/flag measurements, buyer/seller last 5 candle analysis, candlestick pattern at key level, RSI divergence assessment, volume character. Every number citable on TradingView chart.",

  "dimension_2_narrative": "Entry zone with structural basis, stop loss with ATR validation (actual calculation shown), T1 and T2 at named S/R levels, RR calculation step by step, position sizing with actual lot size and risk in rupees.",

  "dimension_3_narrative": "Index bias from Turn 1 with specific context, conviction_adjustment applied and explained, sector stance and strength with actual return numbers, stock vs sector relative performance.",

  "dimension_4_narrative": "Futures basis current value and trend, PCR reading with caveat about thin OI, DTE and rollover phase, what F&O data confirms or warns about.",

  "mentor_notes": "What this specific setup teaches — pattern-specific learning with why it works when it works and what to look for on the chart.",

  "why_could_be_wrong": "Three specific scenarios with actual price levels. Not generic. e.g. 'If HDFCBANK closes below 1828 on volume > 1.5x average, support has failed and flag is invalidated'",

  "key_thing_to_watch": "One specific actionable observation for when market opens. Entry conditions, gap scenarios, what to do if thesis breaks at open.",

  "rejection_reason": null
}
```

---

## Implementation Phases

---

## Phase 1: Indicator Computation Layer

**Goal:** Build `compute_stock_indicators()` function that takes raw OHLCV and returns all pre-computed indicators via pandas-ta with self-computation fallback.

**Files to create/modify:**
```
indicators/technical.py
  → add compute_stock_indicators(df) function
  → pandas-ta primary
  → self-computation fallback per indicator
  → returns complete indicators dict

indicators/validation.py (new)
  → validate_indicators_vs_manual(symbol, date)
  → GET /api/validate/indicators?symbol=HDFCBANK
  → returns side-by-side comparison table
```

**Input for testing Phase 1:**
```python
# Fetch from price_history for any Nifty 50 stock
# Minimum 180 rows required
df = pd.DataFrame({
    "date": [...],
    "open": [...],
    "high": [...],
    "low": [...],
    "close": [...],
    "volume": [...]
})
```

**Expected Output for Phase 1:**
```json
{
  "ema20": float,
  "ema50": float,
  "ema180": float or null,
  "atr14": float,
  "atr_pct": float,
  "rsi14": float,
  "macd_line": float,
  "macd_signal": float,
  "macd_histogram": float,
  "macd_histogram_direction": "GROWING | SHRINKING",
  "rsi_last_20": [float × 20],
  "macd_hist_last_20": [float × 20],
  "price_vs_ema20": "above | below",
  "price_vs_ema50": "above | below",
  "price_vs_ema180": "above | below | unavailable",
  "ema_arrangement": "BULLISH | BEARISH | MIXED",
  "volume_ratio_20d": float,
  "computation_method": "pandas_ta | fallback",
  "warnings": []
}
```

**Validation endpoint:**
```
GET /api/validate/indicators?symbol=HDFCBANK&date=2026-07-04

Returns:
{
  "symbol": "HDFCBANK",
  "date": "2026-07-04",
  "indicators": {
    "EMA20":          {"system": 1842.35, "tradingview": null, "diff_pct": null},
    "EMA50":          {"system": 1798.12, "tradingview": null, "diff_pct": null},
    "EMA180":         {"system": 1756.44, "tradingview": null, "diff_pct": null},
    "RSI14":          {"system": 44.20,   "tradingview": null, "diff_pct": null},
    "MACD_LINE":      {"system": 12.45,   "tradingview": null, "diff_pct": null},
    "MACD_SIGNAL":    {"system": 10.23,   "tradingview": null, "diff_pct": null},
    "MACD_HISTOGRAM": {"system": 2.22,    "tradingview": null, "diff_pct": null},
    "ATR14":          {"system": 24.30,   "tradingview": null, "diff_pct": null}
  },
  "note": "Enter TradingView values manually for comparison"
}
```

**Test Criteria:**
- [ ] pandas-ta returns non-null values for all indicators
- [ ] Fallback activates when pandas-ta raises exception
- [ ] EMA180 returns null when < 180 rows available (log warning)
- [ ] volume_ratio_20d computed correctly
- [ ] EMA relationship strings match actual values
- [ ] Validation endpoint returns all 8 indicators

---

## Phase 2: Data Package Builder

**Goal:** Build `_build_turn3_data(symbol, session_date, turn1_result, turn2_result)` that assembles the complete data package for one stock.

**Files to create/modify:**
```
pipeline/claude_session.py
  → _build_turn3_data(symbol, session_date,
                       turn1_result, turn2_result)
  → returns complete data dict per Section 1-8 spec
```

**Input for testing Phase 2:**
```python
symbol = "HDFCBANK"
session_date = date(2026, 7, 4)
turn1_result = {... full Turn 1 JSON output ...}
turn2_result = {... full Turn 2 JSON output ...}
```

**Expected Output for Phase 2:**
Complete data package matching Section 1-8 of spec above, with:
- All 8 sections populated
- ohlcv_180d: exactly 180 rows (or available max)
- futures_30d: exactly 30 rows (or null if unavailable)
- sector_picture OR all_sector_pictures based on sector_known flag
- All indicator scalars and series populated
- Null fields explicitly set to null (not missing)
- Data quality flags accurate

**Test Criteria:**
- [ ] ohlcv_180d has correct number of rows
- [ ] All indicator scalars are non-null for HDFCBANK
- [ ] sector_known = true for known sector stocks
- [ ] sector_known = false for OTHER sector stocks
- [ ] futures_available = true for F&O stocks
- [ ] options data correctly populated or flagged null
- [ ] turn2_assessment correctly populated from Turn 2 output
- [ ] previous_setups queries DB correctly (not hardcoded [])
- [ ] Market context correctly extracted from Turn 1 result
- [ ] Log summary after data preparation showing all section counts

---

## Phase 3: Prompt Assembly

**Goal:** Build `_build_turn3_prompt(data_package)` that converts the data package into the Claude user message prompt.

**Files to create/modify:**
```
pipeline/claude_session.py
  → _build_turn3_prompt(data_package)
  → returns plain text prompt string
```

**Prompt Structure:**
```
[SECTION A: ROLE AND TASK DEFINITION]
  Who Claude is for this turn
  What it must produce
  Scoring framework explained

[SECTION B: STOCK CONTEXT]
  Symbol, direction hypothesis from Turn 2
  Previous setups if any
  Mandatory stock flag context

[SECTION C: MARKET CONTEXT]
  From Turn 1 — condensed but complete
  conviction_multiplier to apply
  index_bias and conviction_adjustment
  Sector picture for this stock

[SECTION D: PRICE DATA]
  180 days OHLCV (compact JSON)
  Pre-computed indicators
  EMA relationships
  Volume ratio

[SECTION E: F&O DATA]
  Futures 30 days (if available)
  Options snapshot (if available)
  DTE and rollover phase

[SECTION F: SCORING INSTRUCTIONS]
  Detailed scoring rubric per dimension
  Hard gate definitions
  conviction_multiplier application

[SECTION G: OUTPUT SPECIFICATION]
  Complete JSON schema for Block 1 + Block 2
  Field-by-field requirements
  Narrative requirements (cite actual prices)
  "No text outside JSON" instruction
```

**Expected Output for Phase 3:**
Plain text prompt string ready for Claude API call.

**Test Criteria:**
- [ ] All 8 data sections appear in prompt
- [ ] Scoring rubric is complete and accurate
- [ ] Hard gates are explicitly stated
- [ ] Output schema matches Block 1 + Block 2 spec exactly
- [ ] Prompt token count estimated < 10,000 per stock
- [ ] Compact JSON used for OHLCV (not pretty-printed)
- [ ] EMA values injected as pre-computed strings
- [ ] conviction_multiplier value injected from Turn 1

---

## Phase 4: Claude API Call And Response Parsing

**Goal:** Build `_run_turn3(symbol, data_package, session_id, turn_number)` that calls Claude and parses the response.

**Files to create/modify:**
```
pipeline/claude_session.py
  → _run_turn3(symbol, data_package,
               session_id, turn_number)
  → returns parsed result dict
```

**API Call Parameters:**
```python
model: "claude-sonnet-4-6"
max_tokens: 4000
  # Typical output: ~2,600 tokens
  # Buffer: 1,400 tokens headroom
  # Pay only actual tokens used
system: same system prompt as Turn 1 and Turn 2
messages: single user message with assembled prompt
```

**Response Parsing:**
```python
# Use existing _parse_json()
# Validate Block 1 required fields:
required_block1 = [
    "symbol", "direction", "stage",
    "conviction_score", "adjusted_score",
    "setup_summary", "key_levels",
    "trade_parameters", "options_setup",
    "fut_setup", "instrument_recommendation",
    "instrument_reason", "hard_gate_triggered",
    "scoring_breakdown"
]

# Validate Block 2 required fields:
required_block2 = [
    "dimension_1_narrative",
    "dimension_2_narrative",
    "dimension_3_narrative",
    "dimension_4_narrative",
    "mentor_notes",
    "why_could_be_wrong",
    "key_thing_to_watch"
]

# Validate conviction_score: 0-100
# Validate adjusted_score: raw × multiplier
# Validate stage against thresholds
# Validate hard_gate_triggered matches hard gate rules
# Validate rr_t1 and rr_t2 are positive floats
```

**Python Financial Validation (after Claude response):**
```python
# Override Claude's arithmetic with Python calculation
entry_mid = (result["trade_parameters"]["entry_low"] +
             result["trade_parameters"]["entry_high"]) / 2

sl = result["key_levels"]["stop_loss"]
t2 = result["trade_parameters"]["target_2"]

# Risk per lot
lot_size = get_lot_size(symbol)  # from lot_sizes table
risk_per_lot = (entry_mid - sl) * lot_size  # LONG
               # (sl - entry_mid) * lot_size  # SHORT

# Position sizing
capital = 500000
risk_budget = capital * 0.025  # 2.5% risk
lots = max(1, min(floor(risk_budget / risk_per_lot), 5))
actual_risk_inr = risk_per_lot * lots
actual_risk_pct = actual_risk_inr / capital * 100

# RR calculation
actual_rr_t2 = (t2 - entry_mid) / (entry_mid - sl)

# Hard gate enforcement
if actual_rr_t2 < 1.5:
    result["hard_gate_triggered"] = True
    result["hard_gate_reason"] = f"RR {actual_rr_t2:.2f} < 1.5 minimum"
    result["stage"] = "REJECT"

# Overwrite Claude values with Python values
result["fut_setup"]["lots"] = lots
result["fut_setup"]["risk_inr"] = round(actual_risk_inr, 0)
result["fut_setup"]["risk_pct_capital"] = round(actual_risk_pct, 2)
result["trade_parameters"]["rr_t2"] = round(actual_rr_t2, 2)
```

**DB Save:**
```python
save_claude_turn(
    session_id=session_id,
    turn_number=turn_number,
    turn_type="deep_analysis",
    symbol=symbol,
    input_tokens=in_tok,
    output_tokens=out_tok,
    input_text=prompt,
    output_text=json.dumps(result)
)

# Save to trade_setups if not REJECT
if result["stage"] not in ("REJECT", None):
    create_trade_setup({
        "session_id": session_id,
        "setup_date": str(session_date),
        "symbol": symbol,
        "direction": result["direction"],
        "stage": result["stage"],
        "conviction_score": result["adjusted_score"],
        "raw_conviction_score": result["conviction_score"],
        # ... all trade parameters ...
        # ... Block 2 narrative fields ...
    })
```

**Telegram Notification (silent per stock):**
```
✅ HDFCBANK analysed — TRADE_READY 71.8/100
   Long | Bull Flag | Entry ₹1838-1852 | SL ₹1828
   T2 ₹1983 | RR 1:8.1 | OPTIONS recommended
   Cost: $0.028
```

**Test Criteria:**
- [ ] Claude API call succeeds with correct parameters
- [ ] Response parsed to dict successfully
- [ ] All required fields present in Block 1 and Block 2
- [ ] Python financial validation overwrites Claude values
- [ ] Hard gate correctly triggers on low RR
- [ ] REJECT stage not saved to trade_setups
- [ ] TRADE_READY/WATCH/ON_RADAR saved to trade_setups
- [ ] Turn saved to session_claude_turns with input_text
- [ ] Silent Telegram notification sent
- [ ] Token count and cost logged

---

## Phase 5: Deep Analysis Loop Orchestrator

**Goal:** Build the loop that runs Turn 3+ for all forwarded stocks sequentially and handles Python post-processing.

**Files to create/modify:**
```
pipeline/claude_session.py
  → _run_deep_analysis_loop(
        final_forward_list,
        session_id,
        session_date,
        turn1_result,
        turn2_result,
        config
    )
  → returns deep_results list
```

**Loop Logic:**
```python
deep_results = []
turn_number = 3  # starts at turn 3

for stock in final_forward_list:
    symbol = stock["symbol"]
    
    # Budget check before each stock
    monthly_spent = get_monthly_claude_spend()
    if monthly_spent >= budget_usd:
        logger.critical("Budget exhausted — stopping deep analysis")
        send_loud("🚨 Claude budget exhausted — pipeline stopped")
        break
    
    # Build data package
    data_package = _build_turn3_data(
        symbol, session_date,
        turn1_result, turn2_result
    )
    
    # Run Claude turn
    result = _run_turn3(
        symbol, data_package,
        session_id, turn_number
    )
    
    deep_results.append({
        "symbol": symbol,
        "stage": result["stage"],
        "direction": result["direction"],
        "adjusted_score": result["adjusted_score"],
        "conviction_score": result["conviction_score"],
        "rr_t2": result["trade_parameters"]["rr_t2"],
        "pattern_status": result["setup_summary"]["pattern_status"],
        "is_mandatory": stock["is_mandatory"],
        "inclusion_reason": stock["inclusion_reason"],
        "result": result
    })
    
    turn_number += 1
```

**Python Post-Processing After Loop:**

```python
# Step 1: Group by stage
trade_ready = [s for s in deep_results if s["stage"] == "TRADE_READY"]
watch = [s for s in deep_results if s["stage"] == "WATCH"]
on_radar = [s for s in deep_results if s["stage"] == "ON_RADAR"]
rejected = [s for s in deep_results if s["stage"] == "REJECT"]

# Step 2: Apply sector correlation rule to TRADE_READY
# Remove duplicate sector+direction — keep higher score
seen_sector_direction = {}
for stock in sorted(trade_ready, key=lambda x: x["adjusted_score"], reverse=True):
    sector = get_sector(stock["symbol"])
    direction = stock["direction"]
    key = f"{sector}_{direction}"
    
    if key not in seen_sector_direction:
        seen_sector_direction[key] = stock
    else:
        # Duplicate — demote to WATCH
        stock["stage"] = "WATCH"
        stock["demotion_reason"] = f"Sector correlation: {sector} {direction} already selected"
        watch.append(stock)
        trade_ready.remove(stock)

# Step 3: Sort each group by adjusted_score descending
trade_ready.sort(key=lambda x: x["adjusted_score"], reverse=True)
watch.sort(key=lambda x: x["adjusted_score"], reverse=True)
on_radar.sort(key=lambda x: x["adjusted_score"], reverse=True)

# Step 4: Composite ranking for morning brief (top 3 trade_ready)
def composite_score(stock):
    conviction_component = stock["adjusted_score"] * 0.5
    
    rr = stock["rr_t2"]
    rr_pts = 4 if rr >= 3.0 else 3 if rr >= 2.5 else 2 if rr >= 2.0 else 1
    rr_component = rr_pts * 7.5
    
    pattern_pts = 3 if stock["pattern_status"] == "COMPLETE" else 1 if stock["pattern_status"] == "FORMING" else 0
    pattern_component = pattern_pts * 6.67
    
    return conviction_component + rr_component + pattern_component

trade_ready_ranked = sorted(trade_ready, key=composite_score, reverse=True)

# Step 5: Update session
update_analysis_session(session_id, {
    "trade_ready_count": len(trade_ready),
    "watch_count": len(watch),
    "radar_count": len(on_radar),
    "status": "ANALYSIS_COMPLETE",
    "stage_statuses": {
        "deep_analysis": "COMPLETE",
        "deep_trade_ready": len(trade_ready),
        "deep_watch": len(watch),
        "deep_on_radar": len(on_radar),
        "deep_skip": len(rejected)
    }
})
```

**Expected Output for Phase 5:**
```json
{
  "trade_ready": [
    {
      "symbol": "HDFCBANK",
      "stage": "TRADE_READY",
      "adjusted_score": 71.8,
      "composite_rank_score": 84.5,
      "is_mandatory": false
    }
  ],
  "watch": [...],
  "on_radar": [...],
  "rejected": [...]
}
```

**Test Criteria:**
- [ ] Loop processes all forwarded stocks sequentially
- [ ] Budget check fires before each stock
- [ ] Sector correlation rule correctly demotes duplicate sector+direction
- [ ] Mandatory stocks never demoted by sector rule
- [ ] Trade_ready sorted by composite_score for morning brief
- [ ] All groups sorted by adjusted_score
- [ ] Session record updated correctly
- [ ] Total cost accumulated across all turns

---

## Phase 6: Morning Brief Formatting

**Goal:** Format Turn 3+ results into Telegram morning brief with collapsed summary per stock.

**Files to create/modify:**
```
pipeline/morning_brief.py
  → format_deep_analysis_brief(
        trade_ready_ranked,
        watch,
        session_date,
        session_summary
    )
  → returns formatted HTML string for Telegram
```

**Morning Brief Format (Telegram HTML):**
```
📊 <b>Morning Brief — 04 Jul 2026</b>
Market: SIDEWAYS | VIX: 13.61 | Execution: CAUTIOUS

🎯 <b>TRADE READY — 2 setups</b>

<b>1. HDFCBANK — LONG — 71.8/100</b>
Setup: Bull Flag (Complete) | Hammer at support
Support zone: <code>₹1838-1845</code>
Entry: <code>₹1838-1852</code> | SL: <code>₹1828</code>
T1: <code>₹1895</code> (RR 1:2.9) | T2: <code>₹1983</code> (RR 1:8.1)
OPTIONS: <code>1860 CE Jul 29 | ₹28-35</code>
Risk: ₹9,350 (1.87% capital) | 1 lot
Instrument: OPTIONS — VIX cheap, defined risk

<b>2. CIPLA — LONG — 68.5/100</b>
...

👁 <b>WATCH — 3 stocks</b>
• ICICIBANK LONG 65.2 — Bull Flag forming at EMA50
• DRREDDY LONG 58.4 — Breakout consolidating
• TECHM SHORT 54.1 — Breakdown confirmed, entry timing

📊 Session cost: $0.89 | 8 stocks analysed
Dashboard: trading.abhishekmittal.in
```

**Test Criteria:**
- [ ] TRADE_READY appears in ranked order
- [ ] All price levels wrapped in `<code>` tags
- [ ] WATCH section shows symbol, direction, score, one-line reason
- [ ] Cost and stock count accurate
- [ ] Dashboard link present
- [ ] Message length within Telegram 4096 char limit
  - If over: split into two messages

---

## Phase 7: Validation Dashboard UI

**Goal:** Add indicator validation UI to dashboard for manual TradingView comparison.

**Files to create/modify:**
```
Backend: main.py or routes/validation.py
  → GET /api/validate/indicators?symbol=HDFCBANK
  
Frontend: dashboard/src/pages/SystemStatus.jsx
  → Add "Indicator Validation" section
  → Dropdown to select any stock
  → Table showing system vs TradingView values
  → Manual input fields for TradingView values
  → Diff% column highlighting > 1% variance
```

**Validation UI Table:**
```
Stock: [HDFCBANK ▼]    Date: 04-Jul-2026    [Refresh]

Indicator       System Value    TradingView     Diff%
─────────────────────────────────────────────────────
EMA 20          1842.35         [____]          —
EMA 50          1798.12         [____]          —
EMA 180         1756.44         [____]          —
RSI 14          44.20           [____]          —
MACD Line       12.45           [____]          —
MACD Signal     10.23           [____]          —
MACD Histogram   2.22           [____]          —
ATR 14 (pts)    24.30           [____]          —
ATR% (of price)  1.32%          [____]          —

Computation: pandas-ta ✅
Warnings: None

[Calculate Diff]
```

**Test Criteria:**
- [ ] Endpoint returns all 8 indicators for any valid symbol
- [ ] UI renders correctly on mobile
- [ ] Diff calculation works on user input
- [ ] Red highlight on diff > 1%
- [ ] Computation method shown (pandas_ta or fallback)
- [ ] Warnings shown if any indicators null

---

## Testing Guide: How To Validate Each Phase

### Phase 1 Test
```
Run: python -c "
from indicators.technical import compute_stock_indicators
from database.queries import get_price_history
import pandas as pd

rows = get_price_history('HDFCBANK', days=200)
df = pd.DataFrame(rows)
result = compute_stock_indicators(df)
print(result)
"

Expected: All indicators populated, no null values
Compare EMA20 with TradingView HDFCBANK daily chart
```

### Phase 2 Test
```
Run: python -c "
from pipeline.claude_session import _build_turn3_data
from datetime import date

# Load turn1_result from latest session in DB
# Load turn2_result from latest session in DB
# Run for HDFCBANK

data = _build_turn3_data('HDFCBANK', date.today(), turn1_result, turn2_result)
print(json.dumps(data, indent=2, default=str))
"

Expected: All 8 sections present, correct data in each
```

### Phase 3 Test
```
Run: python -c "
from pipeline.claude_session import _build_turn3_prompt
# Use data_package from Phase 2 test

prompt = _build_turn3_prompt(data_package)
print(f'Prompt length: {len(prompt.split())} words')
print(prompt[:2000])  # First 2000 chars
"

Expected: Prompt < 10,000 tokens, all sections present
```

### Phase 4 Test
```
Run analysis on single stock:
POST /api/analyse {"symbol": "HDFCBANK", "direction": "AUTO"}

Expected output:
{
  "symbol": "HDFCBANK",
  "stage": "TRADE_READY | WATCH | ON_RADAR | REJECT",
  "conviction_score": float,
  "adjusted_score": float,
  "key_levels": {...},
  "trade_parameters": {...},
  "options_setup": {...},
  "fut_setup": {...},
  "instrument_recommendation": "OPTIONS | FUT",
  "dimension_1_narrative": "string with actual prices",
  "dimension_2_narrative": "string with ATR validation",
  "dimension_3_narrative": "string with index + sector",
  "dimension_4_narrative": "string with F&O data",
  "mentor_notes": "string",
  "why_could_be_wrong": "string",
  "key_thing_to_watch": "string"
}
```

### Phase 5 Test
```
Run full pipeline for a test session:
POST /api/pipeline/run {"session_id": "TEST_T3_20260704"}

After completion query:
SELECT symbol, stage, conviction_score,
       direction, setup_type
FROM trade_setups
WHERE session_id = 'TEST_T3_20260704'
ORDER BY conviction_score DESC;

Expected: All forwarded stocks have a record
REJECT stocks not in trade_setups
Sector correlation respected in TRADE_READY group
```

### Phase 6 Test
```
GET /api/morning-brief/preview?session_id=TEST_T3_20260704

Expected: Formatted Telegram HTML preview
TRADE_READY stocks shown first in ranked order
All prices in <code> tags
Cost and counts accurate
```

### Phase 7 Test
```
GET /api/validate/indicators?symbol=HDFCBANK

Open TradingView: HDFCBANK NSE Daily chart
Add EMA 20, EMA 50, RSI 14, MACD
Compare values manually

Expected: < 0.5% difference for EMA values
RSI and MACD within 1% of TradingView
```

---

## DB Schema Additions Needed

```sql
-- Add to trade_setups table if not present:
ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  raw_conviction_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  adjusted_conviction_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  conviction_multiplier_applied NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_1_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_2_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_3_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_4_score NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  hard_gate_triggered BOOLEAN DEFAULT FALSE;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  hard_gate_reason TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  instrument_recommendation VARCHAR(10);

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  options_strike NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  options_entry_premium_low NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  options_entry_premium_high NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  options_sl_pct NUMERIC;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_1_narrative TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_2_narrative TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_3_narrative TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  dimension_4_narrative TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  mentor_notes TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  why_could_be_wrong TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  key_thing_to_watch TEXT;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  setup_summary JSONB;

ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS
  is_mandatory BOOLEAN DEFAULT FALSE;
```

---

## Cost Summary

```
Per stock (typical):
  Input:  ~9,300 tokens × $3/1M  = $0.028
  Output: ~2,600 tokens × $15/1M = $0.039
  Total per stock:                  $0.067

Typical night (8 stocks):
  Turn 3+ cost: $0.067 × 8 = $0.536/session
  Monthly (22): $0.536 × 22 = $11.79/month

Busy night (15 stocks):
  Turn 3+ cost: $0.067 × 15 = $1.005/session
  Monthly (22): $1.005 × 22 = $22.11/month

Full pipeline monthly estimate:
  Turn 1:  $4.64
  Turn 2:  $2.79
  Turn 3+: $11.79 (typical) to $22.11 (busy)
  Total:   $19.22 to $29.54/month
  Budget:  $60/month
  Buffer:  $30-40 remaining ✅
```

---

## Implementation Order

```
Week 1:
  Phase 1: Indicator computation (pandas-ta + fallback)
  Phase 7: Validation dashboard UI
  → Validate indicators against TradingView before
    using them in any Claude prompt

Week 2:
  Phase 2: Data package builder
  Phase 3: Prompt assembly
  → Manual review of assembled prompt for
    one stock before calling Claude

Week 3:
  Phase 4: Claude API call + parsing (single stock)
  → Test with /api/analyse endpoint
  → Review output quality for 5 stocks manually

Week 4:
  Phase 5: Full loop + post-processing
  Phase 6: Morning brief formatting
  → End-to-end pipeline test
  → Review morning brief format
```
