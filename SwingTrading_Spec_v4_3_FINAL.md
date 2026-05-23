# Automated Post-Market Swing Trading Analysis System
## Complete System Specification — v4.3 FINAL
**Date:** May 2026 | **Status:** FINAL — Ready For Implementation

---

## QUICK REFERENCE

| Parameter | Value |
|---|---|
| Stock Universe | Nifty 50 — stocks only |
| Trading Style | Swing 2-5 days, Long & Short |
| Instruments | Stock options only (no index options) |
| Capital | ₹5,00,000 |
| Risk Per Trade | 2-3% (₹10,000–₹15,000) |
| Min Risk-Reward | 1:2 hard gate |
| Max Concurrent Trades | 2-3 |
| Options Expiry | Monthly — last Tuesday of month |
| Expiry Rule | ≤5 trading days remaining → use next month |
| Pipeline Start | 10:00 PM IST (trading days only) |
| Morning Brief | 7:00 AM IST via Telegram |
| Dashboard | https://trading.rankachieversclasses.in |
| Backend API | https://api.rankachieversclasses.in |
| Monthly Cost | ₹1,900–₹3,000 (hard ceiling ₹3,500) |

---

## IMPLEMENTATION FILES (Validated 2026-05-23)

**Location:** C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer\other_impls

```
impl_01_nse_equity_bhavcopy.py   — NSE equity CSV download
impl_02_nse_indices_bhavcopy.py  — Sector indices + India VIX
impl_03_nse_fii_dii.py           — FII/DII with session handling
impl_04_kite_historical_ohlcv.py — Daily OHLCV equity + F&O
impl_05_kite_historical_oi.py    — Historical OI futures + options
impl_06_nse_option_chain_iv.py   — Live IV snapshot 3:25 PM
impl_07_telegram_notify.py       — HTML formatted alerts
impl_08_kite_oauth.py            — Daily token refresh flow
```

These 8 files form the data ingestion and notification layer.
All confirmed working. Production code refactors into pipeline modules.

---

## PENDING VALIDATION (Non-Blocking For Build Start)

```
T8: Supabase free tier pausing policy
    Manual check: supabase.com → project settings → general
    Keepalive strategy already designed (6 AM daily ping)

T9: Cloudflare Tunnel Windows service setup
    Separate session required for installation
    Design fully confirmed — setup only remains
```

---

## PHASE 1 SCOPE (Weeks 1-8)

### IN SCOPE
```
Data Layer:
  NSE Bhavcopy download (equity + indices)
  NSE FII/DII fetch with session handling
  NSE Option chain snapshot (3:25 PM)
  Kite OHLCV + futures OI + portfolio data
  Kite OAuth token flow
  All data stored in Supabase

Analysis Pipeline:
  Level 1 filter (earnings, ATR, liquidity)
  OI continuous series builder
  Claude multi-turn session
  Conviction scoring + trade output

Output:
  Telegram notifications (all daily touchpoints)
  Basic dashboard (Today + Setup detail screens)
  Real P&L tracking from Kite

Ledger:
  Trade setup storage
  Paper trade engine (actual option LTP values)
  Outcome reconciliation
```

### DEFERRED TO PHASE 2
```
Config Console        — Use Supabase admin panel in Phase 1
Learning engine       — Needs data to learn from
Signal attribution    — Phase 2
Weekly debrief        — Phase 2
Full dashboard        — Performance, History, Debrief screens
Human-in-loop buttons — Phase 2
Monthly calibration   — Phase 2
Shadow tracking       — Phase 2
Filter evolution      — Phase 2
```

---

# SECTION 1: VISION & NORTH STAR

## Primary Goal
1. Identify high-conviction F&O swing trade setups on Nifty 50 nightly
2. Deliver actionable recommendations before market open via Telegram
3. Track real P&L from Kite and simulate paper trades for accuracy measurement
4. Act as a trading mentor — transferring analytical intelligence to the user
5. Self-calibrate based on outcome evidence over time

## Ultimate Success Metric
> The system succeeds when the user can look at a chart, read the data,
> and reach the same conclusion as Claude in minutes — independently.

## Design Tenets
1. High conviction over high volume — 2-3 strong setups beats 10 weak ones
2. Claude brain over static rules — contextual reasoning not mechanical computation
3. Frugal infrastructure — minimise cost without compromising quality
4. Evolving intelligence — system learns from outcome evidence
5. Human in the loop — user feedback shapes intelligence
6. Minimum external dependencies — fewer = fewer failure points
7. Resilience first — graceful degradation when dependencies fail

---

# SECTION 2: TRADING PARAMETERS

## Style & Instruments
| Parameter | Value |
|---|---|
| Trading Style | Swing Trading |
| Holding Period | 2–5 trading days |
| Direction | Long AND Short — fully direction-agnostic |
| Instruments | Stock options ONLY |
| Stock Universe | Nifty 50 |
| Index | NIFTY 50 + Bank Nifty as macro context only |

## Capital & Risk Rules
| Parameter | Value |
|---|---|
| Total Capital | ₹5,00,000 |
| Risk Per Trade | 2–3% = ₹10,000–₹15,000 |
| Max Concurrent Trades | 2-3 |
| Max Capital At Risk | ₹30,000–₹45,000 (6–9%) |
| Min Risk-Reward | 1:2 hard gate |
| Ideal Risk-Reward | 1:3 |

## Options Expiry Rules
```
Expiry Type     : Monthly — last Tuesday of each month
Stock F&O       : 3 active expiries at any time
                  (e.g. today: MAY, JUNE, JULY)
Expiry Selection: ≤5 trading days to expiry → use next month
Minimum DTE     : 6 trading days

GRADUATED TRANSITION SCHEDULE:
  T-6+         : NORMAL — near month primary
  T-5 to T-3   : ROLLOVER_WATCH — near month primary,
                  next month shown alongside
  T-2 (Monday) : TRANSITION — next month primary,
                  next month expiry for new trades
  T-1 (Tuesday): EXPIRY — next month sole reference
```

## Trade Output Format
```
Stock       : HDFCBANK
Instrument  : HDFCBANK 1480 CE
Direction   : LONG
Setup Type  : Flag Breakout (Early Stage)
Conviction  : 82/100
Stage       : TRADE_READY

Entry Zone  : ₹50–54
Stop Loss   : ₹38 (underlying: ₹1,445)
Target 1    : ₹78 (50% exit — move SL to entry)
Target 2    : ₹95 (full exit)
Expiry      : 27 May 2026 (7 trading days) ✅
IV          : 18% — Low, favourable for buying

Lots        : 2 | Lot Size: 550
Max Risk    : ₹11,000 (2.2% of capital) ✅
Max Gain    : ₹26,400
Risk-Reward : 1:2.4 ✅
```

---

# SECTION 3: DATA ARCHITECTURE

## Complete Validated Data Source Map

### NSE Archives (Open CDN — No Auth Required)
```
BASE URL    : https://nsearchives.nseindia.com
Auth        : None — open CDN
Headers     : Accept-Encoding: gzip, deflate (NOT br — garbled bytes)
Session     : Not required
Rate limit  : None observed — CDN served
Always call : df.columns.str.strip() after every CSV read
```

#### Equity Bhavcopy
```
URL         : /content/cm/sec_bhavdata_full_{DDMMYYYY}.csv
Format      : Plain CSV (NOT zipped)
Date format : DDMMYYYY (e.g. 22052026)
Available   : ~6:00 PM IST

Confirmed fields:
  SYMBOL      — Stock symbol
  SERIES      — Filter: keep EQ series only
  OPEN, HIGH, LOW, CLOSE — OHLCV
  TOTTRDQTY   — Volume (shares)
  TOTTRDVAL   — Total traded value
                RAW RUPEES — divide by 10,000,000 for Crores
  TOTALTRADES — Number of trades
  DELIV_QTY   — Delivery quantity
  DELIV_PER   — Delivery % (already percentage — do NOT divide)
```

#### Indices Bhavcopy
```
URL         : /content/indices/ind_close_all_{DDMMYYYY}.csv
Format      : Plain CSV (~15KB)
Available   : ~6:00 PM IST

Confirmed fields:
  "India VIX" — EXACT name, case-sensitive, one space
                Must strip column names after read
                Use CLOSING VALUE ONLY
                Open/High/Low for VIX = unreliable — NEVER USE

Sector indices present: NIFTY BANK, NIFTY IT, NIFTY AUTO,
  NIFTY PHARMA, NIFTY FMCG, NIFTY METAL, NIFTY ENERGY,
  NIFTY FIN SERVICE — all confirmed present
```

