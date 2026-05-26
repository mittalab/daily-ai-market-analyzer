After every pipeline run, generate a file:
logs/session_cost_{YYYYMMDD}.json

Contents:
{
"session_id": "SESSION_20260525",
"session_date": "2026-05-25",
"regime": "SIDEWAYS_WIDE",

"turns": [
{
"turn_number": 1,
"turn_type": "market_context",
"symbol": null,
"input_tokens": 3241,
"output_tokens": 892,
"input_cost_usd": 0.0097,
"output_cost_usd": 0.0134,
"total_cost_usd": 0.0231
},
{
"turn_number": 2,
"turn_type": "prescan",
"symbol": null,
"input_tokens": 13981,
"output_tokens": 6421,
"input_cost_usd": 0.0419,
"output_cost_usd": 0.0963,
"total_cost_usd": 0.1382
},
{
"turn_number": 3,
"turn_type": "deep_analysis",
"symbol": "APOLLOHOSP",
"input_tokens": 8234,
"output_tokens": 2891,
"input_cost_usd": 0.0247,
"output_cost_usd": 0.0434,
"total_cost_usd": 0.0681
}
],

"totals": {
"total_input_tokens": 187234,
"total_output_tokens": 45621,
"total_cost_usd": 1.2341,
"total_cost_inr": 102.73,
"monthly_budget_usd": 60.00,
"monthly_spent_usd": 3.47,
"monthly_remaining_usd": 56.53,
"sessions_remaining_estimate": 45
},

"context_quality": {
"prescan_data_complete": true,
"deep_data_complete": true,
"oi_data_available": true,
"iv_data_available": false,
"fii_data_source": "LIVE",
"missing_data_flags": [
"IV unavailable — no snapshot (weekend)"
]
}
}

After pipeline completes add to notification:

💰 Session cost: $1.23 (₹102)
📊 Month to date: $3.47 of $60 budget (5.8%)
🔮 Est. sessions remaining: 45


Add to System Status screen:
┌────────────────────────────────────┐
│ 💰 API COST TRACKER                │
│                                    │
│ Today's session: $1.23 (₹102)     │
│ This month: $3.47 / $60.00        │
│ ████░░░░░░░░░░░░  5.8%            │
│                                    │
│ Breakdown today:                   │
│ Turn 1 Market Context:  $0.02     │
│ Turn 2 Pre-scan:        $0.14     │
│ Turn 3-10 Deep:         $0.98     │
│ Turn 11 Selection:      $0.09     │
│                                    │
│ Biggest cost driver:               │
│ Deep analysis (8 stocks)          │
│                                    │
│ Sessions left this month: ~45     │
└────────────────────────────────────┘