import os
import json
import logging
import pytz
from datetime import datetime, timedelta
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
logger = logging.getLogger("DailySilverIngester")

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


class DailyHybridSilverIngester:
    def __init__(self, kite_client: KiteConnect, supabase_client: Client, fred_api_key: str | None = None):
        self.kite = kite_client
        self.supabase = supabase_client
        self.fred = Fred(api_key=fred_api_key) if fred_api_key else None

    def get_active_silver_contracts(self) -> list[dict]:
        """Dynamically retrieves top 3 active MCX Silver contracts (Near, Mid, Far)."""
        instruments = self.kite.instruments("MCX")
        silver_futs = sorted(
            [inst for inst in instruments if inst["name"] == "SILVER" and inst["segment"] == "MCX-FUT"],
            key=lambda x: x["expiry"]
        )
        if len(silver_futs) < 3:
            raise ValueError(f"Expected at least 3 active MCX Silver contracts, found {len(silver_futs)}.")
        return silver_futs[:3]

    def process_session(self):
        logger.info("Starting Midnight Hybrid Ingestion for MCX Silver...")
        contracts = self.get_active_silver_contracts()

        now_ist = datetime.now(IST)
        session_date = (now_ist - timedelta(days=1)).date() if now_ist.hour < 6 else now_ist.date()
        session_date_str = session_date.strftime("%Y-%m-%d")
        logger.info(f"Targeting Session Date: {session_date_str}")

        # ── 1. FETCH LOCAL MCX DATA (ALL 3 EXPIRIES) ─────────────────────────
        from_date = session_date - timedelta(days=220)
        contract_types = ["NEAR", "MID", "FAR"]
        contract_payloads = {}
        raw_db_rows = []
        contract_dfs = []

        for c_type, inst in zip(contract_types, contracts):
            raw = self.kite.historical_data(
                instrument_token=inst["instrument_token"],
                from_date=from_date,
                to_date=session_date,
                interval="day",
                oi=True
            )
            df = calculate_indicators(pd.DataFrame(raw))
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            contract_dfs.append(df)

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            expiry_date = inst["expiry"].date()
            dte = (expiry_date - session_date).days

            c_close = float(latest["close"])
            p_close = float(prev["close"])
            chg_pct = ((c_close - p_close) / p_close) * 100

            curr_oi = int(latest["open_interest"])
            prev_oi = int(prev["open_interest"])
            oi_chg_pct = ((curr_oi - prev_oi) / prev_oi) * 100 if prev_oi else 0.0

            # Row for Layer 1 Table (`silver_daily_ohlcv`)
            raw_db_rows.append({
                "session_date": session_date_str,
                "contract_type": c_type,
                "tradingsymbol": inst["tradingsymbol"],
                "expiry_date": expiry_date.strftime("%Y-%m-%d"),
                "days_to_expiry": dte,
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "close": c_close,
                "volume": int(latest["volume"]),
                "open_interest": curr_oi,
                "oi_change_pct": round(oi_chg_pct, 2),
                "change_pct": round(chg_pct, 2),
                "ema_20": round(float(latest["ema_20"]), 2),
                "ema_50": round(float(latest["ema_50"]), 2),
                "ema_180": round(float(latest["ema_180"]), 2),
                "atr_14": round(float(latest["atr_14"]), 2)
            })

            # Data for Layer 2 JSON Payload
            contract_payloads[f"{c_type.lower()}_contract"] = {
                "tradingsymbol": inst["tradingsymbol"],
                "expiry": expiry_date.strftime("%Y-%m-%d"),
                "days_to_expiry": dte,
                "in_tender_warning": dte <= 7,
                "close": c_close,
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "volume": int(latest["volume"]),
                "open_interest": curr_oi,
                "oi_change_pct": round(oi_chg_pct, 2),
                "change_pct": round(chg_pct, 2),
                "ema_20": round(float(latest["ema_20"]), 2),
                "ema_50": round(float(latest["ema_50"]), 2),
                "ema_180": round(float(latest["ema_180"]), 2),
                "atr_14": round(float(latest["atr_14"]), 2)
            }

        # ── 2. FETCH GLOBAL MACRO DATA ───────────────────────────────────────
        tickers = {
            "comex_silver": "SI=F",
            "comex_gold": "GC=F",
            "dxy": "DX-Y.NYB",
            "usdinr_spot": "USDINR=X",
            "us_10y_yield": "^TNX"
        }
        df_macro = yf.download(
            tickers=list(tickers.values()),
            period="10d",
            interval="1d",
            progress=False
        )["Close"].rename(columns={v: k for k, v in tickers.items()}).ffill()

        latest_macro = df_macro.iloc[-1]
        prev_macro = df_macro.iloc[-2]

        comex_close = float(latest_macro["comex_silver"])
        comex_prev = float(prev_macro["comex_silver"])
        comex_chg = ((comex_close - comex_prev) / comex_prev) * 100

        dxy_close = float(latest_macro["dxy"])
        dxy_prev = float(prev_macro["dxy"])
        dxy_chg = ((dxy_close - dxy_prev) / dxy_prev) * 100

        gsr = float(latest_macro["comex_gold"]) / comex_close

        real_yield = None
        if self.fred:
            try:
                series = self.fred.get_series("DFII10")
                real_yield = float(series.iloc[-1])
            except Exception as e:
                logger.warning(f"FRED fetch warning: {e}")

        macro_db_row = {
            "session_date": session_date_str,
            "comex_silver_close": comex_close,
            "comex_silver_change_pct": round(comex_chg, 2),
            "comex_gold_close": float(latest_macro["comex_gold"]),
            "dxy_close": dxy_close,
            "dxy_change_pct": round(dxy_chg, 2),
            "us_10y_yield": float(latest_macro["us_10y_yield"]),
            "us_10y_real_yield": real_yield,
            "usdinr_spot": float(latest_macro["usdinr_spot"]),
            "gold_silver_ratio": round(gsr, 2)
        }

        # ── 3. WRITE TO LAYER 1 (RAW RELATIONAL TABLES) ─────────────────────
        logger.info("Upserting raw atomic data to Layer 1 tables...")
        self.supabase.table("silver_daily_ohlcv").upsert(raw_db_rows, on_conflict="session_date,contract_type").execute()
        self.supabase.table("global_macro_daily").upsert(macro_db_row, on_conflict="session_date").execute()

        # ── 4. CONSTRUCT & WRITE TO LAYER 2 (IMMUTABLE AI PIPELINE) ─────────
        logger.info("Building frozen JSON payload for Layer 2 AI table...")

        near_close = contract_payloads["near_contract"]["close"]
        mid_close = contract_payloads["mid_contract"]["close"]
        far_close = contract_payloads["far_contract"]["close"]

        term_structure = {
            "near_to_mid_spread": round(mid_close - near_close, 2),
            "near_to_far_spread": round(far_close - near_close, 2),
            "curve_state": "CONTANGO" if (far_close - near_close) > 0 else "BACKWARDATION"
        }

        mcx_near_chg = contract_payloads["near_contract"]["change_pct"]
        fx_divergence = abs(comex_chg - mcx_near_chg) > 0.75

        turn_1_payload = {
            "mcx_term_structure": {
                "contracts": contract_payloads,
                "term_structure_analysis": term_structure
            },
            "global_macro": macro_db_row,
            "inter_asset_ratios": {
                "gold_silver_ratio": round(gsr, 2),
                "gsr_regime": "SILVER_OUTPERFORMING" if gsr < 80 else "GOLD_OUTPERFORMING" if gsr > 85 else "NEUTRAL_RANGE",
                "fx_divergence_detected": fx_divergence
            }
        }

        df_near = contract_dfs[0]
        hist_60d_mcx = df_near.tail(60).replace({np.nan: None}).to_dict(orient="records")

        turn_3_payload = {
            "instrument": "MCX_SILVER",
            "near_contract_symbol": contract_payloads["near_contract"]["tradingsymbol"],
            "mcx_historical_candles_60d": hist_60d_mcx
        }

        pipeline_record = {
            "session_date": session_date_str,
            "created_at": datetime.now(pytz.utc).isoformat(),
            "turn_1_payload": turn_1_payload,
            "turn_3_payload": turn_3_payload
        }

        self.supabase.table("silver_pipeline_data").upsert(pipeline_record, on_conflict="session_date").execute()
        logger.info(f"Successfully completed Hybrid Ingestion for {session_date_str} across Layer 1 and Layer 2!")


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

    ingester = DailyHybridSilverIngester(
        kite_client=kite,
        supabase_client=supabase,
        fred_api_key=fred_api_key
    )
    ingester.process_session()


if __name__ == "__main__":
    main()