### NSE Website (Session Required)
```
BASE URL    : https://www.nseindia.com
Auth        : Akamai session cookies

MANDATORY WARMUP SEQUENCE:
  Step 1: GET https://www.nseindia.com
          → 403 IS NORMAL — Akamai still sets cookies
          → DO NOT treat 403 as failure
  Step 2: Sleep 3 seconds
  Step 3: GET https://www.nseindia.com/market-data/live-equity-market
  Step 4: Sleep 2 seconds
  Step 5: Make API calls with session cookies

Session lifetime : ~30 minutes
Reuse           : Single session for ALL NSE website calls
Spacing         : 2-3 seconds between API calls
```

#### FII/DII API
```
URL         : /api/fiidiiTradeReact
Available   : ~6:00 PM IST (final data)
              During market hours: provisional intraday only

Confirmed field names (CRITICAL — wrong name = silent N/A):
  netValue    : Net buy/sell — PRIMARY FIELD
                NOT netPurchasesSales
  buyValue    : Gross buy value
  sellValue   : Gross sell value
  category    : "FII/FPI" or "DII"

Units       : Already in Crores — DO NOT DIVIDE
              FII -4,440 = sold ₹4,440 Crores net
History     : Most recent day only — accumulates in DB daily
```

#### NSE Option Chain — IV Snapshot (3:25 PM)
```
URL for STOCKS : /api/option-chain-equities?symbol={SYMBOL}
                  NOT /option-chain-indices (that's for NIFTY/BANKNIFTY)
Auth           : Same NSE session as FII/DII

Timing         : 3:25 PM IST — 5 minutes BEFORE market close
                 NOT 3:30 PM — market closes, IV becomes stale
Calls          : 50 stocks × 1 call = 50 calls
                 All expiries in single response — filter after
Duration       : ~17 seconds at 3 req/sec

Confirmed field names:
  impliedVolatility    : Annualised IV % — EXACT camelCase
                         NOT iv, NOT IV, NOT implied_volatility
                         Filter: impliedVolatility > 0 only
                         (deep OTM returns 0 or null)
  openInterest         : OI for this strike
  changeinOpenInterest : OI change
  totalTradedVolume    : Volume
  lastPrice            : LTP (option premium)
  expiryDate           : Filter by this after fetch
  strikePrice          : Strike price

IV interpretation (annualised %):
  Low  : <15%   — favourable for buying
  Med  : 15-25% — moderate
  High : >25%   — expensive, note in rationale
```

### Kite Connect API (Authenticated — ₹500/month)
```
Auth        : API Key + Access Token
TOKEN EXPIRY: MIDNIGHT IST — NOT 6 AM
              Generate at 7 PM → valid until midnight (5 hours)
              ALL Kite calls must complete before midnight
              Pipeline starts 10 PM → Kite fetch complete 10:40 PM ✅

Instruments master: kite.instruments("NSE") = ~4MB
  Cache ONCE per session in memory
  NEVER call per symbol
  Contains: instrument_token, lot_size, expiry for all instruments

Rate limit  : ~3 requests/second (confirmed)
Sleep       : 0.35s between calls
```

#### Kite Historical OHLCV
```
Method      : kite.historical_data(token, from, to, "day")
Max lookback: 2000 calendar days (~5.5 years)
Our need    : 6 months (180 days) — well within limit

Date column : Timezone-aware (2026-05-22 00:00:00+05:30)
              Use .dt.date for comparisons
Volume      : In shares — divide by lot_size for lots (F&O)
Weekends    : Simply absent — no filtering needed
```

#### Kite Historical OI
```
Method      : kite.historical_data(token, from, to, "day", oi=True)
              oi=True MANDATORY — without it OI column absent, no error

OI units    : In shares — ALWAYS divide by lot_size for lots
              NIFTY 11,340,810 ÷ 75 = 151,211 lots

Expiry day  : OI drops to 0 (all contracts settle)
              Creates artificial spike — mark is_expiry_day=TRUE
              Never use expiry day OI change as signal

3-MONTH ADVANCE LISTING INSIGHT:
  Stock F&O lists 3 months ahead
  Today: MAY, JUNE, JULY all active
  When MAY expires: JUNE already has months of OI history
  OI history NEVER RESETS at expiry
  Always 2+ months of near-month history available from Day 1
  No OI bootstrap problem exists ✅

NO instrument_token_archive needed:
  We track JUNE OI from March (when it was listed)
  When MAY expires → JUNE becomes near month with full history
  Clean, continuous, no gaps
```

#### Kite OAuth
```
Token expiry: Midnight IST
Manual step : Human 2FA (TOTP/SMS) — cannot be automated
              User taps phone link → Zerodha login → captured

Redirect URL: https://api.rankachieversclasses.in/kite/callback
              Must match EXACTLY in Kite developer console
              Trailing slash/domain must be precise

request_token: Single-use — exchange ONCE only
Validation  : kite.profile() — run ONCE at pre-flight only
API Secret  : Private — .env only, never in code or git
```

## OI Data — Three Expiry Strategy
```
Stock F&O has 3 active expiries simultaneously:
  Near month  : Primary analysis reference
  Next month  : Rollover + transition reference
  Far month   : Not tracked (low OI, not useful)

What we store daily:
  Futures OI  : Near + next month (from Kite historical)
  Options OI  : Near + next month (from 3:25 PM snapshot)

On expiry transition:
  Old near → gone
  Old next → becomes new near (with months of history) ✅
  Old far  → becomes new next (with weeks of history) ✅
  New far  → listed by NSE (start tracking)

Continuous series builder handles switch automatically
via get_near_month_expiry(date) function
```

## Spot Price Definition
```
Source  : EOD closing price from NSE Equity Bhavcopy
Field   : CLOSE column for relevant symbol
Use     : All EOD calculations — basis, max pain, strike selection
Timing  : Prior trading day close (available from 6 PM bhavcopy)
```

## Lot Size Management
```
Source    : Kite instruments master (fetched weekly — Sunday)
Cache     : Supabase lot_sizes table
Alert     : Telegram when lot size change detected
Never     : Hardcode lot sizes in code
```

## Sector Map
```
File      : C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer\
            config\sector_map.json
Contains  : Stock → Sector, Sector → NSE Index, Holiday calendar
Update    : Manual on Nifty 50 reconstitution (twice/year)
```

---

# SECTION 4: DATA QUALITY & VALIDATION

## Validation Rules
```
Price Data:
  Minimum 120 days history required
  No gaps > 7 calendar days
  Today's data must be present
  Zero volume candles < 3 (warn if more)
  Single day move > 25% flagged (possible split/bonus)

Options Chain:
  impliedVolatility > 0 (filter zeros — deep OTM)
  ATM OI must be non-zero (zero = stale data)

FII/DII:
  If fetch fails: use yesterday's cached value
  Mark as: source = 'CACHED'
  Pipeline continues — 95% of analysis intact
```

## Data Quality Actions
| Severity | Condition | Action |
|---|---|---|
| CRITICAL | Price data missing entirely | Skip stock, flag report |
| HIGH | Today's data missing | Skip stock, flag |
| MEDIUM | Minor history gaps | Proceed with warning |
| LOW | Options chain thin | Proceed, Claude notes |
| INFO | FII data unavailable | Use cache, note in output |

---

# SECTION 5: ANALYSIS PIPELINE

## Daily Schedule (Trading Days Only)

```
06:00 AM → Supabase keepalive (SELECT 1)
06:05 AM → Sunday: Kite instruments master refresh

07:00 AM → Telegram (LOUD): Kite token reminder
           "🔑 Pipeline starts at 10 PM — refresh token now"

03:25 PM → MARKET CLOSE SNAPSHOT starts
           Step 1: NSE session warmup (~7s)
           Step 2: Option chain 50 stocks (50 calls, ~18s)
           Step 3: Filter IV > 0, filter by expiry
           Step 4: Store options_snapshots + continuous OI
           Telegram (LOUD on fail, SILENT on success)

03:42 PM → Snapshot complete (~17 minutes)
           Telegram (LOUD if FAILED — needs attention)
           Telegram (SILENT if SUCCESS)

06:30 PM → First Bhavcopy check (equity + indices)
07:00 PM → Second check if first failed
07:30 PM → Third check if second failed
08:00 PM → Fourth check — if still unavailable:
           Use previous day cache
           Telegram (SILENT): "⚠️ Bhavcopy delayed — using previous day"

09:30 PM → Pre-flight checks:
           ✓ Kite token valid (kite.profile())
           ✓ Database reachable
           ✓ Bhavcopy downloaded and cached
           ✓ Today's snapshot available
           If any fail: Telegram (LOUD) + abort immediately
           HARD RULE: If pre-flight starts after 09:50 PM
                      Skip Kite portfolio fetch
                      Use previous day's positions

10:00 PM → MAIN PIPELINE starts
           Telegram (SILENT): "🔄 Analysis started
           Token ✅ | Snapshot ✅ | Bhavcopy ✅"

10:00 PM → KITE DATA FETCH (all before midnight):
           Step 1: Load instruments master → memory cache
           Step 2: Fetch OHLCV 50 stocks (~18s, 0.35s sleep)
           Step 3: Fetch near+next month futures OI (~18s)
           Step 4: Fetch positions + order history (~5s)
           Total: ~41 seconds of Kite calls

10:40 PM → Kite fetch complete (80 min before midnight) ✅

10:40 PM → Load bhavcopy from DB cache
           Load snapshot IV from options_snapshots
           Load FII/DII from DB (fetched at 6:30 PM)

10:50 PM → Stage 2: Data Validation

10:55 PM → Stage 2.5: OI Continuous Series Builder

11:00 PM → Stage 3: Level 1 Filter + Shadow Tracking

11:02 PM → Stage 4: Context Bundle Assembly

11:05 PM → Stage 5: Claude Multi-Turn Session starts
           (Zero Kite calls from here — token expiry irrelevant)

12:00 AM → Token expires — nothing Kite-related running ✅

11:55 PM → Claude session ends

12:00 AM → Stage 6: Paper Trade Engine

12:10 AM → Stage 7: Learning Engine + Reconciliation

12:20 AM → Stage 8: Report Generation + Dashboard Update

12:25 AM → Pipeline complete
           Telegram (SILENT): "✅/⚠️/❌ Analysis result"

07:00 AM → Morning Brief
           Telegram (LOUD): Full trade recommendations
```

