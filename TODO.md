## High Level TODOs

1. When we swtich tabs in the front end dashboard, the manual stock analysis is gone, the last results should persists
2. In Manual analysis, the button , save to watchlist doesnt work
3. How is watchlist handled?
4. What all signals are used to do the detailed analysis of Nifty 50 stocks
5. Ensure that data for all the signals are fetched and used for manual other stocks


## Costing and Context Check for Claude API

Four additions needed:
1. Generate logs/session_cost_{YYYYMMDD}.json
   after every pipeline run
   Full turn-by-turn cost breakdown
   Plus context_quality section showing
   what data was and was not available

2. Add cost summary to pipeline complete
   Telegram notification:
   "$X.XX today | $X.XX month | X% of budget"

3. Add cost dashboard to System Status screen
   Turn-by-turn breakdown visible
   Monthly budget progress bar

4. Add context_quality flags to Telegram
   If any data was missing:
   "⚠️ Analysis ran with: IV unavailable,
   FII data from cache"

This gives us complete visibility into both cost and context quality daily.

## Daily Routine and Partnering with Claude to ensure the quality

1. Ask what all need to be passed and how the generation of it can be automated

SHARE WITH ME WEEKLY:
1. session_cost JSON files (once Fix 7 is done)
2. trade_setups SQL query results
3. orchestrator.py flow confirmation (Fix 1)

I WILL TRACK:
Whether position sizing is now Python-verified
Hit rate of paper trades week by week
Cost per session trend

BEFORE REAL MONEY:
Position sizing Fix 2 must be complete
3-4 weeks of paper trading
Hit rate > 65% in actual market conditions\




YOUR DAILY ACTION (5 minutes):
Read morning brief
Note if anything feels wrong
Check paper trade outcomes

WEEKLY TUESDAY SHARE WITH ME:
session_cost files for the week
trade_setups table (SQL query result)
Screenshot of any interesting setups
Your honest gut feel on the calls

I WILL TELL YOU:
Hit rate progress toward 80%
Which signals are proving reliable
Context quality issues in analysis
When to trust vs question the system
Prompt adjustments needed
Phase 2 priorities based on real data

MILESTONE REVIEW (Month 2):
If hit rate > 70%: consider small real trades
If hit rate 60-70%: continue paper trading
If hit rate < 60%: prompt engineering review

I will give you honest assessment
Not just optimistic encouragement