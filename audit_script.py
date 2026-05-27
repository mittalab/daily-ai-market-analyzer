import os
from dotenv import load_dotenv
from supabase import create_client
import pandas as pd
import datetime

load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_KEY")
client = create_client(url, key)

tables = [
    'trade_setups', 'analysis_sessions', 'session_claude_turns', 
    'options_snapshots', 'continuous_oi_series', 'futures_continuous_series', 
    'price_history', 'fii_dii_flows', 'lot_sizes', 'kite_tokens', 
    'watchlist_staging', 'level1_shadow_tracks', 'system_config'
]

def fetch_all(table_name, columns='*'):
    data = []
    offset = 0
    limit = 1000
    while True:
        response = client.table(table_name).select(columns).range(offset, offset + limit - 1).execute()
        if not response.data:
            break
        data.extend(response.data)
        if len(response.data) < limit:
            break
        offset += limit
    return pd.DataFrame(data)

print("--- Q1: All tables with row counts ---")
for t in tables:
    try:
        count = client.table(t).select('*', count='exact').limit(1).execute().count
        print(f"{t}: {count}")
    except Exception as e:
        print(f"{t}: Error {e}")

print("\n--- Q2: Price history coverage per symbol ---")
df_ph = fetch_all('price_history')
if not df_ph.empty:
    df_ph['date'] = pd.to_datetime(df_ph['date'])
    summary = df_ph.groupby('symbol').agg(
        trading_days=('date', 'count'),
        earliest=('date', 'min'),
        latest=('date', 'max'),
        null_closes=('close', lambda x: x.isnull().sum()),
        zero_volume_days=('volume', lambda x: (x == 0).sum())
    ).reset_index()
    summary['calendar_span'] = (summary['latest'] - summary['earliest']).dt.days
    summary['ema200_status'] = summary['trading_days'].apply(
        lambda x: '✅ EMA200 ready' if x >= 200 else ('⚠️ EMA200 unavailable' if x >= 50 else '❌ Insufficient history')
    )
    summary = summary.sort_values('trading_days')
    print(summary.head(10).to_string())
    print("... (truncated)")

print("\n--- Q3: Sector index coverage specifically ---")
if not df_ph.empty:
    sector_indices = [
        'NIFTY_50','INDIA_VIX','NIFTY_BANK','NIFTY_IT','NIFTY_AUTO',
        'NIFTY_PHARMA','NIFTY_FMCG','NIFTY_METAL','NIFTY_ENERGY','NIFTY_FIN_SERVICE'
    ]
    df_sectors = df_ph[df_ph['symbol'].isin(sector_indices)]
    if not df_sectors.empty:
        summary_sec = df_sectors.groupby('symbol').agg(
            days=('date', 'count'), earliest=('date', 'min'), latest=('date', 'max')
        ).reset_index()
        print(summary_sec.to_string())
    else:
        print("No sector indices found in price_history.")

print("\n--- Q4: Price history gaps detection ---")
if not df_ph.empty:
    recent = df_ph[df_ph['date'] > (datetime.datetime.now() - datetime.timedelta(days=30))]
    recent = recent.sort_values(['symbol', 'date'])
    recent['prev_date'] = recent.groupby('symbol')['date'].shift(1)
    recent['gap_days'] = (recent['date'] - recent['prev_date']).dt.days
    gaps = recent[recent['gap_days'] > 3]
    print(gaps[['symbol', 'prev_date', 'date', 'gap_days']].head(10).to_string())

print("\n--- Q5: Options snapshots health ---")
df_opt = fetch_all('options_snapshots')
if not df_opt.empty:
    df_opt['snapshot_date'] = pd.to_datetime(df_opt['snapshot_date'])
    df_opt['iv_num'] = pd.to_numeric(df_opt['iv'], errors='coerce')
    df_opt['oi_num'] = pd.to_numeric(df_opt['oi'], errors='coerce')
    summ_opt = df_opt.groupby('snapshot_date').agg(
        symbols_covered=('symbol', 'nunique'),
        total_rows=('symbol', 'count'),
        rows_with_iv=('iv_num', lambda x: (x > 0).sum()),
        rows_without_iv=('iv_num', lambda x: (x.isnull() | (x == 0)).sum()),
        rows_with_oi=('oi_num', lambda x: (x > 0).sum())
    ).reset_index()
    summ_opt['iv_coverage_pct'] = (100 * summ_opt['rows_with_iv'] / summ_opt['total_rows']).round(1)
    summ_opt = summ_opt.sort_values('snapshot_date', ascending=False).head(10)
    print(summ_opt.to_string())