## Snapshot Failure Fallback Hierarchy
```
1. Today's 3:25 PM snapshot (ideal)
2. Yesterday's snapshot values (reasonable — IV stable day-to-day)
3. IV unavailable — Claude notes: "IV data from previous session"
4. VIX proxy as last resort (never the first option)
```

## Market Hours Guard
```
Any pipeline execution (scheduled or manual trigger):
  If current IST time between 09:00 AM and 16:00 PM:
    REJECT immediately
    Telegram: "⚠️ Pipeline blocked — market hours
               Data incomplete until market closes.
               Next run: Tonight 10:00 PM"
```

## Pipeline State Management
```
Each run creates session record in analysis_sessions
Tracks: stage completion, errors, token counts, cost

Claude turn persistence:
  Every Claude turn output saved to session_claude_turns
  On restart: reconstruct conversation from saved turns
  Resume from last completed turn — no token waste
```

---

# SECTION 6: LEVEL 1 — HARD ELIMINATION FILTER

## Philosophy
Generous eliminator — removes only unambiguously untradeable stocks.
Everything borderline goes to Claude.

## Active Filters (3 Only — F&O Ban Removed)

### Filter 1: Earnings Within 5 Trading Days
```
Source: Kite corporate actions API
Action: Eliminate
Reason: Binary event — unanalysable risk for swing trade
Shadow: No (genuine elimination)
```

### Filter 2: ATR Dead Zone
```
ATR(14) as % of price < 0.8%
Action: Eliminate
Reason: Cannot generate 1:2 RR in 2-5 days
Shadow: Yes — monitor for false positives
```

### Filter 3: F&O Liquidity Minimum
```
ATM options OI < 10,000 (from 3:25 PM snapshot)
Action: Eliminate
Reason: Wide spreads destroy P&L on entry/exit
Shadow: Yes — monitor for false positives
```

## F&O Ban List
```
Status: DEFERRED
Reason: NSE all endpoints returning 404
Action: Revisit when reliable source available
```

## Shadow Tracking
```
Eliminated stocks (Filters 2+3) secretly tracked:
  Store: symbol, reason, price at elimination
  After 5 trading days: check if stock moved >5%
  Data feeds monthly filter evolution review
```

---

# SECTION 7: LEVEL 2 — CLAUDE PRE-SCAN

## Data Package Per Stock
- Last 30 days daily OHLCV
- Current price vs 20/50 EMA
- RSI (14) current value
- ATR (14) current value
- Volume ratio (3-day vs 20-day)
- Sector performance last 5 days
- Near month futures OI trend (last 10 days)

## Pre-Scan Output Per Stock
```json
{
  "symbol": "HDFCBANK",
  "direction": "LONG | SHORT | NEUTRAL | SKIP",
  "pre_scan_reasoning": "2-3 lines maximum",
  "priority": "HIGH | MEDIUM | LOW",
  "forward_to_deep": true,
  "override_level1": false,
  "override_reason": null
}
```

## Expected Throughput
```
~47 stocks from Level 1
HIGH:   8-10 → Deep analysis
MEDIUM: 6-8  → Deep analysis (lower priority)
SKIP:   ~27  → Excluded
Total forwarded: 15-20
```

---

# SECTION 8: LEVEL 3 — CLAUDE DEEP ANALYSIS

## Data Package Per Stock
```
PRICE DATA:
  6 months daily OHLCV — full, no compression
  Spot price (EOD close from bhavcopy)
  Lot size (from instruments master cache)

OI DATA:
  Continuous OI series — 30 days (or available history)
    Near month: daily OI, OI change, PCR
    Next month: daily OI (rollover context)
    Max pain per day
    Rollover % (rollover week)
    is_expiry_day flag

  Today's options chain (from 3:25 PM snapshot):
    Strike-wise CE + PE OI
    OI walls: Top 5 strikes by OI each side
    IV per strike (impliedVolatility field)

FUTURES DATA:
  Continuous series — 30 days (or available history)
    Near month: price, OI, OI change, basis, basis %
    Next month: OI (rollover context)
    Rollover % (rollover week)

ROLLOVER CONTEXT:
  Current phase (NORMAL/ROLLOVER_WATCH/TRANSITION/EXPIRY)
  Days to expiry (trading days)
  Phase-specific Claude instructions

IV CONTEXT:
  From today's snapshot: annualised %
  Yesterday's snapshot if today unavailable
  Assessment: Low (<15%) / Med (15-25%) / High (>25%)
```

## Index Context (Sent Once Per Session)
```
Nifty 50 + Bank Nifty: 6 months daily OHLCV
India VIX: 30 days (closing values only)
FII/DII flows: 30 days (from accumulated fii_dii_flows table)
Current market regime
Signal attribution table (Phase 2 — empty in Phase 1)
Active directives (Phase 2 — empty in Phase 1)
```

## Hard Rejection Gates
```
Risk-Reward < 1:2           → Auto-reject
Trading days to expiry < 6  → Auto-reject, use next month
Earnings within hold period → Auto-reject
Available trade slots = 0   → Skip selection turn entirely
```

## Position Slot Enforcement (Before Selection Turn)
```python
available_slots = max_concurrent_trades - count_open_real_trades()
if available_slots == 0:
    skip selection → "All trade slots occupied"
if available_slots == 1:
    Claude selects maximum 1 setup
if available_slots >= 2:
    Claude selects up to 2-3 setups
```

---

# SECTION 9: CONVICTION SCORING FRAMEWORK

## Layer 1: Price Structure (30 points)
| Signal | Max | Scoring |
|---|---|---|
| Multi-timeframe trend alignment | 8 | Both weekly+daily=8, daily only=4, conflicting=0 |
| S/R zone quality | 8 | Significant level=8, approximate=4, none=0 |
| Chart pattern | 8 | Textbook=8, developing=5, weak=2 |
| Candlestick confirmation | 6 | Strong=6, minor=3, absent=0 |

## Layer 2: Momentum & Volume (25 points)
| Signal | Max | Scoring |
|---|---|---|
| Volume character | 10 | Clearly supportive=10, neutral=5, contradictory=0 |
| RSI — contextual | 7 | Context-dependent — NOT fixed ranges |
| MACD momentum | 5 | Aligned=5, neutral=2, opposing=0 |
| ATR character | 3 | Supports thesis=3 |

## Layer 3: Index F&O Context (25 points)
| Signal | Max | Scoring |
|---|---|---|
| Nifty OI direction | 8 | Supportive=8, neutral=4, opposing=0 |
| Index PCR | 7 | Contextual — contrarian at extremes |
| India VIX level | 5 | Favourable=5, neutral=2, adverse=0 |
| Index max pain | 5 | Aligned=5 |

## Layer 4: Stock F&O (10 points)
| Signal | Max | Scoring |
|---|---|---|
| OI trend quality | 5 | Consistent 10+ day buildup=5, inconsistent=3, erratic=1 |
| Futures basis trend | 3 | Expanding+rising OI=3, stable=2, contracting=0 |
| IV context | 2 | Low=2, medium=1, high=0 |

## Layer 5: Market Context (10 points)
| Signal | Max | Scoring |
|---|---|---|
| Sector momentum | 5 | Clear tailwind=5, neutral=2, headwind=0 |
| FII/DII 3-day flow | 5 | Aligned=5, mixed=2, opposing=0 |

## Staging Thresholds
| Score | Stage | Action |
|---|---|---|
| 75–100 | 🟢 Trade Ready | Max 2-3 per day |
| 55–74 | 🟡 Watch | 1-2 days from trigger |
| 35–54 | 🔵 On Radar | Early signals |
| <35 | ❌ Skip | No action |

