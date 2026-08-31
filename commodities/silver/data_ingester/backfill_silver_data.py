import os
import logging
import pytz
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np
import yfinance as yf
from fredapi import Fred
from kiteconnect import KiteConnect
from dotenv import load_dotenv
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("HybridBackfillEngine")

IST = pytz.timezone("Asia/Kolkata")


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates EMA 20, 50, 180 and ATR 14 on daily OHLCV DataFrame."""
    df = df.copy()
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_180"] = df["close"].ewm(span=180, adjust=False).mean()

    df["prev_close"] = df["close"].shift(1)
    df["tr0"] = df["high"] - df["low"]
    df["tr1"] = (df["high"] - df["prev_close"]).abs()
    df["tr2"] = (df["low"] - df["prev_close"]).abs()
    df["tr"] = df[["tr0", "tr1", "tr2"]].max(axis=1)
    df["atr_14"] = df["tr"].rolling(window=14).mean()

    df.drop(columns=["prev_close", "tr0", "tr1", "tr2", "tr"], inplace=True)
    return df


class HybridBackfillEngine:
    def __init__(self, kite_client: KiteConnect, supabase_client: Client, fred_api_key: str | None = None):
        self.kite = kite_client
        self.supabase = supabase_client
        self.fred = Fred(api_key=fred_api_key) if fred_api_key else None

    def get_existing_dates_in_db(self) -> set[str]:
        try:
            res = self.supabase.table("silver_pipeline_data").select("session_date").execute()
            return {row["session_date"] for row in res.data}
        except Exception as e:
            logger.error(f"Error fetching existing dates: {e}")
            return set()

    def get_active_silver_contracts(self) -> list[dict]:
        instruments = self.kite.instruments("MCX")
        silver_futs = sorted(
            [inst for inst in instruments if inst["name"] == "SILVER" and inst["segment"] == "MCX-FUT"],
            key=lambda x: x["expiry"]
        )
        print(silver_futs)
        if len(silver_futs) < 3:
            raise ValueError("Could not locate 3 active MCX Silver contracts.")
        return silver_futs[:3]

    def run_backfill(self, target_lookback_days: int = 180):
        existing_dates = self.get_existing_dates_in_db()
        contracts = self.get_active_silver_contracts()

        today = datetime.now(IST).date()
        from_date = today - timedelta(days=target_lookback_days)

        logger.info(f"Fetching historical daily candles for 3 expiries ({from_date} to {today})...")
        dfs = []
        for inst in contracts:
            raw = self.kite.historical_data(
                instrument_token=inst["instrument_token"],
                from_date=from_date,
                to_date=today,
                interval="day",
                oi=True
            )
            print("Kite data")
            print(raw)
            df = calculate_indicators(pd.DataFrame(raw))
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            dfs.append(df)

        df_near, df_mid, df_far = dfs[0], dfs[1], dfs[2]

        # Fetch Global Macro
        tickers = {
            "comex_silver": "SI=F",
            "comex_gold": "GC=F",
            "dxy": "DX-Y.NYB",
            "usdinr_spot": "USDINR=X",
            "us_10y_yield": "^TNX"
        }
        df_macro = yf.download(
            tickers=list(tickers.values()),
            start=from_date.strftime("%Y-%m-%d"),
            end=(today + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d",
            progress=False
        )

        df_macro_close = df_macro["Close"].rename(columns={v: k for k, v in tickers.items()}).ffill()
        df_macro_close.index = df_macro_close.index.strftime("%Y-%m-%d")

        real_yield_series = None
        if self.fred:
            try:
                real_yield_series = self.fred.get_series("DFII10", observation_start=from_date.strftime("%Y-%m-%d"))
                real_yield_series.index = real_yield_series.index.strftime("%Y-%m-%d")
            except Exception as e:
                logger.warning(f"Failed to fetch FRED series: {e}")

        cutoff_date = (today - timedelta(days=target_lookback_days)).strftime("%Y-%m-%d")
        valid_dates = df_near[df_near["date"] >= cutoff_date]["date"].tolist()

        logger.info(f"Targeting {len(valid_dates)} trading dates for Hybrid Backfill...")
        inserted_count = 0

        for date_str in valid_dates:
            if date_str in existing_dates:
                continue

            sub_near = df_near[df_near["date"] <= date_str]
            sub_mid = df_mid[df_mid["date"] <= date_str]
            sub_far = df_far[df_far["date"] <= date_str]

            if len(sub_near) < 2 or sub_mid.empty or sub_far.empty:
                continue

            n_curr, n_prev = sub_near.iloc[-1], sub_near.iloc[-2]
            m_curr, m_prev = sub_mid.iloc[-1], (sub_mid.iloc[-2] if len(sub_mid) > 1 else sub_mid.iloc[-1])
            f_curr, f_prev = sub_far.iloc[-1], (sub_far.iloc[-2] if len(sub_far) > 1 else sub_far.iloc[-1])

            macro_sub = df_macro_close[df_macro_close.index <= date_str]
            if macro_sub.empty:
                continue
            curr_macro = macro_sub.iloc[-1]
            prev_macro = macro_sub.iloc[-2] if len(macro_sub) > 1 else curr_macro

            # Calculations
            near_close, mid_close, far_close = float(n_curr["close"]), float(m_curr["close"]), float(f_curr["close"])
            comex_close = float(curr_macro["comex_silver"])
            comex_prev = float(prev_macro["comex_silver"])
            comex_chg = ((comex_close - comex_prev) / comex_prev) * 100 if comex_prev else 0.0

            mcx_near_prev = float(n_prev["close"])
            mcx_near_chg = ((near_close - mcx_near_prev) / mcx_near_prev) * 100 if mcx_near_prev else 0.0

            gsr = float(curr_macro["comex_gold"]) / comex_close if comex_close else 0.0
            real_yield_val = float(real_yield_series.loc[date_str]) if (real_yield_series is not None and date_str in real_yield_series.index) else None

            # ── BUILD LAYER 1 ROWS ───────────────────────────────────────────
            raw_db_rows = []
            for c_type, inst, curr_row, prev_row in zip(
                    ["NEAR", "MID", "FAR"],
                    contracts,
                    [n_curr, m_curr, f_curr],
                    [n_prev, m_prev, f_prev]
            ):
                print(c_type)
                print(inst)
                print(curr_row)
                print(prev_row)
                c_c = float(curr_row["close"])
                p_c = float(prev_row["close"])
                c_chg = ((c_c - p_c) / p_c) * 100 if p_c else 0.0
                c_oi = int(curr_row["oi"])
                p_oi = int(prev_row["oi"])
                oi_chg = ((c_oi - p_oi) / p_oi) * 100 if p_oi else 0.0
                exp = inst["expiry"]
                exp_dt = exp.date() if isinstance(exp, datetime) else exp

                atr_val = curr_row.get("atr_14")
                raw_db_rows.append({
                    "session_date": date_str,
                    "contract_type": c_type,
                    "tradingsymbol": inst["tradingsymbol"],
                    "expiry_date": exp_dt.strftime("%Y-%m-%d"),
                    "days_to_expiry": (exp_dt - datetime.strptime(date_str, "%Y-%m-%d").date()).days,
                    "open": float(curr_row["open"]),
                    "high": float(curr_row["high"]),
                    "low": float(curr_row["low"]),
                    "close": c_c,
                    "volume": int(curr_row["volume"]),
                    "open_interest": c_oi,
                    "oi_change_pct": round(oi_chg, 2),
                    "change_pct": round(c_chg, 2),
                    "ema_20": round(float(curr_row["ema_20"]), 2),
                    "ema_50": round(float(curr_row["ema_50"]), 2),
                    "ema_180": round(float(curr_row["ema_180"]), 2),
                    "atr_14": round(float(atr_val), 2) if pd.notna(atr_val) else None
                })

            us_10y_yield_val = curr_macro.get("us_10y_yield")
            macro_row = {
                "session_date": date_str,
                "comex_silver_close": comex_close,
                "comex_silver_change_pct": round(comex_chg, 2),
                "comex_gold_close": float(curr_macro["comex_gold"]),
                "dxy_close": float(curr_macro["dxy"]),
                "dxy_change_pct": round(((float(curr_macro["dxy"]) - float(prev_macro["dxy"])) / float(prev_macro["dxy"])) * 100, 2),
                "us_10y_yield": float(us_10y_yield_val) if pd.notna(us_10y_yield_val) else None,
                "us_10y_real_yield": real_yield_val if pd.notna(real_yield_val) else None,
                "usdinr_spot": float(curr_macro["usdinr_spot"]),
                "gold_silver_ratio": round(gsr, 2)
            }

            # ── BUILD LAYER 2 PAYLOAD ────────────────────────────────────────
            contracts_payload = {
                "near_contract": raw_db_rows[0],
                "mid_contract": raw_db_rows[1],
                "far_contract": raw_db_rows[2]
            }

            turn_1_payload = {
                "mcx_term_structure": {
                    "contracts": contracts_payload,
                    "term_structure_analysis": {
                        "near_to_mid_spread": round(mid_close - near_close, 2),
                        "near_to_far_spread": round(far_close - near_close, 2),
                        "curve_state": "CONTANGO" if (far_close - near_close) > 0 else "BACKWARDATION"
                    }
                },
                "global_macro": macro_row,
                "inter_asset_ratios": {
                    "gold_silver_ratio": round(gsr, 2),
                    "gsr_regime": "SILVER_OUTPERFORMING" if gsr < 80 else "GOLD_OUTPERFORMING" if gsr > 85 else "NEUTRAL_RANGE",
                    "fx_divergence_detected": abs(comex_chg - mcx_near_chg) > 0.75
                }
            }

            hist_60d = sub_near.tail(60).replace({np.nan: None}).to_dict(orient="records")
            turn_3_payload = {
                "instrument": "MCX_SILVER",
                "near_contract_symbol": contracts[0]["tradingsymbol"],
                "mcx_historical_candles_60d": hist_60d
            }

            # Write Layer 1 & Layer 2
            print("Writing DB rows")
            print(raw_db_rows)
            self.supabase.table("silver_daily_ohlcv").upsert(raw_db_rows, on_conflict="session_date,contract_type").execute()
            print(macro_row)
            self.supabase.table("global_macro_daily").upsert(macro_row, on_conflict="session_date").execute()

            pipeline_record = {
                "session_date": date_str,
                "created_at": datetime.now(pytz.utc).isoformat(),
                "turn_1_payload": turn_1_payload,
                "turn_3_payload": turn_3_payload
            }
            self.supabase.table("silver_pipeline_data").upsert(pipeline_record, on_conflict="session_date").execute()

            inserted_count += 1
            logger.info(f"[{inserted_count}] Backfilled hybrid dataset for {date_str}")

        logger.info(f"Backfill complete! Populated {inserted_count} historical session records across Layer 1 & 2.")


def main():
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_API_SECRET")
    fred_api_key = os.getenv("FRED_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not all([api_key, access_token, supabase_url, supabase_key]):
        logger.error("Missing required environment variables.")
        return

    try:
        logger.info("KiteConnect client initialized successfully.")
        from new_integration.kite_positions import get_kite_token, get_kite
        token_row = get_kite_token()
        if not token_row:
            raise RuntimeError(
                "No Kite access token in DB — run the OAuth flow first"
            )

        kite = get_kite(token_row["access_token"])
    except Exception as e:
        logger.error(f"Failed to initialize KiteConnect client: {e}")
        return

    supabase = create_client(supabase_url, supabase_key)

    backfill_engine = HybridBackfillEngine(
        kite_client=kite,
        supabase_client=supabase,
        fred_api_key=fred_api_key
    )
    backfill_engine.run_backfill(target_lookback_days=180)


if __name__ == "__main__":
    main()