print("\n--- Q6: Continuous OI series health ---")
df_oi = fetch_all('continuous_oi_series')
if not df_oi.empty:
    df_oi['date'] = pd.to_datetime(df_oi['date'])
    summ_oi = df_oi.groupby('date').agg(
        symbols=('symbol', 'nunique'),
        with_oi=('near_month_oi', lambda x: x.notnull().sum()),
        with_pcr=('pcr_near', lambda x: x.notnull().sum()),
        with_maxpain=('max_pain', lambda x: x.notnull().sum()),
        with_rollover=('rollover_pct', lambda x: x.notnull().sum()),
        expiry_flags=('is_expiry_day', lambda x: (x == True).sum()),
        phases_present=('rollover_phase', lambda x: ', '.join(x.dropna().unique()))
    ).reset_index().sort_values('date', ascending=False).head(10)
    print(summ_oi.to_string())

print("\n--- Q7: Futures series health ---")
df_fut = fetch_all('futures_continuous_series')
if not df_fut.empty:
    df_fut['date'] = pd.to_datetime(df_fut['date'])
    summ_fut = df_fut.groupby('date').agg(
        symbols=('symbol', 'nunique'),
        has_close=('futures_price', lambda x: x.notnull().sum()),
        has_open=('futures_open', lambda x: x.notnull().sum() if 'futures_open' in x.index else 0),
        has_high=('futures_high', lambda x: x.notnull().sum() if 'futures_high' in x.index else 0),
        has_low=('futures_low', lambda x: x.notnull().sum() if 'futures_low' in x.index else 0),
        has_volume=('futures_volume', lambda x: x.notnull().sum() if 'futures_volume' in x.index else 0),
        has_basis=('basis', lambda x: x.notnull().sum()),
        has_oi=('near_month_oi', lambda x: x.notnull().sum())
    ).reset_index().sort_values('date', ascending=False).head(10)
    print(summ_fut.to_string())

print("\n--- Q8: FII/DII data health ---")
df_fii = fetch_all('fii_dii_flows')
if not df_fii.empty:
    df_fii['date'] = pd.to_datetime(df_fii['date'])
    df_fii['status'] = df_fii['fii_net_cr'].apply(lambda x: '❌ Missing' if pd.isnull(x) else '✅ OK')
    print(df_fii[['date', 'fii_net_cr', 'dii_net_cr', 'status']].sort_values('date', ascending=False).head(15).to_string())

print("\n--- Q9: All pipeline sessions ---")
df_sess = fetch_all('analysis_sessions')
df_turns = fetch_all('session_claude_turns')
if not df_sess.empty:
    print(df_sess[['session_id', 'session_date', 'status', 'trade_ready_count']].to_string())
if not df_turns.empty:
    print("\n--- Q10: Claude turns detail ---")
    df_turns['input_saved'] = df_turns['input_text'].apply(lambda x: f"✅ YES ({len(x)} chars)" if isinstance(x, str) else "❌ NO")
    df_turns['output_saved'] = df_turns['output_text'].apply(lambda x: f"✅ YES ({len(x)} chars)" if isinstance(x, str) else "❌ NO")
    print(df_turns[['session_id', 'turn_number', 'turn_type', 'symbol', 'input_tokens', 'output_tokens', 'input_saved', 'output_saved']].head(10).to_string())

print("\n--- Q11: Trade setups ledger ---")
df_trades = fetch_all('trade_setups')
if not df_trades.empty:
    print(df_trades[['symbol', 'direction', 'stage', 'setup_date', 'paper_outcome']].head(10).to_string())

print("\n--- Q13: System config ---")
df_conf = fetch_all('system_config')
if not df_conf.empty:
    df_conf['status'] = df_conf['value'].apply(lambda x: '❌ MISSING' if pd.isnull(x) or x == '' else '✅')
    print(df_conf[['key', 'value', 'status']].to_string())

print("\n--- Q16: Lot sizes ---")
df_lots = fetch_all('lot_sizes')
if not df_lots.empty:
    print(df_lots.head(10).to_string())

print("\n--- Q17: Kite token status ---")
df_tokens = fetch_all('kite_tokens')
if not df_tokens.empty:
    print(df_tokens.to_string())

print("\n--- Q19: Watchlist staging ---")
df_watch = fetch_all('watchlist_staging')
if not df_watch.empty:
    print(df_watch[['symbol', 'current_stage', 'days_in_stage']].head(10).to_string())

print("\n--- Q20: Shadow tracks ---")
df_shadow = fetch_all('level1_shadow_tracks')
if not df_shadow.empty:
    print(df_shadow.head(10).to_string())