## PCR Interpretation (Validated From NSE Data)
```
PCR < 0.7  : Excessive bullishness → contrarian BEARISH signal
PCR 0.7-1.1: Neutral range
PCR > 1.3  : Excessive bearishness → contrarian BULLISH signal

CRITICAL: PCR is contrarian at extremes — not directional
          High PCR ≠ automatically bearish
          Always interpret in context of price action
```

## Rollover Week Scoring Adjustment
```
ROLLOVER_WATCH + TRANSITION phases:
  OI trend quality: Cap at 3 points (OI includes rollover noise)
  Futures basis:    Weight increases to compensate
  
EXPIRY phase (T-1):
  OI components: 0 points (settlement data)
  Conviction threshold raised by 5 for Trade Ready
  Fewer setups on expiry day is correct behaviour
```

---

# SECTION 10: OPTIONS STRATEGY SELECTION

## CE vs PE
```
LONG setup  → Buy CE
SHORT setup → Buy PE
```

## Strike Selection
```
IV Low  (<15%): Slightly OTM — better RR
IV Med  (15-25%): ATM or just OTM
IV High (>25%): ATM — reduce premium cost
Minimum Delta: 0.35 (never deep OTM for swing)
```

## Expiry Selection
```
Trading days remaining ≥ 6: Current month expiry
Trading days remaining ≤ 5: Next month expiry
In TRANSITION or EXPIRY phase: Always next month
```

## Position Sizing
```
max_risk = capital × risk_pct (2-3%)
risk_per_lot = (entry_premium - stop_loss_premium) × lot_size
lots = floor(max_risk / risk_per_lot)
Cap: max 5 lots, min 1 lot
If 1 lot exceeds 3% capital: reject setup (unsizable)
```

## Two-Target Exit
```
Target 1 (50% position): RR 1:1.5 — partial lock-in
Target 2 (remaining 50%): RR 1:2+ — full target
After T1: Move SL to entry price (breakeven)
```

---

# SECTION 11: MARKET REGIME DETECTION

## Classification
```python
Inputs: Nifty 60-day OHLCV, VIX 30-day

if vix > 20:
    return "BEAR_HIGH_VOLATILITY" if ret20d < -5 else "HIGH_VOLATILITY"
if price > ema20 > ema50 and ret20d > 3:
    return "BULL_TRENDING"
if price < ema20 < ema50 and ret20d < -3:
    return "BEAR_TRENDING"
if 15-day range < 4%:
    return "SIDEWAYS_TIGHT"
return "SIDEWAYS_WIDE"
```

## Signal Guidance Per Regime
| Regime | Favour | Caution |
|---|---|---|
| BULL_TRENDING | Breakout longs | Short setups |
| BEAR_TRENDING | Breakdown shorts | Long setups |
| SIDEWAYS_TIGHT | S/R bounces | Breakouts |
| HIGH_VOLATILITY | Wide stops | Naked option buying |

---

# SECTION 12: DATABASE SCHEMA

## Tables

### trade_setups
```sql
CREATE TABLE trade_setups (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              VARCHAR(50) NOT NULL,
    setup_date              DATE NOT NULL,
    symbol                  VARCHAR(20) NOT NULL,
    direction               VARCHAR(10) CHECK (direction IN ('LONG','SHORT')),
    stage                   VARCHAR(20),
    setup_type              VARCHAR(50),
    setup_maturity          VARCHAR(10),
    conviction_score        INTEGER,
    instrument              VARCHAR(50),
    strike                  NUMERIC,
    option_type             VARCHAR(5),
    expiry_date             DATE,
    entry_zone_low          NUMERIC,
    entry_zone_high         NUMERIC,
    stop_loss_premium       NUMERIC,
    target_1_premium        NUMERIC,
    target_2_premium        NUMERIC,
    underlying_stop         NUMERIC,
    lots                    INTEGER,
    lot_size                INTEGER,
    max_risk_inr            NUMERIC,
    risk_pct_capital        NUMERIC,
    target_reward_inr       NUMERIC,
    risk_reward             NUMERIC,
    iv_at_flag              NUMERIC,
    iv_assessment           VARCHAR(20),
    signals_contributing    TEXT[],
    scoring_breakdown       JSONB,
    claude_full_rationale   TEXT,
    mentor_explanation      TEXT,
    key_learning_today      TEXT,
    why_could_be_wrong      TEXT,
    market_regime           VARCHAR(30),
    vix_at_analysis         NUMERIC,
    days_to_expiry_at_flag  INTEGER,
    rollover_phase          VARCHAR(20),
    near_month_oi_at_flag   BIGINT,
    next_month_oi_at_flag   BIGINT,
    rollover_pct_at_flag    NUMERIC,
    user_response           VARCHAR(20),
    user_context_note       TEXT,
    user_response_at        TIMESTAMPTZ,
    entry_triggered         BOOLEAN DEFAULT FALSE,
    entry_date              DATE,
    actual_entry_price      NUMERIC,
    paper_outcome           VARCHAR(20),
    paper_exit_date         DATE,
    paper_exit_price        NUMERIC,
    paper_pnl_inr           NUMERIC,
    paper_holding_days      INTEGER,
    real_trade_executed     BOOLEAN DEFAULT FALSE,
    real_trade_pnl_inr      NUMERIC,
    kite_order_ids          TEXT[],
    rationale_held          BOOLEAN,
    signals_held            TEXT[],
    signals_failed          TEXT[],
    post_mortem_text        TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
```

### analysis_sessions
```sql
CREATE TABLE analysis_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              VARCHAR(50) UNIQUE NOT NULL,
    session_date            DATE NOT NULL,
    status                  VARCHAR(20),
    stage_statuses          JSONB,
    stocks_level1_passed    INTEGER,
    stocks_deep_analyzed    INTEGER,
    trade_ready_count       INTEGER,
    watch_count             INTEGER,
    radar_count             INTEGER,
    market_regime           VARCHAR(30),
    nifty_close             NUMERIC,
    vix_close               NUMERIC,
    fii_net_flow_cr         NUMERIC,
    claude_tokens_input     INTEGER,
    claude_tokens_output    INTEGER,
    claude_cost_usd         NUMERIC,
    pipeline_duration_mins  INTEGER,
    prompt_versions         JSONB,
    telegram_message_ids    JSONB,
    errors                  JSONB,
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);
```

### session_claude_turns
```sql
CREATE TABLE session_claude_turns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      VARCHAR(50) NOT NULL,
    turn_number     INTEGER NOT NULL,
    turn_type       VARCHAR(30),
    symbol          VARCHAR(20),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    output_text     TEXT,
    completed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, turn_number)
);
```

### options_snapshots
```sql
CREATE TABLE options_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    snapshot_date   DATE NOT NULL,
    expiry_date     DATE NOT NULL,
    strike          NUMERIC NOT NULL,
    option_type     VARCHAR(5) NOT NULL,
    oi              BIGINT,
    oi_change       BIGINT,
    volume          BIGINT,
    iv              NUMERIC,
    premium_close   NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, snapshot_date, expiry_date, strike, option_type)
);
```

### continuous_oi_series
```sql
CREATE TABLE continuous_oi_series (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    date                DATE NOT NULL,
    rollover_phase      VARCHAR(20),
    near_expiry         DATE,
    next_expiry         DATE,
    near_month_oi       BIGINT,
    next_month_oi       BIGINT,
    total_oi            BIGINT,
    oi_change           BIGINT,
    in_rollover_week    BOOLEAN DEFAULT FALSE,
    is_expiry_day       BOOLEAN DEFAULT FALSE,
    rollover_pct        NUMERIC,
    pcr_near            NUMERIC,
    pcr_total           NUMERIC,
    max_pain            NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
```

### futures_continuous_series
```sql
CREATE TABLE futures_continuous_series (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    date                DATE NOT NULL,
    rollover_phase      VARCHAR(20),
    near_expiry         DATE,
    next_expiry         DATE,
    futures_price       NUMERIC,
    spot_price          NUMERIC,
    basis               NUMERIC,
    basis_pct           NUMERIC,
    near_month_oi       BIGINT,
    next_month_oi       BIGINT,
    oi_change           BIGINT,
    in_rollover_week    BOOLEAN DEFAULT FALSE,
    is_expiry_day       BOOLEAN DEFAULT FALSE,
    rollover_pct        NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
```

### price_history
```sql
CREATE TABLE price_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol      VARCHAR(20) NOT NULL,
    date        DATE NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
CREATE INDEX idx_price_history_symbol_date ON price_history(symbol, date);
```

