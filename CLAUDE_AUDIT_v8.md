# CLAUDE_audit_v8.md
**Design Specification — Daily AI Market Analyzer**
**Version**: v8 — June 2026, full system design for new requirements generation
**Codebase**: daily-ai-market-analyzer @ main
**Prepared by**: Claude Sonnet 4.6 via complete codebase review

---

## TABLE OF CONTENTS

1. [System Overview](#1-system-overview)
2. [Daily Event Timeline](#2-daily-event-timeline)
3. [Pipeline Stage-by-Stage Flow](#3-pipeline-stage-by-stage-flow)
4. [Claude Multi-Turn Session Architecture](#4-claude-multi-turn-session-architecture)
5. [System Prompt — Turns 1 and 2 (Exact)](#5-system-prompt--turns-1-and-2-exact)
6. [Turn 1 — Market Context (Exact Proto)](#6-turn-1--market-context-exact-proto)
7. [Turn 2 — Pre-scan (Exact Proto)](#7-turn-2--pre-scan-exact-proto)
8. [Turns 3+ — Deep Analysis (Exact Proto)](#8-turns-3--deep-analysis-exact-proto)
9. [Chat Endpoint — /api/chat (Exact Proto)](#9-chat-endpoint--apichat-exact-proto)
10. [Variable Name Audit](#10-variable-name-audit)
11. [Key Design Decisions](#11-key-design-decisions)
12. [Database Tables](#12-database-tables)
13. [API Endpoints](#13-api-endpoints)

---

## 1. System Overview

Nightly automated swing trading analysis for Nifty 50 F&O stocks. Capital Rs 5,00,000. Stock options only (CE=LONG, PE=SHORT). 2-5 day holds. Max 3 concurrent trades. Risk 2-3% per trade. Min 1:2 RR (Python-enforced, not trusted from Claude).

**AI Model:** `claude-sonnet-4-6`
**Pricing tracked:** $3.00/1M input tokens, $15.00/1M output tokens
**Module constants** (set in `claude_session.py`):

```python
_MODEL         = "claude-sonnet-4-6"
_TOKEN_CEILING = 250_000    # session abort threshold
_MAX_RETRIES   = 3
_BACKOFF       = [5, 10, 20]  # seconds between retries
```

### Source File Map

| File | Role |
|------|------|
| `main.py` | FastAPI app, lifespan, CORS, Kite OAuth routes |
| `scheduler.py` | 10 APScheduler jobs |
| `pipeline/orchestrator.py` | Pipeline coordinator — calls all stages |
| `pipeline/claude_session.py` | Multi-turn session: Turn 1, Turn 2, Turns 3+ |
| `pipeline/deep_analysis.py` | `DEEP_SYSTEM`, stock data package, deep prompt, Claude call, position sizing |
| `pipeline/system_prompt_builder.py` | Builds Turn 1/2 system prompt from context bundle |
| `pipeline/context_builder.py` | DB reads -> context bundle dict |
| `pipeline/level1_filter.py` | Level 1 hard elimination (3 filters) |
| `pipeline/data_ingestion.py` | Bhavcopy, snapshot, Kite OHLCV/OI |
| `pipeline/market_regime.py` | Regime detection from Nifty + VIX |
| `pipeline/paper_trade_engine.py` | Entry/exit paper simulation |
| `api/dashboard.py` | Dashboard read endpoints + `/api/chat` |
| `database/queries.py` | All DB operations |
| `config/sector_map.json` | Sector assignments + holiday list |

---

## 2. Daily Event Timeline

| Time | Job | Source file |
|------|-----|-------------|
| 06:00 daily | Supabase keepalive | `scheduler.py:job_keepalive` |
| 07:00 Mon-Fri | Morning brief to Telegram | `pipeline/morning_brief.py` |
| 15:20 Mon-Fri | Option chain IV snapshot | `scheduler.py:job_option_snapshot` |
| 18:30 Mon-Fri | NSE bhavcopy + FII/DII | `scheduler.py:job_bhavcopy` |
| 19:00 Mon-Fri | Kite token reminder | `scheduler.py:job_token_reminder` |
| 19:00/19:30/20:00 Mon-Fri | Bhavcopy retries | `scheduler.py:job_bhavcopy_retry_*` |
| 21:30 Mon-Fri | Pre-flight check | `scheduler.py:job_preflight_check` |
| 22:00 Mon-Fri | Main analysis pipeline (currently manually triggered) | `pipeline/orchestrator.py:run_pipeline` |

---

## 3. Pipeline Stage-by-Stage Flow

`pipeline/orchestrator.py:run_pipeline(session_date: date)`

### Stage 0 — Session Init
Creates/reuses row in `analysis_sessions`. Sets `status = RUNNING`. Builds symbol list = Nifty 50 + active watchlist stocks.

### Stage 1 — Data Ingestion (pre-pipeline jobs + runtime fetch)

**3:20 PM** — `data_ingestion.py:run_snapshot_job` — Kite Quotes OI/premium + NSE option chain IV enrichment -> `options_snapshots`

**6:30 PM** — `data_ingestion.py:run_bhavcopy_job` — equity bhavcopy + indices + FII/DII -> `price_history`, `fii_dii_flows`

**10 PM inside pipeline** — `data_ingestion.py:run_kite_data_fetch` — 250d OHLCV -> `price_history`, near+next futures -> `futures_continuous_series`

### Stage 2.5 — OI Series Builder
`pipeline/oi_series_builder.py` — Merges near/next OI, computes `pcr_near`, `max_pain`, `rollover_pct`, `rollover_phase`. -> `continuous_oi_series`

### Stage 2.6 — Market Regime
`pipeline/market_regime.py:run_market_regime(analysis_date)` — reads `NIFTY_50` + `INDIA_VIX` -> `indicators/regime.py:detect_regime`.

Returns:
```python
{
    "regime":      "BULL_TRENDING",  # one of 6 values
    "nifty_close": 24350.5,
    "ema20":       24100.0,
    "ema50":       23800.0,
    "ret20d":      2.5,              # 20-day % return
    "vix":         14.2,             # NOTE: key is "vix" not "vix_close"
    "fallback":    False,
}
```

**Six regime values:** `BULL_TRENDING`, `BULL_VOLATILE`, `SIDEWAYS_TIGHT`, `SIDEWAYS_WIDE`, `BEAR_VOLATILE`, `BEAR_TRENDING`

### Stage 3 — Level 1 Filter
`pipeline/level1_filter.py:run_level1_filter` — three sequential filters:
1. Earnings within 5 trading days (NSE event calendar, keywords: `financial results`, `agm`, `board meeting`)
2. ATR(14) < 0.8% of price -> `ATR_DEAD` (shadow tracked 5 days)
3. ATM CE+PE OI < 10,000 contracts -> `FNO_ILLIQUID` (shadow tracked 5 days)

Output dict keys: `passed`, `eliminated`, `filter_skipped`, `errors`

### Stage 4 — Context Bundle
`pipeline/context_builder.py:build_context_bundle(session_date, session_id, regime_result)` — pure DB reads -> dict. Full structure in Section 5.

### Stage 5 — Claude Session
`pipeline/claude_session.py:run_claude_session(context_bundle, level1_passed, session_id, watchlist_priority)` — Sections 5-8.

### Stage 6 — Paper Trade Engine
`pipeline/paper_trade_engine.py` — Part A: entry check (2-day window, option premium vs entry zone). Part B: exit check (SL -> T1 partial -> T2 -> Day 5 time stop). Outcomes: `ENTRY_MISSED`, `SL_HIT`, `CLOSED_BREAKEVEN`, `TARGET_HIT`, `EXPIRED`.

---

## 4. Claude Multi-Turn Session Architecture

### Conversation Structure

```
API Call 1 — Stateful (Turns 1+2 share messages[])
  system:   [context bundle prompt + cache_control:ephemeral]
  messages: [
    {role:"user",      content:<Turn 1 JSON payload>},
    {role:"assistant", content:<Turn 1 response — market narrative JSON>},
    {role:"user",      content:<Turn 2 JSON payload>},
  ]
  -> returns: pre-scan array JSON

API Calls 2..N — Stateless (one independent call per forwarded stock)
  system:   [DEEP_SYSTEM constant + cache_control:ephemeral]
  messages: [
    {role:"user", content:<deep analysis JSON payload for stock X>},
  ]
  -> returns: trade setup JSON for stock X
```

### Session State (local variables in `run_claude_session`)

```python
messages:         list[dict]   # grows: user->T1, assistant->T1, user->T2 (never passed to deep turns)
turn_costs:       list[dict]   # per-turn token/cost tracking written to logs/session_cost_YYYYMMDD.json
total_input:      int          # running token sum across all turns
total_output:     int
trade_ready_list: list[dict]   # sector correlation tracking: [{symbol, sector, direction}, ...]
index_ctx:        dict         # built once from regime_result, passed unchanged to every deep turn
```

### Budget Circuit Breaker

```python
# pipeline/claude_session.py lines 426-440
config        = get_all_system_config()
budget_usd    = float(config.get("claude_monthly_budget_usd", 50.0))
monthly_spent = get_monthly_claude_spend()   # SUM(claude_cost_usd) from analysis_sessions this month

if monthly_spent >= budget_usd:
    send_budget_exhausted(monthly_spent, budget_usd, str(session_date))
    raise BudgetExhaustedException(...)
```

### Token Ceiling Check (after Turn 1, before Turn 2)

```python
# lines 488-493
if total_input + total_output + 25_000 >= _TOKEN_CEILING:   # 250,000
    raise RuntimeError("Token ceiling would be exceeded entering Turn 2")
```

### Index Context (built once, reused in all deep turns)

```python
# pipeline/claude_session.py lines 576-583
index_ctx = {
    "regime":      regime_result.get("regime"),
    "nifty_close": regime_result.get("nifty_close"),
    "vix":         regime_result.get("vix"),        # key is "vix" — same as regime_result source
    "ema20":       regime_result.get("ema20"),
    "ema50":       regime_result.get("ema50"),
    "ret20d_pct":  regime_result.get("ret20d"),
}
```

### Post-Analysis Python Overrides (applied after every deep turn response)

```python
# 1. Symbol injection (symbol not in Claude's response — injected by Python)
analysis["symbol"] = symbol

# 2. Python-authoritative position sizing
analysis = validate_position_sizing(analysis, config)

# 3. Sector correlation enforcement
if stage == "TRADE_READY":
    sym_sector, _ = _sector_info(symbol)
    conflict = next((r for r in trade_ready_list
                     if r["sector"] == sym_sector
                     and r["direction"] == analysis["direction"]
                     and sym_sector != "UNKNOWN"), None)
    if conflict:
        analysis["stage"] = "WATCH"   # downgrade — sector already covered
```

### Deep Analysis Queue Order

1. Watchlist re-analysis stocks (from `watchlist_priority` param) not in pre-scan -> inserted at front
2. Watchlist stocks that pre-scan also forwarded -> marked `is_watchlist_reanalysis=True`
3. HIGH priority pre-scan stocks
4. MEDIUM priority pre-scan stocks
5. LOW priority pre-scan stocks

---

## 5. System Prompt — Turns 1 and 2 (Exact)

**File:** `pipeline/system_prompt_builder.py:build_system_prompt(bundle: dict) -> str`

**How the caller wraps it** (`claude_session.py:_call_claude` lines 95-99):

```python
system = [{
    "type":          "text",
    "text":          system_text,           # plain string returned by build_system_prompt()
    "cache_control": {"type": "ephemeral"}, # enables Anthropic prompt caching
}]
```

This `system` list is passed identically to both Turn 1 and Turn 2 API calls. It is NOT used in Turns 3+.

### Context Bundle Input

`build_context_bundle` returns this dict (all keys guaranteed present):

```python
{
    "session_date":      date,           # e.g. date(2026, 6, 24)
    "session_id":        str,            # "SESSION_20260624"
    "config":            dict,           # all rows from system_config table as {key: value}
    "regime":            dict | None,    # regime_result from run_market_regime()
    "system_memory":     [],             # Phase 2 placeholder, always []
    "active_directives": [],             # Phase 2 placeholder, always []
    "active_watchlist":  list[dict],     # watchlist_staging rows (WATCH/ON_RADAR stages)
    "open_positions":    list[dict],     # trade_setups where entry_triggered=True, paper_outcome IS NULL
    "recent_outcomes":   list[dict],     # trade_setups last 7 days with paper_outcome set
    "available_slots":   int,            # max_slots - len(open_positions), floored at 0
    "max_slots":         int,            # from system_config.max_concurrent_trades (default 3)
    "rollover_context":  dict | None,    # latest row from continuous_oi_series
}
```

### Exact System Prompt Template

From `pipeline/system_prompt_builder.py:build_system_prompt` lines 114-155:

```
You are an experienced hedge fund manager and swing trading mentor specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, stock options only — monthly Tuesday expiry).

━━━━━ TONIGHT'S SESSION CONTEXT ━━━━━
Date          : {date_str}
Market Regime : {regime}  (Nifty {nifty_close:.1f} | VIX {vix:.2f})
Trade Slots   : {available_slots} of {max_slots} available
Capital at Risk: ₹{open_risk:,.0f} ({open_risk_pct:.1f}%)

━━━━━ ROLLOVER CONTEXT ━━━━━
{_rollover_block(rollover_ctx)}

━━━━━ PCR INTERPRETATION GUIDE ━━━━━
PCR is contrarian at extremes:
PCR < 0.7  → contrarian bearish (excessive bullishness)
PCR 0.7-1.1 → neutral
PCR > 1.3  → contrarian bullish (excessive bearishness)
Do NOT interpret high PCR as automatically bearish.

━━━━━ SIGNAL PERFORMANCE ━━━━━
[Phase 1: Signal attribution building — use general judgment.]

━━━━━ RECENT OUTCOMES ━━━━━
{_recent_outcomes_block(outcomes)}

━━━━━ ACTIVE WATCHLIST ━━━━━
{_watchlist_block(watchlist)}

━━━━━ OPEN POSITIONS ━━━━━
{_open_positions_block(open_positions)}

━━━━━ OPERATING RULES ━━━━━
Capital        : ₹5,00,000
Risk per trade : 2-3% (₹10,000-15,000)
Min RR         : 1:2 (hard gate — reject below)
Max setups     : {available_slots} Trade Ready tonight
Min DTE        : 6 trading days
Expiry         : Monthly Tuesday
Instruments    : Stock options ONLY
Sector rule    : No two stocks from same sector + same direction
Do NOT force setups — SKIP is always valid
```

### Rollover Block Values (from `_ROLLOVER_BLOCKS` dict, lines 20-36)

```python
"NORMAL":         "(No special rollover context — normal expiry week.)"
"ROLLOVER_WATCH": "Rollover beginning. Near month OI declining partially reflects rolling, "
                  "not just direction. Monitor next month OI alongside. "
                  "Futures basis direction more meaningful."
"TRANSITION":     "Next month now dominant. Near month OI collapse expected. "
                  "Recommend next month expiry for all new trades."
"EXPIRY":         "Expiry day. Near month settled — OI data is settlement noise. "
                  "Use yesterday's OI as last valid reference. "
                  "Weight price structure and futures basis heavily today."
```

Rendered block format: `[{phase}]: {text}\n  (Near expiry: {near_expiry} | Rollover %: {rollover_pct:.1f}%)`

### Block Functions and Output Format

**`_recent_outcomes_block`:**
```
  HDFCBANK     2026-06-20  -> TARGET_HIT
  INFY         2026-06-19  -> SL_HIT
```
Empty fallback: `"(No completed trades in the last 7 days.)"`

**`_watchlist_block`:**
```
  RELIANCE     stage=WATCH (3d)
  AXISBANK     stage=ON_RADAR (1d)
```
Empty fallback: `"(Watchlist empty — first session.)"`

**`_open_positions_block`:**
```
  HDFCBANK     LONG
  WIPRO        SHORT
```
Empty fallback: `"(No open positions.)"`

### System Prompt Variable Sources

| Variable | Source expression | Intent |
|----------|-------------------|--------|
| `date_str` | `session_date.strftime("%A, %d %b %Y")` | e.g. "Tuesday, 24 Jun 2026" |
| `regime` | `regime_result.get("regime", "UNKNOWN")` | Macro backdrop label |
| `nifty_close` | `regime_result.get("nifty_close") or 0.0` | Tonight's Nifty reference level |
| `vix` | `regime_result.get("vix") or 0.0` | India VIX value (note: key `"vix"` in regime_result) |
| `available_slots` | `max_slots - len(open_positions)` | Hard cap on new TRADE_READY setups tonight |
| `max_slots` | `config.get("max_concurrent_trades", 3)` | From DB `system_config` table |
| `open_risk` | `sum(float(p.get("risk_amount", 0) or 0) for p in open_positions)` | Rs already at risk from open trades |
| `open_risk_pct` | `open_risk / capital * 100` | Percentage of Rs 5,00,000 committed |
| rollover block | `get_rollover_context(session_date)` -> latest `continuous_oi_series` row | Tells Claude how to interpret OI data tonight |
| outcomes block | `get_recent_outcomes(days=7)` | Last 7 days paper trade results — feedback loop |
| watchlist block | `get_watchlist()` WATCH/ON_RADAR rows | Stocks Claude should re-evaluate tonight |
| positions block | `get_open_trade_setups()` | Prevents sector duplication and slot overflow |

---

## 6. Turn 1 — Market Context (Exact Proto)

**File:** `pipeline/claude_session.py:_build_turn1_message(session_date, regime_result) -> str`

### API Call (lines 461-462)

```python
t1_resp = _call_claude(client, system_text, messages, max_tokens=1500)
```

**Full API call structure sent to Anthropic:**

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1500,
    system=[{
        "type":          "text",
        "text":          system_text,        # context bundle system prompt
        "cache_control": {"type": "ephemeral"},
    }],
    messages=[
        {"role": "user", "content": <Turn1UserMessage>},
    ],
)
```

### Turn 1 User Message — Exact Python Construction

```python
# lines 169-197
payload = {
    "turn":         "market_context",
    "session_date": str(session_date),           # e.g. "2026-06-24"
    "regime":       regime_result.get("regime"),
    "nifty_close":  regime_result.get("nifty_close"),
    "vix_close":    regime_result.get("vix"),    # NOTE: source key "vix" -> payload key "vix_close"
    "ema20":        regime_result.get("ema20"),
    "ema50":        regime_result.get("ema50"),
    "ret20d_pct":   regime_result.get("ret20d"),
    "nifty_30d":    nifty_30,
    "vix_30d":      vix_30,
    "fii_dii_30d":  fii_30,
    "nifty_oi_walls": nifty_oi_walls,
}

# Series construction:
nifty_rows = get_price_history("NIFTY_50",  days=32)
vix_rows   = get_price_history("INDIA_VIX", days=32)
fii_rows   = get_fii_dii_flows(days=32)

nifty_30 = [
    {"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]}
    for r in nifty_rows[-30:]
]
vix_30  = [{"date": r["date"], "close": r["close"]} for r in vix_rows[-30:]]
fii_30  = [
    {"date": r["date"], "fii_net_cr": r.get("fii_net_cr"), "dii_net_cr": r.get("dii_net_cr")}
    for r in fii_rows[-30:]
]

# OI walls computed by oi_walls() from deep_analysis.py:
nifty_oi_walls = {
    "ce_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in top5_CE],
    "pe_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in top5_PE],
}
# Fallback: {} if no Nifty snapshot available

# Final message string:
return json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions
```

### Complete Turn 1 User Message (Example)

```json
{
  "turn": "market_context",
  "session_date": "2026-06-24",
  "regime": "BULL_TRENDING",
  "nifty_close": 24350.5,
  "vix_close": 14.2,
  "ema20": 24100.0,
  "ema50": 23800.0,
  "ret20d_pct": 2.5,
  "nifty_30d": [
    {"date": "2026-05-25", "close": 23800.0, "high": 23950.0, "low": 23720.0},
    {"date": "2026-05-26", "close": 23950.0, "high": 24050.0, "low": 23810.0},
    "...28 more rows...",
    {"date": "2026-06-24", "close": 24350.5, "high": 24420.0, "low": 24280.0}
  ],
  "vix_30d": [
    {"date": "2026-05-25", "close": 16.1},
    "...29 more rows...",
    {"date": "2026-06-24", "close": 14.2}
  ],
  "fii_dii_30d": [
    {"date": "2026-05-25", "fii_net_cr": -800.0, "dii_net_cr": 650.0},
    "...29 more rows...",
    {"date": "2026-06-24", "fii_net_cr": 1234.5, "dii_net_cr": -500.0}
  ],
  "nifty_oi_walls": {
    "ce_walls": [
      {"strike": 24500, "oi": 5000000},
      {"strike": 25000, "oi": 4200000},
      {"strike": 24600, "oi": 3800000},
      {"strike": 24700, "oi": 3200000},
      {"strike": 25500, "oi": 2800000}
    ],
    "pe_walls": [
      {"strike": 24000, "oi": 4500000},
      {"strike": 23500, "oi": 3800000},
      {"strike": 24200, "oi": 3200000},
      {"strike": 23000, "oi": 2900000},
      {"strike": 24300, "oi": 2500000}
    ]
  }
}

Analyse the market context above. Factor in the Nifty 50 OI walls (Support=PE walls, Resistance=CE walls) when defining key levels and narrative. Respond with ONLY a JSON object — no commentary outside the JSON:
{
  "session_narrative": "3-4 sentences on market condition and tone tonight",
  "risk_flags": ["key risk 1", "key risk 2"],
  "favourable_setups": "LONG | SHORT | NEUTRAL | BOTH",
  "index_key_levels": {"support": 0, "resistance": 0}
}
```

### Turn 1 Field-by-Field Intent

| Field | Source | Intent |
|-------|--------|--------|
| `turn` | `"market_context"` hardcoded | Identifies this payload type to Claude |
| `session_date` | `str(session_date)` | Grounds Claude in the correct trading date |
| `regime` | `regime_result["regime"]` | Pre-computed label. Claude can confirm or challenge based on raw data provided |
| `nifty_close` | `regime_result["nifty_close"]` | Tonight's Nifty reference level for the narrative |
| `vix_close` | `regime_result["vix"]` | **Source key is `"vix"`, payload key is `"vix_close"` — naming split (Issue 5)** |
| `ema20` | `regime_result["ema20"]` | Short-term trend EMA |
| `ema50` | `regime_result["ema50"]` | Medium-term trend EMA. Together with ema20 confirms regime |
| `ret20d_pct` | `regime_result["ret20d"]` | 20-day return — momentum strength |
| `nifty_30d` | `price_history` for `NIFTY_50`, 30 rows | Full price series for trend/pattern/gap analysis |
| `vix_30d` | `price_history` for `INDIA_VIX`, 30 rows | Volatility trend. Expanding = risk-off. Spikes = event risk |
| `fii_dii_30d` | `fii_dii_flows` 30 rows, values in Crores | Institutional flow direction and consistency. FII sustained buying = bullish backdrop |
| `nifty_oi_walls.ce_walls` | Top 5 CE OI strikes from `options_snapshots` for `NIFTY_50` | Resistance from options market. High CE OI = option sellers defending that level |
| `nifty_oi_walls.pe_walls` | Top 5 PE OI strikes from `options_snapshots` for `NIFTY_50` | Support from options market. High PE OI = put writers defending that level |

### Turn 1 Instructions Text (exact, lines 184-196)

```
Analyse the market context above. Factor in the Nifty 50 OI walls (Support=PE walls, Resistance=CE walls) when defining key levels and narrative. Respond with ONLY a JSON object — no commentary outside the JSON:
{
  "session_narrative": "3-4 sentences on market condition and tone tonight",
  "risk_flags": ["key risk 1", "key risk 2"],
  "favourable_setups": "LONG | SHORT | NEUTRAL | BOTH",
  "index_key_levels": {"support": 0, "resistance": 0}
}
```

### Turn 1 Expected Response Schema

```json
{
  "session_narrative": "string — 3-4 sentence market assessment",
  "risk_flags":        ["string", "string"],
  "favourable_setups": "LONG | SHORT | NEUTRAL | BOTH",
  "index_key_levels": {
    "support":    24000,
    "resistance": 24500
  }
}
```

### After Turn 1 — Message and DB Flow

```python
# lines 473-485
save_claude_turn(session_id, 1, "market_context", None,
                 u1.input_tokens, u1.output_tokens, t1_text_user, t1_out_text)

messages.append({"role": "assistant", "content": t1_out_text})  # raw text, not re-serialised
turn1_result = _parse_json(t1_out_text)   # parsed for dashboard use only
```

Turn 1 assistant response appended as raw text — Turn 2 sees it verbatim in conversation history.

### Turn 1 Response Downstream Usage

| Field | Used in |
|-------|---------|
| `session_narrative` | `_format_chat_context` -> "Your Market Narrative" |
| `risk_flags` | `_format_chat_context` -> "Risk Flags You Identified" bullets |
| `favourable_setups` | `_format_chat_context` -> "Favourable Setups" line |
| `index_key_levels.support` | `_format_chat_context` -> "Key Support" |
| `index_key_levels.resistance` | `_format_chat_context` -> "Key Resistance" |

---

## 7. Turn 2 — Pre-scan (Exact Proto)

**File:** `pipeline/claude_session.py:_build_turn2_message(level1_passed, session_date) -> str`

### API Call (lines 501-502)

```python
t2_resp = _call_claude(client, system_text, messages, max_tokens=12000)
```

**Full API call structure:**

```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=12000,                    # large — ~40 stocks x ~300 tokens each
    system=[{
        "type":          "text",
        "text":          system_text,    # SAME context bundle prompt as Turn 1 -> cache HIT
        "cache_control": {"type": "ephemeral"},
    }],
    messages=[
        {"role": "user",      "content": <Turn1UserMessage>},
        {"role": "assistant", "content": <Turn1ResponseText>},
        {"role": "user",      "content": <Turn2UserMessage>},   # <- new
    ],
)
```

### Turn 2 User Message — Exact Python Construction

```python
# lines 266-291
payload = {
    "turn":         "prescan",
    "session_date": str(session_date),
    "stock_count":  len(stocks),         # actual count after data-skip exclusions
    "stocks":       stocks,              # list of per-stock dicts
}

return json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions
```

### Per-Stock Object — Exact Code (`_stock_data`, lines 202-248)

```python
def _stock_data(symbol: str, session_date: date) -> dict | None:
    rows = get_price_history(symbol, days=40)
    if len(rows) < 20:
        return None          # excluded from payload, logged as warning

    df      = pd.DataFrame(rows)
    closes  = df["close"]
    ema20_s = calculate_ema(closes, 20)
    ema50_s = calculate_ema(closes, 50) if len(df) >= 50 else ema20_s
    rsi_s   = calculate_rsi(closes, 14)
    atrp_s  = atr_pct(df, 14)
    volr_s  = volume_ratio(df["volume"], short=3, long=20)

    last30  = [round(float(c), 2) for c in closes.iloc[-30:].tolist()]

    oi_rows  = get_continuous_oi(symbol, days=10)
    oi_10d   = [r.get("near_month_oi") for r in oi_rows]
    latest   = oi_rows[-1] if oi_rows else {}

    fut       = get_futures_row(symbol, session_date)
    fut_price = float(fut["futures_price"]) if fut and fut.get("futures_price") else None
    basis_p   = float(fut["basis_pct"])     if fut and fut.get("basis_pct")     else None

    return {
        "sym":            symbol,                          # KEY IS "sym" NOT "symbol" — Issue 1
        "close":          round(float(closes.iloc[-1]), 2),
        "closes_30d":     last30,                          # list of 30 floats
        "rsi14":          _val(rsi_s),
        "ema20":          round(float(ema20_s.iloc[-1]), 2),
        "ema50":          round(float(ema50_s.iloc[-1]), 2),
        "atr_pct14":      _val(atrp_s),
        "vol_ratio":      _val(volr_s),
        "oi_10d":         oi_10d,                          # list of 10 near_month_oi values
        "futures_price":  fut_price,
        "basis_pct":      basis_p,
        "pcr_near":       latest.get("pcr_near"),
        "max_pain":       latest.get("max_pain"),
        "rollover_phase": latest.get("rollover_phase"),
    }
```

### Complete Per-Stock Example

```json
{
  "sym": "HDFCBANK",
  "close": 1650.25,
  "closes_30d": [
    1580.0, 1582.5, 1590.0, 1585.0, 1595.5, 1601.0, 1598.0, 1610.0, 1608.5, 1615.0,
    1620.0, 1618.5, 1625.0, 1630.5, 1628.0, 1635.0, 1633.5, 1640.0, 1638.5, 1645.0,
    1643.0, 1648.5, 1646.0, 1649.5, 1647.5, 1651.0, 1649.0, 1652.0, 1650.5, 1650.25
  ],
  "rsi14": 58.4,
  "ema20": 1620.3,
  "ema50": 1583.7,
  "atr_pct14": 1.24,
  "vol_ratio": 1.83,
  "oi_10d": [12500000, 12550000, 12600000, 12650000, 12700000, 12720000, 12750000, 12780000, 12850000, 13100000],
  "futures_price": 1653.4,
  "basis_pct": 0.19,
  "pcr_near": 0.85,
  "max_pain": 1600,
  "rollover_phase": "NORMAL"
}
```

### Complete Turn 2 Payload Structure

```json
{
  "turn": "prescan",
  "session_date": "2026-06-24",
  "stock_count": 38,
  "stocks": [
    { "sym": "HDFCBANK", "close": 1650.25, "closes_30d": [...30 values...], "rsi14": 58.4, "ema20": 1620.3, "ema50": 1583.7, "atr_pct14": 1.24, "vol_ratio": 1.83, "oi_10d": [...10 values...], "futures_price": 1653.4, "basis_pct": 0.19, "pcr_near": 0.85, "max_pain": 1600, "rollover_phase": "NORMAL" },
    { "sym": "INFY",     "close": 1520.0,  "closes_30d": [...], "rsi14": 45.2, "ema20": 1535.0, "ema50": 1550.0, "atr_pct14": 0.95, "vol_ratio": 0.92, "oi_10d": [...], "futures_price": 1522.5, "basis_pct": 0.16, "pcr_near": 1.1, "max_pain": 1500, "rollover_phase": "NORMAL" },
    "...36 more stocks..."
  ]
}
```

### Turn 2 Instructions Text (exact, lines 273-289)

```
Pre-scan all {N} stocks above. For each stock, assess direction and priority based on the data provided. Respond with ONLY a JSON array — one object per stock, no commentary:
[
  {
    "symbol": "HDFCBANK",
    "direction": "LONG",
    "pre_scan_reasoning": "2-3 lines max",
    "priority": "HIGH",
    "forward_to_deep": true,
    "override_level1": false,
    "override_reason": null
  },
  ...
]
```

### Turn 2 Per-Stock Field Intent

| Field | DB source | Computation | Intent |
|-------|-----------|-------------|--------|
| `sym` | `price_history` | Symbol string | **Named `"sym"` not `"symbol"` — Issue 1** |
| `close` | `price_history.close` latest | `closes.iloc[-1]` | Current price. Reference level |
| `closes_30d` | `price_history.close` 30 rows | `closes.iloc[-30:].tolist()` | Price trend series. Claude reads momentum direction from sequence |
| `rsi14` | Computed | `calculate_rsi(closes, 14).iloc[-1]` | Overbought (>70) / oversold (<30). 55-70 range on LONG = good entry zone |
| `ema20` | Computed | `calculate_ema(closes, 20).iloc[-1]` | Price > EMA20 = immediate bullish bias |
| `ema50` | Computed | `calculate_ema(closes, 50).iloc[-1]` | EMA20 > EMA50 = confirmed uptrend |
| `atr_pct14` | Computed | `atr_pct(df, 14).iloc[-1]` | Volatility level. Stock passed Level 1 (>=0.8%) |
| `vol_ratio` | Computed | `volume_ratio(volume, short=3, long=20).iloc[-1]` | Volume confirmation. >1.5 on breakout = strong signal |
| `oi_10d` | `continuous_oi_series.near_month_oi` 10 rows | `[r.get("near_month_oi") for r in oi_rows]` | OI trend. Building = new positions = conviction. **Ambiguous name — Issue 6** |
| `futures_price` | `futures_continuous_series.futures_price` latest | `fut.get("futures_price")` | Near-month futures level |
| `basis_pct` | `futures_continuous_series.basis_pct` latest | `fut.get("basis_pct")` | Positive = bullish futures premium |
| `pcr_near` | `continuous_oi_series.pcr_near` latest | `latest_oi.get("pcr_near")` | Contrarian: >1.3 bullish, <0.7 bearish |
| `max_pain` | `continuous_oi_series.max_pain` latest | `latest_oi.get("max_pain")` | Magnetic strike near expiry |
| `rollover_phase` | `continuous_oi_series.rollover_phase` latest | `latest_oi.get("rollover_phase")` | EXPIRY = OI data unreliable |

### Turn 2 Expected Response Schema (one entry per stock)

```json
[
  {
    "symbol":            "HDFCBANK",
    "direction":         "LONG",
    "pre_scan_reasoning": "EMA20 breakout with RSI at 58 and volume 1.8x average. OI building steadily. Positive basis confirms direction.",
    "priority":          "HIGH",
    "forward_to_deep":   true,
    "override_level1":   false,
    "override_reason":   null
  },
  {
    "symbol":            "WIPRO",
    "direction":         "SHORT",
    "pre_scan_reasoning": "Failed at EMA50 resistance. RSI diverging. OI flat.",
    "priority":          "LOW",
    "forward_to_deep":   false,
    "override_level1":   false,
    "override_reason":   null
  }
]
```

### Turn 2 Response Field Intent

| Field | Intent | Downstream use |
|-------|--------|---------------|
| `symbol` | Stock identifier in response. Note: input used `"sym"` — inconsistency | Queue building, DB, dashboard |
| `direction` | `LONG` or `SHORT` — passed as direction lock to deep analysis | `build_deep_prompt(stock_pkg, index_ctx, direction)` |
| `pre_scan_reasoning` | 2-3 line reasoning | Dashboard "Notable skips" (truncated to 120 chars) |
| `priority` | `HIGH`/`MEDIUM`/`LOW` — sort key for deep analysis queue | Queue ordering |
| `forward_to_deep` | Boolean gate | `forwarded_stocks = [s for s in turn2_results if s.get("forward_to_deep")]` |
| `override_level1` | Rare true if Claude overrides Level 1 filter | Logged |
| `override_reason` | Justification for override | Logged |

### Turn 2 Queue Sorting (exact code, lines 549-553)

```python
forwarded_stocks = [s for s in turn2_results if s.get("forward_to_deep")]
forwarded_stocks.sort(key=lambda s: (s.get("priority") != "HIGH", s.get("priority") != "MEDIUM"))
```

---

## 8. Turns 3+ — Deep Analysis (Exact Proto)

**File:** `pipeline/deep_analysis.py`

### 8.1 `DEEP_SYSTEM` — Exact Constant (lines 52-61)

```python
DEEP_SYSTEM = (
    "You are an experienced hedge fund manager and swing trading mentor "
    "specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, "
    "stock options only — monthly Tuesday expiry).\n\n"
    "Operating rules:\n"
    "  Capital: ₹5,00,000 | Risk per trade: 2-3% | Min RR: 1:2\n"
    "  Instruments: stock options ONLY | Min DTE: 6 trading days\n"
    "  PCR > 1.3 = contrarian BULLISH | PCR < 0.7 = contrarian BEARISH\n"
    "  Do NOT force setups — SKIP is always valid"
)
```

Rendered text:
```
You are an experienced hedge fund manager and swing trading mentor specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, stock options only — monthly Tuesday expiry).

Operating rules:
  Capital: ₹5,00,000 | Risk per trade: 2-3% | Min RR: 1:2
  Instruments: stock options ONLY | Min DTE: 6 trading days
  PCR > 1.3 = contrarian BULLISH | PCR < 0.7 = contrarian BEARISH
  Do NOT force setups — SKIP is always valid
```

### 8.2 Deep Analysis API Call (exact, `call_claude_deep` lines 491-502)

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=3000,
    system=[{
        "type":          "text",
        "text":          DEEP_SYSTEM,            # constant — same every turn, every session
        "cache_control": {"type": "ephemeral"},  # maximum cache hit rate
    }],
    messages=[{"role": "user", "content": prompt_text}],  # single-turn, no history
)
```

`prompt_text` = `json.dumps({"task": "deep_analysis", "index_context": index_ctx, "stock": stock_pkg}) + "\n\n" + instructions + [conditional additions]`

### 8.3 `index_context` Object (exact, built in `claude_session.py` lines 576-583)

```python
index_ctx = {
    "regime":      regime_result.get("regime"),
    "nifty_close": regime_result.get("nifty_close"),
    "vix":         regime_result.get("vix"),       # key "vix" — consistent with regime_result
    "ema20":       regime_result.get("ema20"),
    "ema50":       regime_result.get("ema50"),
    "ret20d_pct":  regime_result.get("ret20d"),
}
```

Example:
```json
{
  "regime":      "BULL_TRENDING",
  "nifty_close": 24350.5,
  "vix":         14.2,
  "ema20":       24100.0,
  "ema50":       23800.0,
  "ret20d_pct":  2.5
}
```

| Field | Intent |
|-------|--------|
| `regime` | Macro backdrop for scoring `market_context` component (10/100 pts) |
| `nifty_close` | Index reference level for relative stock strength |
| `vix` | Market-wide volatility when stock IV unavailable |
| `ema20`, `ema50` | Raw inputs so Claude can verify regime label independently |
| `ret20d_pct` | Momentum confirmation — positive = genuine trend, not dead-cat bounce |

### 8.4 `stock` Object — Complete Exact Structure (`build_stock_package`, lines 312-397)

```python
return {
    # ── Identity ────────────────────────────────────────────────────────────
    "symbol":               symbol,
    "sector":               sector,                  # from config/sector_map.json
    "sector_index":         sector_index,            # e.g. "NIFTY_BANK"
    "spot_price":           float(closes.iloc[-1]),  # latest price_history.close
    "lot_size":             lot_size,                # int or None (from lot_sizes table or Kite)
    "iv_data_available":    iv_available,            # bool: len(options_snapshots) > 0

    # ── 10-day centered option window ────────────────────────────────────────
    "centered_option_history_10d": centered_history, # list[{date, chain:[{s,t,p,oi},...]}]

    # ── Technical indicators (scalar, latest value) ──────────────────────────
    "ema20":       round(float(ema20_s.iloc[-1]), 2),
    "ema50":       round(float(ema50_s.iloc[-1]), 2),
    "ema200":      round(float(ema200_s.iloc[-1]), 2) if ema200_s is not None else None,
    "rsi14":       _last(rsi_s),
    "atr_pct14":   _last(atrp_s),
    "vol_ratio":   _last(volr_s),
    "macd":        _last(macd_l),
    "macd_signal": _last(macd_sig),
    "macd_hist":   _last(macd_hist),

    # ── Sector performance ───────────────────────────────────────────────────
    "sector_20d_return": sector_20d_ret,   # % return of sector_index over 20 days
    "nifty_20d_return":  nifty_20d_ret,    # % return of NIFTY_50 over 20 days
    "sector_vs_nifty":   sector_vs_nifty,  # sector_20d_return - nifty_20d_return
    "sector_status":     "TAILWIND" if (sector_vs_nifty or 0) > 0 else "HEADWIND",

    # ── Options / OI (scalar, latest) ────────────────────────────────────────
    "rollover_phase": rollover_phase,          # latest from continuous_oi_series
    "near_expiry":    near_expiry_str,         # "YYYY-MM-DD"
    "next_expiry":    next_expiry_str,         # "YYYY-MM-DD"
    "atm_iv_pct":     atm_iv,                  # ATM CE IV % or None
    "iv_assessment":  _iv_assessment(atm_iv),  # "LOW"/<15, "MEDIUM"/15-25, "HIGH">25, "UNKNOWN"
    "oi_walls":       oi_walls_data,           # {"ce_walls":[...], "pe_walls":[...]}
    "next_oi_walls":  next_oi_walls,           # {} unless near DTE < 5
    "near_month_oi":  latest_oi.get("near_month_oi"),
    "pcr_near":       latest_oi.get("pcr_near"),
    "max_pain":       latest_oi.get("max_pain"),
    "rollover_pct":   latest_oi.get("rollover_pct"),

    # ── Futures (scalar, latest) ─────────────────────────────────────────────
    "futures_price": latest_fut.get("futures_price"),
    "basis":         latest_fut.get("basis"),
    "basis_pct":     latest_fut.get("basis_pct"),

    # ── Time series ──────────────────────────────────────────────────────────
    "ohlcv_120d":          [...],   # 120 rows, see below
    "oi_series_30d":       [...],   # 30 rows, see below
    "futures_series_30d":  [...],   # 30 rows, see below
    "options_chain":       [...],   # all strikes for near expiry
    "next_options_chain":  [...],   # all strikes for next expiry (empty unless DTE < 5)
    "sector_index_20d":    [...],   # 20 rows
    "previous_setups":     [...],   # last 3 setups for this symbol
}
```

### 8.5 Time Series Exact Formats

**`ohlcv_120d`** — `df.tail(120)` with RSI column attached:
```json
[
  {"date": "2026-06-24", "open": 1645.0, "high": 1655.0, "low": 1643.0,
   "close": 1650.25, "volume": 5200000, "rsi": 58.4},
  "...119 more rows..."
]
```

**`oi_series_30d`** — `continuous_oi_series` 30 rows:
```json
[
  {"date": "2026-06-24", "near_oi": 13100000, "next_oi": 3200000, "oi_change": 350000,
   "pcr_near": 0.85, "max_pain": 1600, "rollover_pct": 12.5, "is_expiry_day": false},
  "...29 more rows..."
]
```

**`futures_series_30d`** — `futures_continuous_series` 30 rows:
```json
[
  {"date": "2026-06-24", "futures_price": 1653.4, "open": 1648.0, "high": 1658.0,
   "low": 1646.0, "volume": 120000, "near_oi": 13100000,
   "basis": 3.15, "basis_pct": 0.19, "rollover_pct": 12.5},
  "...29 more rows..."
]
```

**`options_chain`** — `options_snapshots` all strikes for near expiry:
```json
[
  {"strike": 1600, "type": "CE", "oi": 1200000, "iv": 18.5},
  {"strike": 1600, "type": "PE", "oi": 900000,  "iv": 17.8},
  {"strike": 1650, "type": "CE", "oi": 800000,  "iv": 19.1},
  {"strike": 1650, "type": "PE", "oi": 600000,  "iv": 17.2}
]
```

**`centered_option_history_10d`** — ±25 strikes from spot, 10 days. Abbreviated field names to reduce tokens:
```json
[
  {
    "date": "2026-06-24",
    "chain": [
      {"s": 1600, "t": "CE", "p": 52.5, "oi": 450000},
      {"s": 1600, "t": "PE", "p": 10.0, "oi": 200000},
      {"s": 1650, "t": "CE", "p": 28.0, "oi": 800000},
      {"s": 1650, "t": "PE", "p": 30.5, "oi": 600000}
    ]
  },
  "...9 more days..."
]
```
Field abbreviations: `s`=strike, `t`=option_type, `p`=premium_close, `oi`=oi

**`sector_index_20d`** — `price_history` for sector_index, last 20 rows:
```json
[
  {"date": "2026-06-24", "close": 52800.0},
  "...19 more rows..."
]
```

**`previous_setups`** — last 3 from `trade_setups` for this symbol:
```json
[
  {"setup_date": "2026-06-20", "direction": "LONG", "conviction_score": 72,
   "stage": "TRADE_READY", "paper_outcome": "TARGET_HIT", "setup_type": "EMA Breakout"},
  "...up to 2 more..."
]
```

### 8.6 `stock` Field-by-Field Intent

| Field | Source | Intent for Claude |
|-------|--------|-------------------|
| `symbol` | Symbol string | Used in Claude's response for identification |
| `sector` | `sector_map.json` | Sector label for context discussion |
| `sector_index` | `sector_map.json` | Index tracking this sector (NIFTY_BANK, NIFTY_IT, etc.) |
| `spot_price` | `price_history.close` latest | Reference for strike selection and entry zone calibration |
| `lot_size` | `lot_sizes` table or Kite fallback | If None -> lots/max_risk must be null (conditional instruction fires) |
| `iv_data_available` | `len(options) > 0` | If False -> IV instruction appended; Claude must not guess IV values |
| `centered_option_history_10d` | `options_snapshots` ±25 strikes, 10 days | Observe premium decay rate, OI migration across strikes, IV trend over time |
| `ema20` | EMA(20) on 250d history | Short-term trend. Price > EMA20 = bullish bias |
| `ema50` | EMA(50) on 250d history | Medium-term trend. EMA20 > EMA50 = confirmed uptrend |
| `ema200` | EMA(200) on 250d history or None | Long-term filter. Full alignment price>EMA20>EMA50>EMA200 = strongest long |
| `rsi14` | RSI(14) latest | 55-70 = good LONG entry. Above 70 = overbought. Below 30 = oversold |
| `atr_pct14` | ATR(14)/price | Volatility sizing proxy. Claude calibrates stop distance vs premium |
| `vol_ratio` | 3d avg / 20d avg volume | Volume confirmation. >1.5 on breakout candle = strong |
| `macd` | MACD line (12,26,9) | Positive + rising = bullish momentum |
| `macd_signal` | Signal line | MACD above signal = bullish crossover |
| `macd_hist` | Histogram | Positive and growing = accelerating momentum |
| `sector_20d_return` | 20d return of sector_index | Sector momentum |
| `nifty_20d_return` | 20d return of NIFTY_50 | Benchmark for relative performance |
| `sector_vs_nifty` | `sector_20d_return - nifty_20d_return` | Positive = outperformance = TAILWIND for direction |
| `sector_status` | `"TAILWIND"` or `"HEADWIND"` | Pre-computed label. Part of `index_fo_context` score (25/100) |
| `rollover_phase` | `continuous_oi_series.rollover_phase` | How to interpret OI: EXPIRY = ignore near OI. TRANSITION = recommend next expiry |
| `near_expiry` | From OI series | Option expiry date. Must have >= 6 trading days DTE |
| `next_expiry` | From OI series | Fallback expiry if near DTE < 5 |
| `atm_iv_pct` | ATM CE IV from `options_snapshots` | Raw IV%. Drives `iv_assessment` label |
| `iv_assessment` | `_iv_assessment(atm_iv)` | LOW/MEDIUM/HIGH/UNKNOWN. Part of `stock_fo` score (10/100) |
| `oi_walls.ce_walls` | Top 5 CE OI strikes, near expiry | Resistance levels. Claude anchors T2 below strongest CE wall |
| `oi_walls.pe_walls` | Top 5 PE OI strikes, near expiry | Support levels. Claude anchors stop above strongest PE wall |
| `next_oi_walls` | Top 5 OI walls for next expiry | Only populated when near DTE < 5; allows next-expiry rollover recommendations |
| `near_month_oi` | Latest near_month_oi | Absolute positioning size. High = strong stock interest |
| `pcr_near` | Latest pcr_near | Contrarian: >1.3 = net put writing = floor = bullish. <0.7 = overbought = bearish |
| `max_pain` | Latest max_pain | Magnetic strike. Stock gravitates near expiry. Claude notes proximity |
| `rollover_pct` | Latest rollover_pct | High early rollover = strong directional conviction |
| `futures_price` | Latest futures_price | Near-month futures level |
| `basis` | `futures_price - spot_price` | Rs premium |
| `basis_pct` | `basis / spot * 100` | Positive = bullish futures premium. Extreme = strong bet |
| `ohlcv_120d` | `price_history` 120 rows + RSI | Primary pattern/trend/support/resistance data |
| `oi_series_30d` | `continuous_oi_series` 30 rows | OI buildup trend, PCR trend, max pain migration |
| `futures_series_30d` | `futures_continuous_series` 30 rows | Futures volume (liquidity), basis trend |
| `options_chain` | `options_snapshots` near expiry all strikes | Strike selection, IV skew, market positioning |
| `next_options_chain` | `options_snapshots` next expiry (if DTE<5) | When to recommend next expiry |
| `sector_index_20d` | `price_history` for sector_index 20 rows | Independent verification of sector_vs_nifty claim |
| `previous_setups` | `trade_setups` last 3 for symbol | Prevents repeating recently failed pattern. Same pattern + TARGET_HIT = higher confidence |

### 8.7 Deep Analysis Instructions (exact from `build_deep_prompt`, lines 428-468)

```python
instructions = (
    "Perform a full deep analysis of this stock using the Section 9 "
    "conviction scoring framework. Factor in the daily Spot RSI series and "
    "Futures OHLCV data for momentum and liquidity context.\n"
    "Respond with ONLY a JSON object:\n"
    "{\n"
    '  "stage": "TRADE_READY | WATCH | ON_RADAR | SKIP",\n'
    '  "direction": "LONG | SHORT",\n'
    '  "conviction_score": 0-100,\n'
    '  "setup_type": "string",\n'
    '  "setup_maturity": "EARLY | DEVELOPING | READY",\n'
    '  "entry_zone_low": number,\n'          # underlying price level
    '  "entry_zone_high": number,\n'         # underlying price level
    '  "underlying_stop": number,\n'
    '  "underlying_target_1": number,\n'
    '  "underlying_target_2": number,\n'
    '  "option_type": "CE | PE",\n'
    '  "strike": number,\n'
    '  "expiry_date": "YYYY-MM-DD",\n'
    '  "entry_premium_low": number,\n'       # option premium low end
    '  "entry_premium_high": number,\n'      # option premium high end
    '  "stop_loss_premium": number,\n'
    '  "target_1_premium": number,\n'
    '  "target_2_premium": number,\n'
    '  "rr_reasoning": "Justify your stop loss, targets, and R:R ratio based on price structure and option premiums",\n'
    '  "lots": integer,\n'
    '  "lot_size": integer,\n'
    '  "max_risk_inr": number,\n'
    '  "risk_reward": number,\n'
    '  "iv_assessment": "LOW | MEDIUM | HIGH | UNKNOWN",\n'
    '  "scoring_breakdown": {\n'
    '    "price_structure": 0-30,\n'
    '    "momentum_volume": 0-25,\n'
    '    "index_fo_context": 0-25,\n'
    '    "stock_fo": 0-10,\n'
    '    "market_context": 0-10\n'
    '  },\n'
    '  "signals_contributing": ["list of key signals"],\n'
    '  "claude_full_rationale": "full paragraph rationale",\n'
    '  "mentor_explanation": "explanation for learning",\n'
    '  "why_could_be_wrong": "key bear case / risk",\n'
    '  "skip_reason": null\n'
    "}"
)
```

### 8.8 Conditional Instruction Additions (exact code, lines 403-423)

```python
# Direction lock
direction_instruction = (
    f"\n\nIMPORTANT: Analyse for {direction} setup ONLY. "
    "If no valid setup exists in that direction, return stage=SKIP."
    if direction not in ("AUTO", None, "")
    else ""
)

# Unknown lot size
lot_size_instruction = (
    "\n\nIMPORTANT: lot_size is unknown for this symbol. "
    "Set lots=null, lot_size=null, max_risk_inr=null in your response. "
    "Still provide all other fields."
    if stock_pkg.get("lot_size") is None
    else ""
)

# No IV data
iv_instruction = ""
if not stock_pkg.get("iv_data_available"):
    iv_instruction = (
        "\n\nIMPORTANT: IV data unavailable — NSE blocked. "
        "Do NOT guess or use stale IV. Use VIX as market-wide vol context. "
        "Assess option premium relative to its own 10-day history instead. "
        "Flag expensive/cheap qualitatively in your rationale."
    )

# Watchlist re-analysis (added in claude_session.py, lines 614-625)
custom_instructions = (
    f"\n\nCONTEXT: This stock has been on Watch for {days_in} days. "
    f"Previous conviction: {prev_score}. Previous setup: {prev_type}. "
    "Re-evaluate with today's data. Has the setup confirmed or broken down?"
    if is_re else ""
)
```

### 8.9 Expected Response Schema (annotated)

```json
{
  "stage":           "TRADE_READY",   // TRADE_READY / WATCH / ON_RADAR / SKIP
  "direction":       "LONG",          // LONG / SHORT
  "conviction_score": 72,             // 0-100. TRADE_READY >= 70. WATCH >= 55. Below 55 = DEGRADED
  "setup_type":      "EMA Pullback to 20",
  "setup_maturity":  "READY",         // EARLY / DEVELOPING / READY

  // Underlying price levels (NOT option premiums)
  "entry_zone_low":      1640.0,
  "entry_zone_high":     1655.0,
  "underlying_stop":     1595.0,
  "underlying_target_1": 1710.0,
  "underlying_target_2": 1760.0,

  // Option contract
  "option_type":    "CE",
  "strike":         1650,
  "expiry_date":    "2026-07-29",     // must have >= 6 trading days DTE

  // Option premium levels (THESE are stored as entry_zone_low/high in DB — Issue 2)
  "entry_premium_low":  48.0,
  "entry_premium_high": 55.0,
  "stop_loss_premium":  28.0,
  "target_1_premium":   80.0,
  "target_2_premium":  115.0,
  "rr_reasoning": "Stop at 28 (below key support). T2 at 115 = 2.84:1 on entry mid 51.5.",

  // Position sizing — ALL THREE overwritten by validate_position_sizing()
  "lots":         2,
  "lot_size":     550,
  "max_risk_inr": 11000,
  "risk_reward":  2.84,

  "iv_assessment": "MEDIUM",

  "scoring_breakdown": {
    "price_structure":  22,   // max 30
    "momentum_volume":  18,   // max 25
    "index_fo_context": 17,   // max 25
    "stock_fo":          8,   // max 10
    "market_context":    7    // max 10
  },

  "signals_contributing": [
    "EMA20 bounce with volume 1.8x average",
    "RSI crossed 55 from below",
    "OI building in 1650 CE strikes",
    "Basis positive +0.19%",
    "Banking sector outperforming Nifty +0.7%"
  ],

  "claude_full_rationale": "HDFCBANK pulled back cleanly to EMA20 after breakout...",
  "mentor_explanation":    "Textbook EMA20 pullback in uptrend. The key to this setup is...",
  "why_could_be_wrong":    "If Nifty breaks 24000 or FII selling accelerates...",
  "skip_reason": null    // populated if stage=SKIP
}
```

### 8.10 Python Position Sizing Override (exact, lines 530-583)

```python
def validate_position_sizing(analysis: dict, config: dict) -> dict:
    capital  = float(config.get("capital_inr", 500_000))  # Rs 5,00,000
    max_risk = capital * 0.025                              # 2.5% = Rs 12,500

    lot_size   = analysis.get("lot_size")
    entry_low  = analysis.get("entry_premium_low")
    entry_high = analysis.get("entry_premium_high")
    stop_loss  = analysis.get("stop_loss_premium")
    target_2   = analysis.get("target_2_premium")

    # Skip if any field missing
    if not all(x is not None for x in [lot_size, entry_low, entry_high, stop_loss, target_2]):
        return analysis

    entry_mid    = (float(entry_low) + float(entry_high)) / 2.0
    risk_per_lot = (entry_mid - float(stop_loss)) * int(lot_size)

    # Reject if risk <= 0
    if risk_per_lot <= 0:
        analysis.update({"lots": 0, "max_risk_inr": 0.0, "risk_reward": 0.0})
        return analysis

    # Hard reject: single lot risk > 3% of capital (Rs 15,000)
    if risk_per_lot > capital * 0.03:
        analysis.update({"lots": 0, "max_risk_inr": round(risk_per_lot, 0)})
        return analysis

    # Optimal lots: floor(max_risk / risk_per_lot), capped at MAX_LOTS=5
    lots        = max(1, min(int(max_risk / risk_per_lot), 5))
    actual_risk = risk_per_lot * lots
    actual_rr   = (float(target_2) - entry_mid) / (entry_mid - float(stop_loss))

    # RR gate: < 2.0 -> SKIP
    if actual_rr < 2.0:
        analysis["rr_gate_passed"] = False
        analysis["stage"]       = "SKIP"
        analysis["skip_reason"] = f"RR {actual_rr:.2f} below 2.0 minimum"
        return analysis

    # Overwrite Claude's values
    analysis["lots"]         = lots
    analysis["max_risk_inr"] = round(actual_risk, 0)
    analysis["risk_reward"]  = round(actual_rr, 2)
    return analysis
```

### 8.11 DB Storage Mapping (exact, `claude_session.py` lines 733-763)

```python
create_trade_setup({
    "session_id":            session_id,
    "setup_date":            str(session_date),
    "symbol":                symbol,                              # injected by Python
    "direction":             analysis.get("direction"),
    "stage":                 stage,                               # may be WATCH from sector downgrade
    "setup_type":            analysis.get("setup_type"),
    "setup_maturity":        analysis.get("setup_maturity"),
    "conviction_score":      analysis.get("conviction_score"),
    "strike":                analysis.get("strike"),
    "option_type":           analysis.get("option_type"),
    "expiry_date":           analysis.get("expiry_date"),
    "entry_zone_low":        analysis.get("entry_premium_low"),  # OPTION PREMIUM -> "zone" column
    "entry_zone_high":       analysis.get("entry_premium_high"), # OPTION PREMIUM -> "zone" column
    "stop_loss_premium":     analysis.get("stop_loss_premium"),
    "target_1_premium":      analysis.get("target_1_premium"),
    "target_2_premium":      analysis.get("target_2_premium"),
    "underlying_stop":       analysis.get("underlying_stop"),
    "lots":                  analysis.get("lots"),               # Python-validated
    "lot_size":              analysis.get("lot_size"),
    "max_risk_inr":          analysis.get("max_risk_inr"),       # Python-validated
    "risk_reward":           analysis.get("risk_reward"),        # Python-validated
    "iv_assessment":         analysis.get("iv_assessment"),
    "scoring_breakdown":     analysis.get("scoring_breakdown", {}),
    "signals_contributing":  analysis.get("signals_contributing", []),
    "claude_full_rationale": analysis.get("claude_full_rationale"),
    "mentor_explanation":    analysis.get("mentor_explanation"),
    "key_learning_today":    analysis.get("key_learning_today"),  # not in schema -> usually None
    "why_could_be_wrong":    analysis.get("why_could_be_wrong"),
})
```

### 8.12 Conviction Scoring Weights

| Component | Max | What Claude evaluates |
|-----------|-----|----------------------|
| `price_structure` | 30 | EMA alignment (price vs EMA20/50/200), pattern quality, trend continuity, breakout/pullback quality |
| `momentum_volume` | 25 | RSI level+direction, MACD crossover+histogram, volume ratio on key candles, momentum acceleration |
| `index_fo_context` | 25 | Regime backdrop, sector vs Nifty (TAILWIND/HEADWIND), FII flow direction, OI wall alignment |
| `stock_fo` | 10 | IV expensiveness, PCR contrarian reading, OI buildup trend, max pain proximity |
| `market_context` | 10 | Tonight's regime quality score, VIX level |
| **Total** | **100** | TRADE_READY: 70+. WATCH: 55-74. Below 55: DEGRADED |

---

## 9. Chat Endpoint — /api/chat (Exact Proto)

**File:** `api/dashboard.py:chat(body: ChatRequest)` lines 614-766

### Request Model

```python
class ChatRequest(BaseModel):
    messages:   list[dict]           # [{"role": "user"|"assistant", "content": str}]
    session_id: str | None = None    # optional — defaults to latest ANALYSIS_COMPLETE session
```

### Message Sanitisation and Cap

```python
# Cap at 20 exchanges (40 messages) — keep most recent
if len(messages) > 40:
    messages = messages[-40:]

# Sanitise: only allow role/content keys, block anything else
clean_messages = [
    {"role": m["role"], "content": str(m["content"])}
    for m in messages
    if m.get("role") in ("user", "assistant")
]
```

### System Prompt for Chat

Built by `_format_chat_context(session, turn1_data, turn2_data, setups, fii_row)` — exact same function as `GET /api/session/today/chat-context`.

The system prompt is a plain-text document with these sections:

```
═══════════════════════════════════════════════════
SWING TRADING ANALYSIS — {date}
═══════════════════════════════════════════════════

INSTRUCTIONS FOR CLAUDE:
You are the AI analyst who performed tonight's analysis for Indian F&O markets.
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MARKET CONTEXT TONIGHT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Date        : {date}
Regime      : {market_regime}
Nifty Close : {nifty_close}
VIX         : {vix_close}
FII Flow    : {fii_net_cr} Cr
DII Flow    : {dii_net_cr} Cr

Your Market Narrative:
{session_narrative from Turn 1}

Risk Flags:
  • {risk_flag_1}
  • {risk_flag_2}

Favourable Setups : {favourable_setups}
Key Support       : {support}
Key Resistance    : {resistance}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TONIGHT'S RECOMMENDATIONS ({N} setups)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

─── {SYMBOL} | {LONG/SHORT} | {STAGE} ───────────
Conviction : {conviction_score}/100
Setup      : {setup_type} ({setup_maturity})
Instrument : {option_type} {strike} expiry {expiry_date} ({DTE} trading days)
IV Context : {iv_assessment}
Rollover   : {rollover_phase} at time of analysis

Entry Zone : Rs {entry_zone_low} to Rs {entry_zone_high}
Stop Loss  : Rs {stop_loss_premium}
Target 1   : Rs {target_1_premium} (50% exit)
Target 2   : Rs {target_2_premium} (full exit)
Underlying SL : Rs {underlying_stop}

Position   : {lots} lots x {lot_size}
Max Risk   : Rs {max_risk_inr} ({risk_pct_capital}% of capital)
R:R Ratio  : 1:{risk_reward}

Scoring:
  Price Structure    {price_structure}/30
  Momentum/Volume    {momentum_volume}/25
  Index F&O Context  {index_fo_context}/25
  Stock F&O          {stock_fo}/10
  Market Context     {market_context}/10
  TOTAL              {conviction_score}/100

Signals That Contributed:
  • {signal_1}
  • {signal_2}

Your Full Analysis:
{claude_full_rationale}

Mentor Explanation:
{mentor_explanation}

Why This Could Be Wrong:
{why_could_be_wrong}

Key Learning:
{key_learning_today}

Paper Trade Status: {paper_outcome | "Monitoring"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRE-SCAN SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stocks Level 1 passed : {stocks_level1_passed}
Forwarded for deep    : {len(forwarded)}
Deep analysed         : {len(setups)}

Forwarded stocks:
  {SYMBOL} - {direction} - {priority}

Notable skips:
  {SYMBOL}: {pre_scan_reasoning (truncated 120 chars)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSION INFO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Session ID   : {session_id}
Session Date : {date}
Analysis Cost: ${claude_cost_usd}
═══════════════════════════════════════════════════
```

### Chat API Call (exact, lines 727-736)

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[{
        "type":          "text",
        "text":          system_prompt,          # _format_chat_context() output
        "cache_control": {"type": "ephemeral"},  # cached across exchanges in same session
    }],
    messages=clean_messages,                     # full sanitised conversation history
)
```

**Retries:** 3 attempts with `_CHAT_BACKOFF = [5, 10, 20]` seconds on `RateLimitError`, `APIStatusError >= 500`, `APIConnectionError`.

### Chat Response

```python
{
    "reply":         response.content[0].text,
    "input_tokens":  response.usage.input_tokens,
    "output_tokens": response.usage.output_tokens,
    "cost_usd":      round(input_tokens / 1_000_000 * 3.0 + output_tokens / 1_000_000 * 15.0, 6),
    "session_id":    session_id,
}
```

---

## 10. Variable Name Audit

### Issue 1 — `sym` vs `symbol` in Turn 2 Payload — SEVERITY: HIGH

**Location:** `pipeline/claude_session.py:_stock_data()` line 234

**Problem:**
```python
return {
    "sym": symbol,    # <-- inconsistent with everything else
    ...
}
```
Turn 2 input uses `"sym"`. Turn 2 response schema specifies `"symbol"`. Deep analysis payload uses `"symbol"`. DB columns use `symbol`. This naming inconsistency exists within the same API request.

**Fix:** Change `"sym": symbol` to `"symbol": symbol` in `_stock_data()`. One-line change.

---

### Issue 2 — `entry_zone_low/high` Stores Option Premiums — SEVERITY: HIGH

**Location:** `pipeline/claude_session.py` lines 745-746; `trade_setups` DB table

**Problem:** DB columns `entry_zone_low/high` store option premium values from `entry_premium_low/high`:
```python
"entry_zone_low":  analysis.get("entry_premium_low"),   # option premium Rs, not underlying price
"entry_zone_high": analysis.get("entry_premium_high"),  # option premium Rs, not underlying price
```
The column names suggest underlying price levels. Both underlying price fields (`entry_zone_low/high`) and option premium fields (`entry_premium_low/high`) exist in Claude's response schema — making it confusing which is stored where.

**Fix:** Rename DB columns to `entry_premium_low/high` (migration + all query references), OR rename Claude's underlying entry fields to `underlying_entry_low/high` to make both sets unambiguous.

---

### Issue 3 — `key_learning_today` Not in Prompt Schema — SEVERITY: MEDIUM

**Location:** `pipeline/claude_session.py` line 761 stores it; `api/dashboard.py` line 461 displays it

**Problem:** `create_trade_setup` stores `analysis.get("key_learning_today")` and the chat formatter displays "Key Learning:". But `build_deep_prompt()` does not include `key_learning_today` in the response schema. Claude never produces it. Always null.

**Fix:** Add to deep analysis response schema:
```json
"key_learning_today": "one concrete lesson from this setup — what makes it textbook vs forced"
```

---

### Issue 4 — `risk_pct_capital` Never Populated — SEVERITY: LOW

**Location:** `api/dashboard.py` line 422

**Problem:**
```python
f"Max Risk   : Rs {_fmt_val(s.get('max_risk_inr'))} ({_fmt_val(s.get('risk_pct_capital'))}% of capital)"
```
`risk_pct_capital` is never computed or stored. Always renders as "Not available% of capital".

**Fix:** Compute in formatter or at storage time:
```python
risk_pct_capital = round((s.get("max_risk_inr") or 0) / 500000 * 100, 2)
```

---

### Issue 5 — `vix` vs `vix_close` Naming Split — SEVERITY: LOW

**Problem:** Four different naming contexts for the same value:

| Location | Key name |
|----------|----------|
| `regime_result` dict (from `run_market_regime`) | `"vix"` |
| Turn 1 payload field | `"vix_close"` (line 175: `regime_result.get("vix")`) |
| `index_ctx` dict for deep analysis | `"vix"` |
| `analysis_sessions` DB column | `vix_close` |

**Fix:** Standardise to `"vix_close"` everywhere. Rename `regime_result["vix"]` to `regime_result["vix_close"]` and update all four callsites.

---

### Issue 6 — `oi_10d` Field Name Ambiguity — SEVERITY: LOW

**Location:** `pipeline/claude_session.py:_stock_data()` line 222-223

**Problem:**
```python
oi_rows = get_continuous_oi(symbol, days=10)
oi_10d  = [r.get("near_month_oi") for r in oi_rows]
```
Field named `oi_10d` — doesn't specify near-month vs total, units are contracts not lots. Inconsistent with deep analysis `oi_series_30d` which has explicit `near_oi` sub-field.

**Fix:** Rename to `near_month_oi_10d` to align with `oi_series_30d.near_oi` naming pattern.

---

### Issue 7 — Deep Analysis Lacks Turn 1 Session Narrative — SEVERITY: Design Note

Deep analysis turns are stateless. They receive regime/vix in `index_context` but NOT Turn 1's `session_narrative` or `risk_flags`. Claude cannot reason: "given tonight's bearish narrative, I raise the bar for LONGs."

**Recommendation:** Pass Turn 1 output into `index_context`:
```python
index_ctx["session_narrative"] = turn1_result.get("session_narrative")
index_ctx["risk_flags"]        = turn1_result.get("risk_flags", [])
index_ctx["favourable_setups"] = turn1_result.get("favourable_setups")
```

---

### Issue 8 — Pre-scan Gets Single `rollover_phase` vs Full Series in Deep — SEVERITY: Design Note

Pre-scan receives only the latest `rollover_phase` label (single value). Deep analysis receives the full 30-day OI series with `rollover_pct` history.

This is intentional — pre-scan is deliberately lightweight to minimise tokens. No fix needed; documented for context when designing a "medium depth" scan tier.

---

## 11. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Turns 1-2 stateful; Turns 3+ stateless | Turns 1-2 need to build on each other (macro -> triage). Deep analysis per stock is independent. Isolation keeps per-turn token cost predictable and prevents context blowout. |
| `cache_control: ephemeral` on all system prompts | System prompt identical within and across turns/sessions. Saves 50-70% of input token cost on Turn 2+ via Anthropic prompt cache. |
| Python overwrites Claude's position sizing | Claude's lot calculations are inconsistent. Python enforces exact risk formula, single-lot 3% hard reject, 5-lot cap, RR >= 2.0 gate. Claude's `lots`, `max_risk_inr`, `risk_reward` always overwritten. |
| Sector correlation enforced in Python | Deep analysis is stateless — Claude cannot recall which sectors it approved. Python's `trade_ready_list` tracks in memory and downgrades same-sector+same-direction conflicts to WATCH. |
| `DEEP_SYSTEM` is a constant | Does not vary by session, stock, or regime. Identical across all deep turns -> maximum cache hit rate. |
| Turn 1 max_tokens=1500, Turn 2=12000, Turn 3+=3000 | Turn 1 is a short assessment. Turn 2 is wide (40 stocks x ~300 tokens). Turn 3+ needs room for full rationale + scoring breakdown + option parameters. |
| Deep analysis queue: watchlist stocks first | Graduation from WATCH to TRADE_READY has higher urgency than new discoveries. |
| Paper trade SL uses underlying OHLC; targets use option premium EOD | Intraday underlying low/high gives more accurate SL detection. EOD option premium for targets is conservative and avoids intraday spike fills. |
| FII/DII fallback to previous day with `source=CACHED` | NSE API sometimes unavailable post-market. Pipeline not blocked; `source` field enables dashboard staleness flag. |

---

## 12. Database Tables

| Table | Primary Key | Description |
|-------|-------------|-------------|
| `analysis_sessions` | `session_id` string | One per pipeline run. Fields: `status`, token counts, `claude_cost_usd`, `market_regime`, setup counts, `stage_statuses` JSON, `prompt_versions` JSON |
| `session_claude_turns` | `id` UUID | Every Claude API call. Fields: `session_id`, `turn_number`, `turn_type` (market_context/prescan/deep_analysis), `symbol`, `input_tokens`, `output_tokens`, `input_text`, `output_text`, `completed_at` |
| `trade_setups` | `id` UUID | One per actionable setup. All option parameters, scoring, rationale, paper trade state. `entry_zone_low/high` stores option premiums |
| `price_history` | `(symbol, date)` | OHLCV. Index symbols: `NIFTY_50`, `INDIA_VIX`, `NIFTY_BANK`, `NIFTY_IT`, etc. Kite authoritative over bhavcopy on conflict |
| `fii_dii_flows` | `date` | Daily FII/DII net flows in Crores. `source`: `LIVE` or `CACHED` |
| `options_snapshots` | `(symbol, snapshot_date, expiry_date, strike, option_type)` | `oi`, `oi_change`, `volume`, `implied_volatility`, `premium_close` |
| `continuous_oi_series` | `(symbol, date)` | `near_month_oi`, `next_month_oi`, `oi_change`, `pcr_near`, `max_pain`, `rollover_pct`, `rollover_phase`, `near_expiry`, `next_expiry`, `is_expiry_day` |
| `futures_continuous_series` | `(symbol, date)` | Futures OHLCV + OI + basis per symbol per day |
| `watchlist_staging` | `symbol` | Stages: `WATCH`, `ON_RADAR`, `TRADE_READY`, `ENTRY_TRIGGERED`, `FLAGGED`, `DEGRADED`, `EXPIRED`, `MANUAL_ADD`. Fields: `current_stage`, `direction_bias`, `days_in_stage`, `first_flagged_date` |
| `kite_tokens` | `user_id = "primary"` | Single-row Zerodha access token. Expires daily |
| `system_config` | `key` | `capital_inr`, `claude_monthly_budget_usd`, `max_concurrent_trades`, `usd_to_inr_rate`, `manual_analysis_enabled`, `dashboard_url` |
| `lot_sizes` | `symbol` | NSE F&O lot sizes from Kite instruments master |
| `shadow_track` | `id` UUID | Level 1 eliminated stocks. `symbol`, `elimination_date`, `reason`, `price`, `atr_pct`, `track_until_date` |

---

## 13. API Endpoints

### System Routes (`main.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | DB status, version, environment |
| GET | `/kite/refresh` | Start Zerodha OAuth -> redirects to login |
| GET | `/kite/callback` | Handle OAuth callback, store `access_token` in DB |

### Dashboard Routes (`api/dashboard.py`, prefix `/api`)

| Method | Path | Claude max_tokens | Description |
|--------|------|------------------|-------------|
| GET | `/api/today` | — | Latest session: market context + TRADE_READY + WATCH setups sorted conviction DESC. `stale=true` if >24h |
| GET | `/api/setup/{id}` | — | Full setup detail: scoring, signals, rationale, paper trade status |
| GET | `/api/deep-analysis` | — | All deep analysis turns from latest session including SKIPs. Deep Analysis tab |
| GET | `/api/positions` | — | Open paper trades with estimated P&L from latest `price_history.close` |
| GET | `/api/watchlist` | — | `watchlist_staging` rows ordered by `days_in_stage` DESC |
| POST | `/api/watchlist` | — | Add symbol with stage `MANUAL_ADD` |
| GET | `/api/session/today/chat-context` | — | Plain-text analysis context for Claude.ai paste. Headers: `X-Session-Date`, `X-Session-Id`, `X-Generated-At` |
| POST | `/api/chat` | 1024 | In-widget chat. Stateless. 40-message cap. 3 retries with backoff. |
| GET | `/api/system/status` | — | DB, Kite token, scheduler jobs, monthly cost vs budget, per-turn cost breakdown |

### Manual Analysis (`api/manual_analysis.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/analyse` | On-demand single-stock deep analysis. Gate: `system_config.manual_analysis_enabled = "true"`. Bypasses Level 1 filter |

---

*End of CLAUDE_audit_v8.md — Design Specification v8, 24 June 2026*
