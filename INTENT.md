# Multi-Turn AI Trading Session

## Design Intent & Mental Model

# What We Are Building

A nightly AI-powered trading mentor that analyses the Indian F&O market after market close and delivers actionable swing trade recommendations before the next market open.

The AI Agent is **not a rule engine**. It is the analytical brain of the system. It receives real market data and applies genuine trading judgment, just as a senior hedge fund analyst would when reviewing charts and market data after trading hours.

The session follows a **progressive funnel**. Each turn narrows the focus until only the highest-conviction opportunities remain.

By the end of the session, the AI Agent has deeply analysed the most promising setups and produced final trade recommendations with complete reasoning.

---

# Core Philosophy

The AI Agent should behave like an experienced Indian F&O trader who:

* Understands Indian market microstructure
* Reads institutional behaviour through Open Interest and FII/DII flows
* Thinks in terms of **risk-reward**, not merely direction
* Is conservative—a **SKIP** is always an acceptable outcome
* Acts as both a trader and a mentor
* Explains *why* a trade exists
* Clearly communicates what could invalidate the setup

The system is designed for **paper trading first**.

Real capital should only be deployed after approximately three months of validated performance.

The AI Agent earns trust through **consistency**, not confidence.

---

# Session Structure

```
Market Context
        ↓
Universe Pre-Scan
        ↓
Deep Analysis
        ↓
Trade Recommendation / Watch / Skip
```

Each stage progressively reduces the number of stocks requiring detailed analysis.

---

# Turn 1 — Market Context

## Purpose

Before analysing individual stocks, an experienced trader first asks:

* What type of market am I trading tonight?
* Should I be aggressive or defensive?
* What is institutional money doing?
* Which market levels matter most?

Turn 1 answers these questions.

It represents the process of reading the tape, checking FII positioning, reviewing the index, and forming an opinion on the overall market before opening any stock chart.

---

## Expected Output

The AI Agent should produce a **coherent market narrative**, not merely a collection of observations.

The distinction between a junior analyst and a senior analyst is interpretation.

A junior analyst lists facts.

A senior analyst explains **what those facts collectively imply**.

---

## Why It Matters

The market narrative becomes the filter for every subsequent decision.

For example:

> If Turn 1 concludes that the market is in a distribution phase with aggressive FII selling, then bullish stock setups require substantially higher conviction than they would during a strong uptrend.

---

## Quality Bar

A successful Turn 1 should make an experienced trader think:

> "Yes, that perfectly captures today's market."

It should identify:

* the dominant market theme
* institutional behaviour
* the most important risk to monitor tomorrow

It should **not** be a generic summary of market statistics.

---

# Turn 2 — Universe Pre-Scan

## Purpose

An experienced trader does not spend twenty minutes analysing every stock.

Instead, they perform a rapid scan.

Within seconds they decide:

* Interesting → Analyse further
* Nothing here → Skip

Turn 2 reproduces this behaviour across the filtered universe.

---

## Objective

The AI Agent reviews every stock that has already passed the mechanical filters (liquidity, ATR, earnings, etc.) and determines which deserve deeper analysis.

The objective is to reduce the universe to roughly:

**8–15 candidates**

---

## Important Principle

This stage is **not** about finding trade-ready setups.

It is about recognising **potential**.

Examples include:

* Institutional accumulation beginning
* Breakout structures developing
* Unusual volume
* Sector leadership emerging
* OI positioning becoming interesting

Likewise, stocks should be confidently skipped if they exhibit:

* Flat momentum
* Directionless price action
* Neutral Open Interest
* No meaningful edge

---

## Quality Bar

Approximately **80% of stocks should be skipped.**

Passing too many stocks into deep analysis wastes computation and dilutes recommendation quality.

---

# Turn 3+ — Deep Analysis

## Purpose

This is the stage where genuine trading work begins.

The AI Agent performs a complete analysis of one stock at a time using:

* Price action
* Technical indicators
* Futures Open Interest
* Options chain positioning
* Sector behaviour
* Market regime
* Historical context
* Previous watchlist status

Each analysis must end with either:

* Trade Ready
* Watch
* Skip

---

# Conviction Framework

Every setup is evaluated across five dimensions.

The scoring is **not mechanical**.

The AI Agent should adjust weighting depending on the type of opportunity.

---

## 1. Price Structure

Questions:

* Is the chart telling a clear story?
* Is there a recognisable pattern?
* Is support or resistance well defined?

Examples:

* Breakout
* Flag
* Trend continuation
* Pullback
* Support retest

Clean structures receive higher scores.

Messy charts receive lower scores regardless of other indicators.

---

## 2. Momentum & Volume

Questions:

* Is participation increasing?
* Does volume confirm price?
* Is Futures OI supporting the move?

Examples:

Positive:

* Rising price + rising OI
* Strong breakout volume

Negative:

* Weak volume breakout
* Falling OI during rally

---

## 3. Market Alignment

Questions:

* Is the broader market helping or opposing the trade?
* Does the current market regime favour this setup?

Inputs include:

* Index trend
* VIX
* Index Open Interest walls
* Institutional positioning

---

## 4. Stock F&O Positioning

Questions:

* What are institutions doing in this stock?

Signals include:

* Futures OI buildup
* PCR
* Futures premium
* Strike concentration
* Call/Put writing

---

## 5. Sector Context

Questions:

* Is the sector leading or lagging?
* Is the stock outperforming peers?
* Are institutional flows supporting this sector?

---

# Trade Construction

When recommending a trade, the AI Agent must provide:

* Exact entry
* Exact stop loss
* Profit target(s)
* Suggested option strike
* Entry premium
* Risk-reward ratio

A minimum **1:2 Risk-Reward** is mandatory.

If such a trade cannot be constructed, the recommendation must be:

> **SKIP**

---

# Position Sizing

Position sizing is ultimately enforced by Python.

The AI Agent should estimate reasonable sizing based on conviction, but mathematical precision is handled separately.

---

# Mentor Role

Every deep analysis should include three educational sections.

## 1. Rationale

Explain **why** the recommendation exists.

---

## 2. Mentor Notes

Teach the trader what they should observe.

The explanation should make someone think:

> "I wouldn't have noticed that, but now I understand why it matters."

---

## 3. Risks

Explain what could invalidate the trade.

Examples:

* Failed breakout
* Market weakness
* Sector rotation
* Unexpected FII selling
* Options positioning changing

Honest risk discussion builds trust.

---

# Quality Bar

A successful deep analysis should be credible enough that an experienced F&O trader would respect the reasoning, even if they disagree with some specifics.

A poor analysis simply describes indicators without producing genuine insight.

If the reasoning could apply to any stock on any day, it is not sufficiently specific.

---

# Watchlist Lifecycle

The system maintains memory across trading sessions.

A stock does **not** receive only one opportunity.

Possible lifecycle:

```
Developing Setup
        ↓
Watch
        ↓
Trade Ready
        ↓
Telegram Alert
```

or

```
Watch
      ↓
Weakens
      ↓
Removed
```

---

## Rules

* Conviction around **65** → Add to Watchlist
* Conviction **75+** → Trade Ready
* Watchlist stocks receive priority re-analysis each night
* Watchlist entries automatically expire after approximately **10 trading sessions** if no trigger occurs

---

## Consistency Requirement

Reasoning must remain consistent across sessions.

Example:

If today's analysis concludes:

> "A bullish flag is still developing."

Tomorrow's analysis should determine whether the flag:

* continued developing
* broke out successfully
* failed

rather than treating the stock as an entirely new analysis.

The AI Agent should build on previous observations rather than restarting from scratch.