### fii_dii_flows
```sql
CREATE TABLE fii_dii_flows (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE UNIQUE NOT NULL,
    fii_buy_cr  NUMERIC,
    fii_sell_cr NUMERIC,
    fii_net_cr  NUMERIC,
    dii_buy_cr  NUMERIC,
    dii_sell_cr NUMERIC,
    dii_net_cr  NUMERIC,
    source      VARCHAR(20) DEFAULT 'LIVE',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### lot_sizes
```sql
CREATE TABLE lot_sizes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    lot_size        INTEGER NOT NULL,
    previous_lot    INTEGER,
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol)
);
```

### kite_tokens
```sql
CREATE TABLE kite_tokens (
    user_id         VARCHAR(20) PRIMARY KEY DEFAULT 'primary',
    access_token    TEXT NOT NULL,
    generated_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);
```

### watchlist_staging
```sql
CREATE TABLE watchlist_staging (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    current_stage       VARCHAR(20),
    direction_bias      VARCHAR(10),
    days_in_stage       INTEGER DEFAULT 0,
    first_flagged_date  DATE,
    stage_history       JSONB,
    last_analysis_notes TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
```

### level1_shadow_tracks
```sql
CREATE TABLE level1_shadow_tracks (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol               VARCHAR(20) NOT NULL,
    elimination_date     DATE NOT NULL,
    elimination_reason   VARCHAR(50) NOT NULL,
    atr_pct              NUMERIC,
    price_at_elimination NUMERIC,
    track_until_date     DATE,
    price_after_5d       NUMERIC,
    move_pct             NUMERIC,
    significant_move     BOOLEAN,
    reconciled_at        TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
```

### system_config
```sql
CREATE TABLE system_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  VARCHAR(20),
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Default values
INSERT INTO system_config VALUES
('capital_inr',               '500000',   'float',  'Total capital'),
('risk_pct_min',              '0.02',     'float',  'Min risk per trade'),
('risk_pct_max',              '0.03',     'float',  'Max risk per trade'),
('min_rr_ratio',              '2.0',      'float',  'Min RR gate'),
('max_concurrent_trades',     '3',        'int',    'Max open positions'),
('conviction_trade_ready',    '75',       'int',    'Min score Trade Ready'),
('conviction_watch',          '55',       'int',    'Min score Watch'),
('conviction_radar',          '35',       'int',    'Min score On Radar'),
('atr_dead_zone_pct',         '0.8',      'float',  'ATR elimination threshold'),
('earnings_buffer_days',      '5',        'int',    'Days before earnings'),
('min_atm_oi',                '10000',    'int',    'Min ATM OI for liquidity'),
('min_dte_trading_days',      '6',        'int',    'Min DTE for options'),
('claude_monthly_budget_usd', '60',       'float',  'Hard ceiling API budget'),
('claude_warning_threshold',  '0.75',     'float',  'Budget warning %'),
('max_deep_analysis_stocks',  '18',       'int',    'Stocks per deep session'),
('pipeline_start_time_ist',   '22:00',    'string', 'Pipeline start time'),
('morning_brief_time_ist',    '07:00',    'string', 'Morning brief time'),
('snapshot_time_ist',         '15:25',    'string', 'IV snapshot time'),
('pipeline_enabled',          'true',     'bool',   'Pipeline on/off switch');
```

---

# SECTION 13: FEEDBACK LOOP & SELF-CALIBRATION (Phase 2)

Deferred to Phase 2. Phase 1 collects data for future calibration.
Core ledger tables (trade_setups, outcomes) populated from Day 1.

---

# SECTION 14: HUMAN-IN-THE-LOOP (Phase 2)

User response buttons deferred to Phase 2.
Context note field exists in trade_setups from Day 1.

---

# SECTION 15: LEARNING ARCHITECTURE (Phase 2)

Weekly debrief and pattern curriculum deferred to Phase 2.
Daily mentor explanation included in Claude output from Day 1.

---

# SECTION 16: PROMPT ENGINEERING

## Model
```
All pipeline steps: claude-sonnet-4-6
Review trigger    : After first month actual cost data
No mixed models   : Simpler architecture, clean cost baseline
```

## Prompt Versions (Tracked Per Session)
```json
{
  "system_prompt":   "v1.0",
  "market_context":  "v1.0",
  "prescan":         "v1.0",
  "deep_analysis":   "v1.0",
  "reconciliation":  "v1.0"
}
```

## System Prompt Template
```
You are an experienced hedge fund manager and swing trading mentor
specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds,
stock options only — monthly Tuesday expiry).

━━━━━ TONIGHT'S SESSION CONTEXT ━━━━━
Date          : {date}
Market Regime : {regime}
Trade Slots   : {available} of 3 available
Capital at Risk: ₹{open_risk} ({pct}%)

━━━━━ ROLLOVER CONTEXT ━━━━━
{rollover_phase_block}

[NORMAL]: No special rollover context
[ROLLOVER_WATCH T-5 to T-3]:
  "Rollover beginning. Near month OI declining partially
   reflects rolling, not just direction. Monitor next month
   OI alongside. Futures basis direction more meaningful."
[TRANSITION T-2]:
  "Next month now dominant. Near month OI collapse expected.
   Recommend next month expiry for all new trades."
[EXPIRY T-1]:
  "Expiry day. Near month settled — OI data is settlement noise.
   Use yesterday's OI as last valid reference. Weight price
   structure and futures basis heavily today."

━━━━━ PCR INTERPRETATION GUIDE ━━━━━
PCR is contrarian at extremes:
PCR < 0.7  → contrarian bearish (excessive bullishness)
PCR 0.7-1.1 → neutral
PCR > 1.3  → contrarian bullish (excessive bearishness)
Do NOT interpret high PCR as automatically bearish.

━━━━━ SIGNAL PERFORMANCE ━━━━━
{signal_attribution_table}
[Phase 1: "Signal attribution building — use general judgment"]

━━━━━ RECENT OUTCOMES ━━━━━
{recent_outcomes_7_days}

━━━━━ ACTIVE WATCHLIST ━━━━━
{watchlist_summary}

━━━━━ OPEN POSITIONS ━━━━━
{open_positions}

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

---

# SECTION 17: COST MANAGEMENT

## Pricing
```
claude-sonnet-4-6:
  Input  : $3.00 per million tokens
  Output : $15.00 per million tokens
```

## Estimated Usage Per Session
```
Turn 1 (Market context)      :  3,000 in +  1,000 out
Turn 2 (Pre-scan 45 stocks)  : 25,000 in +  4,000 out
Turns 3-20 (Deep 18 stocks)  :144,000 in + 36,000 out
Turn N+1 (Selection)         :  5,000 in +  1,500 out
Turn N+2 (Reconciliation)    : 10,000 in +  3,000 out

Total: ~187,000 input + ~45,500 output
Cost per session: ~$1.24 (~₹103)
Monthly (22 days): ~$27 (~₹2,250)
Note: Higher than original estimate due to larger OI data packages
      Actual cost confirmed after Month 1
```

## Cost Controls
```
Layer 1: Anthropic Console hard limit: $60/month
         Set at console.anthropic.com BEFORE first run

Layer 2: System alerts
  75% ($45): Telegram warning
  $50 spent: Reduce deep stocks to 12 automatically
  $55 spent: Minimal analysis mode (10 stocks)

Layer 3: Per-session token limit (configurable)
  Default: 250,000 tokens combined
```

## Updated Cost Summary
```
Kite Connect   : ₹500/month (CORRECTED from ₹2,000)
Claude API     : ₹1,400-2,500/month (estimated)
Everything else: ₹0
─────────────────────────────────
Normal range   : ₹1,900-3,000/month
Hard ceiling   : ~₹3,500/month
```

---

# SECTION 18: TELEGRAM NOTIFICATIONS

## Implementation
```
Library    : requests (direct Bot API)
Parse mode : HTML (not Markdown — more reliable)
Store in   : .env only — TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Token      : Permanent — never expires (unlike Kite)
Rate limit : 1 message/second — sleep 1.1s between messages
Max length : 4,096 chars per message — split if longer
Log        : message_id for every sent message
```

## HTML Tags (Confirmed Working)
```
<b>text</b>        → Bold
<i>text</i>        → Italic
<code>text</code>  → Monospace — USE FOR ALL PRICES AND NUMBERS
                     Prevents phone number hyperlinking
No nested tags     → <b><i>text</i></b> unreliable
```

## Silent vs Loud
```
LOUD (disable_notification=False — plays sound):
  Morning brief 7 AM
  Kite token reminder 7 PM
  Snapshot FAILED (needs action before pipeline)
  Critical infrastructure failures

SILENT (disable_notification=True — no sound):
  Pipeline start
  Pipeline status updates
  Snapshot started + completed (success)
  Bhavcopy check results
  Cost alerts
  All night notifications
```

## Message Templates

### Morning Brief (LOUD — 7 AM)
```html
<b>🌅 Morning Brief — Wed 20 May 2026</b>

<b>📊 Market Context</b>
Nifty: <code>21,840</code> | Regime: Bull Trending
VIX: <code>14.2</code> 🟢 | FII: <code>+₹1,240 Cr</code>
<b>━━━━━━━━━━━━━━━━━━</b>
<b>🟢 TRADE READY (2)</b>

<b>1. HDFCBANK 1480 CE | LONG</b>
Conviction: <code>82</code> | Flag Breakout
Entry: <code>₹50–54</code> | SL: <code>₹38</code>
T1: <code>₹78</code> | T2: <code>₹95</code>
Risk: <code>₹11,000</code> | RR: <code>1:2.4</code>
Expiry: <code>27 May</code> (<code>7</code> trading days)
IV: <code>18%</code> — Low ✅
<b>━━━━━━━━━━━━━━━━━━</b>
<b>🟡 WATCHING (3)</b>
INFY · RELIANCE · BAJFINANCE

📱 <a href="https://trading.rankachieversclasses.in">Full Analysis</a>
```

### Token Reminder (LOUD — 7 PM)
```html
🔑 <b>Kite Token Refresh Needed</b>
Pipeline starts at <b>10:00 PM tonight</b>.
Token expires at midnight.
<a href="https://trading.rankachieversclasses.in/token">Refresh Token</a>
```

### Pipeline Start (SILENT — 10 PM)
```html
🔄 <b>Analysis Pipeline — Wed 20 May 2026</b>
Token ✅ | Snapshot ✅ | Bhavcopy ✅
Starting analysis...
```

### Pipeline Complete (SILENT)
```html
✅ <b>Analysis Complete — Wed 20 May 2026</b>
🟢 Trade Ready: <code>2</code> | 🟡 Watch: <code>3</code>
Duration: <code>85</code> min | Cost: <code>$1.18</code>
Brief arrives at <code>7:00 AM</code>
```

### Snapshot Failed (LOUD)
```html
❌ <b>Snapshot FAILED — Wed 20 May 2026</b>
IV unavailable for tonight's analysis.
Pipeline will use yesterday's IV values.
Check NSE connectivity if this persists.
```

---

# SECTION 19: DASHBOARD SPECIFICATION

## Tech Stack
```
Frontend : React + Tailwind CSS
Hosting  : Netlify (replacing Vercel — already have account)
Backend  : Python FastAPI (Windows Laptop)
Database : Supabase PostgreSQL
Auth     : Supabase Auth
Public   : trading.rankachieversclasses.in (Netlify)
API      : api.rankachieversclasses.in (Cloudflare Tunnel)
```

## Dashboard Architecture (Option B — Confirmed)
```
NORMAL OPERATION:
  Phone → Netlify Dashboard → Laptop API → Supabase

LAPTOP DOWN:
  Phone → Netlify Dashboard → Supabase DIRECTLY
  Using Supabase JavaScript client in React
  Shows last cached data with stale indicator
  Dashboard always works regardless of laptop status

Supabase Row-Level Security:
  Read-only access for dashboard queries
  Write access only from backend API (service key)
  Dashboard uses anon key (read-only)
```

## Mobile-First Rules
```
No horizontal scrolling — ever
Cards not tables on mobile
Dark mode by default
Single column layout
Large tap targets (min 44px)
Critical info always at top
```

## Navigation (Bottom Bar)
```
🏠 Today | 👁️ Watchlist | 📊 Performance | ⚙️ Config
```

## Phase 1 Screens

### Screen 1: Today's Action
```
Market context header (regime, VIX, FII)
Trade Ready setups (cards with full parameters)
Watch list stocks
Offline indicator if laptop down
```

### Screen 2: Setup Detail
```
Full Claude rationale
Scoring breakdown
Mentor explanation
Key learning today
Why could be wrong
Trade parameters
User response buttons (Phase 2)
```

### Screen 3: Open Trades
```
Real positions from Kite
P&L, hold days, SL reminder
T1/T2 status
```

### Offline Behavior
```
Shows last cached analysis always
Header displays:
  "⚠️ Backend offline"
  Analysis date
  Calendar days ago
  Trading days ago
[🔄 Reconnect] button
```

---

# SECTION 20: CONFIGURATION CONSOLE (Phase 2)

Deferred to Phase 2. In Phase 1: use Supabase admin panel directly.

Planned parameters (7 total):
- Monthly budget limit (USD)
- Warning threshold (%)
- Stocks for deep analysis
- Tokens per session
- Pipeline enabled/disabled
- Analysis time (IST)
- Morning brief time (IST)

---

# SECTION 21: INTERNAL API SPECIFICATION

```
Authentication : JWT via Supabase Auth

GET  /api/today           → Today's analysis + setups
GET  /api/setup/{id}      → Full setup detail
GET  /api/watchlist       → Watch + Radar stocks
GET  /api/positions       → Live Kite positions + P&L
GET  /api/performance     → System performance metrics
GET  /api/system/status   → Pipeline health + next run
POST /api/system/trigger  → Manual pipeline run
GET  /kite/refresh        → Initiate token refresh
GET  /kite/callback       → OAuth callback handler
GET  /health              → Basic health check
```

---

# SECTION 22: SECURITY

## Secrets (.env — Never Committed)
```
KITE_API_KEY=
KITE_API_SECRET=
ANTHROPIC_API_KEY=
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
JWT_SECRET=
ENVIRONMENT=production
```

## Key Security Rules
```
API Secret (Kite): Private — treat like database password
Telegram tokens  : In .env only — not in spec or codebase
Supabase anon    : Read-only — safe for dashboard
Supabase service : Write access — backend only, never frontend
SSL              : Auto via Netlify (dashboard) + Cloudflare (API)
```

---

# SECTION 23: INFRASTRUCTURE

## Architecture
```
Windows Laptop (always on, sleep disabled):
  Python FastAPI (port 8000) — NSSM Windows service
  APScheduler — embedded scheduler
  Cloudflare Tunnel — Windows service → api.rankachieversclasses.in

Netlify:
  React dashboard → trading.rankachieversclasses.in
  Auto-deploy from GitHub
  Same platform as DNS (rankachieversclasses.in)

Supabase:
  PostgreSQL database — free tier
  Daily keepalive prevents pausing

External APIs:
  NSE, Kite, Claude, Telegram
```

## Domain Configuration
```
DNS Provider: Netlify (rankachieversclasses.in)

Netlify handles dashboard subdomain natively:
  trading.rankachieversclasses.in → Netlify hosting

DNS record for API:
  CNAME api → [cloudflare-tunnel-uuid].cfargotunnel.com
```

## Windows Setup
```
# Prevent sleep
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

# Install NSSM for FastAPI service
nssm install SwingTradingBackend python main.py
nssm set SwingTradingBackend AppDirectory C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer
nssm set SwingTradingBackend Start SERVICE_AUTO_START
nssm set SwingTradingBackend AppRestartDelay 30000
nssm start SwingTradingBackend

# Cloudflare Tunnel
cloudflared tunnel login
cloudflared tunnel create swing-trading
cloudflared service install
net start cloudflared
```

## Project Directory
```
C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer\
├── main.py                    ← FastAPI entry point
├── scheduler.py               ← APScheduler jobs
├── .env                       ← All secrets
├── requirements.txt
├── config\
│   └── sector_map.json        ← Sector + holiday mapping
├── integrations\
│   ├── impl_01_nse_equity_bhavcopy.py
│   ├── impl_02_nse_indices_bhavcopy.py
│   ├── impl_03_nse_fii_dii.py
│   ├── impl_04_kite_historical_ohlcv.py
│   ├── impl_05_kite_historical_oi.py
│   ├── impl_06_nse_option_chain_iv.py
│   ├── impl_07_telegram_notify.py
│   └── impl_08_kite_oauth.py
├── pipeline\
│   ├── orchestrator.py
│   ├── data_ingestion.py
│   ├── data_validation.py
│   ├── level1_filter.py
│   ├── oi_series_builder.py
│   ├── context_builder.py
│   ├── claude_session.py
│   ├── paper_trade_engine.py
│   ├── reconciliation.py
│   └── report_generator.py
├── indicators\
│   ├── technical.py
│   └── regime.py
├── api\
│   ├── auth.py
│   ├── dashboard.py
│   ├── setups.py
│   └── kite_oauth.py
├── database\
│   ├── client.py
│   ├── queries.py
│   └── migrations\
│       ├── 001_initial_schema.sql
│       ├── 002_oi_series.sql
│       └── 003_shadow_tracks.sql
├── frontend\                  ← React dashboard
│   └── src\
├── validation_tests\          ← 8 validated impl files
└── logs\                      ← 30-day rotation
```

## Kite OAuth Production
```
Kite Developer Console:
  Redirect URL: https://api.rankachieversclasses.in/kite/callback
  Must match EXACTLY (trailing slash, correct domain)

Daily flow:
  7 PM: Telegram reminder
  User: Taps link → Zerodha 2FA login
  System: Captures callback → stores token → confirms via Telegram
  10 PM: Pre-flight validates token
  Midnight: Token expires — after all Kite calls complete ✅
```

---

# SECTION 24: SCHEDULING

## APScheduler Jobs
```python
IST = pytz.timezone('Asia/Kolkata')

# Supabase keepalive — 6 AM daily
CronTrigger(hour=6, minute=0, timezone=IST)

# Instruments master refresh — 6:05 AM Sundays
CronTrigger(hour=6, minute=5, day_of_week='sun', timezone=IST)

# Kite token reminder — 7 PM daily (trading days)
CronTrigger(hour=19, minute=0, day_of_week='mon-fri', timezone=IST)

# Market close snapshot — 3:25 PM daily (trading days)
CronTrigger(hour=15, minute=25, day_of_week='mon-fri', timezone=IST)

# Bhavcopy check — 6:30 PM daily (trading days)
CronTrigger(hour=18, minute=30, day_of_week='mon-fri', timezone=IST)

# Main pipeline — 10 PM daily (trading days)
CronTrigger(hour=22, minute=0, day_of_week='mon-fri', timezone=IST)

# Morning brief — 7 AM daily (trading days)
# Scheduled dynamically at end of pipeline
```

---

# SECTION 25: ERROR HANDLING

## Categories
| Error | Response |
|---|---|
| Kite auth failure at pre-flight | Abort, Telegram (LOUD) |
| Database unreachable | Abort, Telegram (LOUD) |
| Bhavcopy unavailable after 4 retries | Use cache, SILENT alert |
| FII/DII fetch fails | Use yesterday's cache, continue |
| Claude API timeout | Retry 3x exponential backoff |
| Single stock data missing | Skip stock, note in report |
| Snapshot fails | Use yesterday's IV, LOUD alert |
| Telegram fails | Retry 3x × 30s, log, continue |

## Midnight Guard
```
If pre-flight starts after 09:50 PM IST:
  Skip Kite portfolio data fetch
  Use previous day's positions from DB
  Note in session: "Kite portfolio data from previous session"
  This prevents any Kite calls after 10 PM starting late
```

## Restart Recovery
```
On every backend startup:
  Check if today's pipeline was missed
  If missed and time > 10 PM: run immediately + Telegram alert
  If pipeline interrupted: resume from last session_claude_turns entry
```

---

# SECTION 26: PAPER TRADING ENGINE

## Philosophy
Every Trade Ready and Watch setup = automatic paper trade.
Measures system accuracy independent of user decisions.

## Actual Option LTP (Not Underlying Price)
```
Paper trade uses ACTUAL option/futures LTP from Kite data.
NOT underlying stock price movement.

Entry price : Actual option LTP at entry zone
Exit price  : Actual option LTP from Kite historical_data
P&L         : (exit - entry) × lots × lot_size

Why this matters:
  IV crush captured naturally
  Theta decay captured naturally
  More accurate than underlying price approximation
  Reflects real-world P&L accurately
```

## State Machine
```
FLAGGED → entry zone reached? → ACTIVE → SL hit → CLOSED_SL
                              ↘ T1 hit → PARTIAL → T2 hit → CLOSED_TARGET
                                                  → SL hit → CLOSED_BREAKEVEN
                                                  → Day 5  → CLOSED_EXPIRED
       → entry not reached in 2 days → ENTRY_MISSED
```

## Entry Rules
```
Uses next 2 trading days' actual option LTP data
Entry triggered when LTP trades through entry zone
Fill: Midpoint of entry zone + 0.5% slippage
Gap scenarios: If gapped past zone → ENTRY_MISSED
```

## Exit Rules
```
SL checked before target each day (conservative)
T1 hit (50% lots): Move SL to entry (breakeven)
T2 hit (remaining): Full close
Day 5: Exit at actual LTP regardless
Brokerage: ₹40/lot or 0.03% (lower) × 2 (buy+sell)
```

---

# SECTION 27: OI CONTINUOUS SERIES BUILDER

## Core Logic
```
3-month advance listing means:
  At any point: 2+ months of near-month history
  At any point: 1+ month of next-month history
  OI history NEVER RESETS at expiry

Nightly processing:
  For each Nifty 50 stock:
    1. Determine rollover phase
    2. Identify near/next expiry
    3. Aggregate strike-wise OI by expiry
    4. Calculate PCR (near + total)
    5. Calculate max pain
    6. Calculate rollover %
    7. Calculate basis (futures)
    8. Store in continuous tables

Max pain calculation:
  For each possible settlement price:
    Sum losses to all CE buyers (settlement < strike)
    Sum losses to all PE buyers (settlement > strike)
  Max pain = strike with maximum total buyer loss
```

## Expiry Transition Handling
```
get_near_month_expiry(date):
  Returns last Tuesday of current month
  If today IS last Tuesday (expiry):
    Return last Tuesday of next month
  The day after expiry:
    Next month automatically becomes near month
    Already has full OI history ✅

is_expiry_day(date):
  Returns True if date == last_tuesday_of_month
  Used to mark OI = 0 as settlement noise
```

---

# SECTION 28: ROLLOVER TRANSITION FRAMEWORK

## Schedule
```
T-6+         : NORMAL
T-5 to T-3   : ROLLOVER_WATCH (near primary, next shown)
T-2 (Monday) : TRANSITION (next becomes primary)
T-1 (Tuesday): EXPIRY (next month sole reference)
```

## Efficacy Tracking
```
Every setup stores: rollover_phase, days_to_expiry_at_flag

Monthly report (after 3 months of data):
  Hit rate by phase
  If ROLLOVER_WATCH < NORMAL - 15%:
    Propose moving boundary T-5 → T-4
  User approval required for any change

Dashboard shows rollover phase performance after 3 months.
```

---

# SECTION 29: VALIDATION RESULTS (Final Record)

```
T1  NSE Equity Bhavcopy      ✅ PASS
    File: sec_bhavdata_full_{DDMMYYYY}.csv (plain CSV)
    No session, no ZIP, open CDN

T2  Kite F&O + OI            ✅ PASS
    historical_data(oi=True) confirmed
    OI in shares ÷ lot_size = lots
    3-month advance listing = no OI gap ever

T2  NSE Option Chain IV      ⚠️ PARTIAL
    IV only during market hours
    Solution: 3:25 PM snapshot (confirmed working)

T2  NSE F&O Bhavcopy         ❌ FAIL
    NSE removed public access
    Replaced by: Kite historical OI + 3:25 PM snapshot

T3  NSE Indices Bhavcopy     ✅ PASS
    "India VIX" field (strip spaces, close only)
    147 indices including all sector indices

T4  NSE FII/DII              ✅ PASS
    netValue field (not netPurchasesSales)
    Already in Crores — do not divide
    403 on homepage: Normal — cookies still set

T5  NSE F&O Ban List         ❌ FAIL
    All endpoints 404
    Feature deferred entirely

T6  Kite Plan                ✅ PASS
    ₹500/month (corrected from ₹2,000)
    Historical + OI confirmed accessible

T7  Kite OAuth               ✅ PASS
    Token expires midnight IST (not 6 AM)
    Redirect URL must match exactly
    request_token single-use

T8  Supabase                 ⏸️ PENDING
    Keepalive strategy designed

T9  Cloudflare Tunnel        ⏸️ PENDING
    Design confirmed — setup required

T10 Telegram Bot             ✅ PASS
    HTML mode confirmed
    1 msg/sec rate limit
    Silent/loud strategy confirmed
    @abhishek_mittal_trade_bot working
```

---

# SECTION 30: BUILD SEQUENCE (Phase 1)

## Week-by-Week

```
WEEK 1: Foundation
  Database migrations (all tables)
  system_config seed data
  sector_map.json
  Supabase connection + queries module
  Basic FastAPI app running

WEEK 2: Data Ingestion Layer
  Refactor 8 impl files into pipeline modules
  data_ingestion.py (all sources)
  data_validation.py
  lot_sizes weekly refresh job
  Telegram notifications working

WEEK 3: Pre-Processing
  level1_filter.py (earnings, ATR, liquidity)
  oi_series_builder.py (continuous OI + futures)
  market regime detection
  context_builder.py (Claude context assembly)

WEEK 4: Claude Integration
  claude_session.py (multi-turn manager)
  All 5 prompts implemented
  JSON output parsing + validation
  Session cost tracking

WEEK 5: Output Layer
  report_generator.py
  Telegram morning brief formatting
  Basic dashboard (Today screen)
  Setup detail screen
  Netlify deployment

WEEK 6: Portfolio & Paper Trading
  Kite OAuth flow + token storage
  positions + order history fetch
  paper_trade_engine.py (actual LTP)
  reconciliation.py (outcome tracking)

WEEK 7: Integration Testing
  Full dry run on historical date
  Prompt quality evaluation
  End-to-end test (Section 31)
  Fix issues found

WEEK 8: Go Live
  Cloudflare Tunnel Windows service
  NSSM service installation
  Sleep disabled on laptop
  First production run
  Monitor carefully for 2 weeks
```

---

# SECTION 31: TESTING STRATEGY

## Pre-Go-Live Dry Run
```
Feed real historical data for one trading day
Run complete pipeline (data → Claude → output)
Evaluate manually:
  Does Claude output valid JSON?
  Is conviction scoring reasonable?
  Is rationale genuinely useful?
  Does mentor explanation make sense?
  Are trade parameters correctly calculated?
Fix prompt issues before live trading
```

## Key Unit Tests
```
ATR dead zone detection (border cases)
Expiry selection (Tuesday, last week, transition)
Position sizing (edge cases — reject if unsizable)
Max pain calculation
OI continuous series builder (expiry transition)
Rollover phase detection
Market hours guard (reject during 9AM-4PM)
FII/DII field name (netValue not netPurchasesSales)
IV field name (impliedVolatility camelCase)
Token expiry midnight (not 6 AM)
```

## Key Integration Tests
```
Full Level 1 filter pipeline
Claude pre-scan JSON parsing
Ledger storage + retrieval
Paper trade entry/exit with actual LTP
Snapshot fallback to yesterday's IV
Bhavcopy retry sequence
Pipeline restart recovery from saved turns
```

---

# SECTION 32: SCALABILITY & ROADMAP

## Phase 2 (Month 3-4)
```
Config Console UI
Signal attribution tracking
Human-in-loop response buttons
Post-mortem generation
Directive system
Full dashboard (Performance, History screens)
Weekly debrief
Monthly calibration
```

## Phase 3 (Month 5-6)
```
Regime-aware prompt adjustment
Pattern curriculum tracker
Level 1 shadow tracking analysis
Filter evolution proposals
```

## Phase 4 (Month 7+)
```
Expand to Nifty 100
Options spread strategies
Two-way Telegram bot commands
Portfolio correlation management
```

---

# APPENDIX A: NIFTY 50 UNIVERSE

```
ADANIENT, ADANIPORTS, APOLLOHOSP, ASIANPAINT, AXISBANK,
BAJAJ-AUTO, BAJAJFINSV, BAJFINANCE, BHARTIARTL, BPCL,
BRITANNIA, CIPLA, COALINDIA, DIVISLAB, DRREDDY,
EICHERMOT, GRASIM, HCLTECH, HDFCBANK, HDFCLIFE,
HEROMOTOCO, HINDALCO, HINDUNILVR, ICICIBANK, ITC,
INDUSINDBK, INFY, JSWSTEEL, KOTAKBANK, LT,
LTIM, M&M, MARUTI, NESTLEIND, NTPC,
ONGC, POWERGRID, RELIANCE, SBILIFE, SBIN,
SHRIRAMFIN, SUNPHARMA, TATACONSUM, TATAMOTORS, TATASTEEL,
TCS, TECHM, TITAN, TRENT, ULTRACEMCO, WIPRO
```

---

# APPENDIX B: SECTOR MAP (sector_map.json)

```json
{
  "stocks": {
    "HDFCBANK":  {"sector": "BANKING",  "index": "NIFTY BANK"},
    "ICICIBANK": {"sector": "BANKING",  "index": "NIFTY BANK"},
    "KOTAKBANK": {"sector": "BANKING",  "index": "NIFTY BANK"},
    "AXISBANK":  {"sector": "BANKING",  "index": "NIFTY BANK"},
    "SBIN":      {"sector": "BANKING",  "index": "NIFTY BANK"},
    "INDUSINDBK":{"sector": "BANKING",  "index": "NIFTY BANK"},
    "TCS":       {"sector": "IT",       "index": "NIFTY IT"},
    "INFY":      {"sector": "IT",       "index": "NIFTY IT"},
    "WIPRO":     {"sector": "IT",       "index": "NIFTY IT"},
    "HCLTECH":   {"sector": "IT",       "index": "NIFTY IT"},
    "TECHM":     {"sector": "IT",       "index": "NIFTY IT"},
    "LTIM":      {"sector": "IT",       "index": "NIFTY IT"},
    "RELIANCE":  {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "ONGC":      {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "BPCL":      {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "NTPC":      {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "POWERGRID": {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "COALINDIA": {"sector": "ENERGY",   "index": "NIFTY ENERGY"},
    "MARUTI":    {"sector": "AUTO",     "index": "NIFTY AUTO"},
    "TATAMOTORS":{"sector": "AUTO",     "index": "NIFTY AUTO"},
    "M&M":       {"sector": "AUTO",     "index": "NIFTY AUTO"},
    "BAJAJ-AUTO":{"sector": "AUTO",     "index": "NIFTY AUTO"},
    "EICHERMOT": {"sector": "AUTO",     "index": "NIFTY AUTO"},
    "HEROMOTOCO":{"sector": "AUTO",     "index": "NIFTY AUTO"},
    "HINDUNILVR":{"sector": "FMCG",    "index": "NIFTY FMCG"},
    "ITC":       {"sector": "FMCG",    "index": "NIFTY FMCG"},
    "NESTLEIND": {"sector": "FMCG",    "index": "NIFTY FMCG"},
    "BRITANNIA": {"sector": "FMCG",    "index": "NIFTY FMCG"},
    "TATACONSUM":{"sector": "FMCG",    "index": "NIFTY FMCG"},
    "SUNPHARMA": {"sector": "PHARMA",  "index": "NIFTY PHARMA"},
    "DRREDDY":   {"sector": "PHARMA",  "index": "NIFTY PHARMA"},
    "CIPLA":     {"sector": "PHARMA",  "index": "NIFTY PHARMA"},
    "DIVISLAB":  {"sector": "PHARMA",  "index": "NIFTY PHARMA"},
    "APOLLOHOSP":{"sector": "PHARMA",  "index": "NIFTY PHARMA"},
    "BAJFINANCE":{"sector": "FINSERV", "index": "NIFTY FIN SERVICE"},
    "BAJAJFINSV":{"sector": "FINSERV", "index": "NIFTY FIN SERVICE"},
    "HDFCLIFE":  {"sector": "FINSERV", "index": "NIFTY FIN SERVICE"},
    "SBILIFE":   {"sector": "FINSERV", "index": "NIFTY FIN SERVICE"},
    "SHRIRAMFIN":{"sector": "FINSERV", "index": "NIFTY FIN SERVICE"},
    "TATASTEEL": {"sector": "METALS",  "index": "NIFTY METAL"},
    "JSWSTEEL":  {"sector": "METALS",  "index": "NIFTY METAL"},
    "HINDALCO":  {"sector": "METALS",  "index": "NIFTY METAL"},
    "ULTRACEMCO":{"sector": "CEMENT",  "index": "NIFTY INFRA"},
    "GRASIM":    {"sector": "CEMENT",  "index": "NIFTY INFRA"},
    "LT":        {"sector": "INFRA",   "index": "NIFTY INFRA"},
    "ADANIENT":  {"sector": "INFRA",   "index": "NIFTY INFRA"},
    "ADANIPORTS":{"sector": "INFRA",   "index": "NIFTY INFRA"},
    "TITAN":     {"sector": "CONSUMER","index": "NIFTY INDIA CONSUMPTION"},
    "TRENT":     {"sector": "CONSUMER","index": "NIFTY INDIA CONSUMPTION"},
    "ASIANPAINT":{"sector": "CONSUMER","index": "NIFTY INDIA CONSUMPTION"},
    "BHARTIARTL":{"sector": "TELECOM", "index": "NIFTY MEDIA"}
  },
  "holidays_2026": [
    "2026-01-26", "2026-03-25", "2026-04-02",
    "2026-04-14", "2026-04-15", "2026-05-01",
    "2026-08-15", "2026-10-02", "2026-10-24",
    "2026-11-05", "2026-11-20", "2026-12-25"
  ],
  "last_updated": "2026-01-01",
  "nifty50_reconstitution_dates": ["2026-06-30", "2026-12-31"]
}
```

---

# APPENDIX C: KEY FORMULAS

```
EMA(n)     = close.ewm(span=n, adjust=False).mean()
RSI(14)    = 100 - (100 / (1 + avg_gain/avg_loss))
ATR(14)    = ewm(true_range, span=14)
true_range = max(H-L, |H-prev_C|, |L-prev_C|)

Position sizing:
  max_risk     = capital × risk_pct
  risk_per_lot = (entry - stop_loss) × lot_size
  lots         = floor(max_risk / risk_per_lot)

RR ratio:
  rr = (target - entry) / (entry - stop_loss)

OI in lots:
  oi_lots = oi_shares / lot_size

Basis:
  basis     = futures_close - spot_close
  basis_pct = basis / spot_close × 100

Rollover %:
  rollover_pct = next_month_oi / (near_oi + next_oi) × 100
```

---

*Specification Version: 4.3 FINAL*
*Status: Ready For Phase 1 Implementation*
*Implementation files: C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer\*
*Pending: T8 (Supabase) + T9 (Cloudflare) — non-blocking*
*Build sequence: Section 30 — Week 1 starts with database setup*
