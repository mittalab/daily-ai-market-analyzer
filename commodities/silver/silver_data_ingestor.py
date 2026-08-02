import pytz
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
from fredapi import Fred
from kiteconnect import KiteConnect

# ── TIMEZONE SETTINGS ──────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

class SilverDataIngester:
    def __init__(self, kite_client: KiteConnect | None = None, fred_api_key: str | None = None):
        self.kite = kite_client
        self.fred = Fred(api_key=fred_api_key) if fred_api_key else None

    # ── 1. GLOBAL MACRO FETCH (yfinance & FRED) ─────────────────────────────
    def fetch_global_macro(self, days_back: int = 30) -> pd.DataFrame:
        """Fetches COMEX Silver, Gold, DXY, USDINR, and US10Y Yield from yfinance."""
        tickers = {
            "comex_silver": "SI=F",
            "comex_gold": "GC=F",
            "dxy": "DX-Y.NYB",
            "usdinr_spot": "USDINR=X",
            "us_10y_yield": "^TNX"
        }

        raw_data = yf.download(
            tickers=list(tickers.values()),
            period=f"{days_back}d",
            interval="1d",
            progress=False
        )['Close']

        # Rename columns to standardized internal names
        inv_tickers = {v: k for k, v in tickers.items()}
        raw_data.rename(columns=inv_tickers, inplace=True)
        raw_data.ffill(inplace=True)

        return raw_data

    def fetch_real_yields(self) -> float | None:
        """Fetch latest 10-Year US Real Inflation-Adjusted Yield from FRED API."""
        if not self.fred:
            return None
        try:
            dfii10 = self.fred.get_series('DFII10')
            return float(dfii10.iloc[-1])
        except Exception as e:
            print(f"[Warning] Failed to fetch FRED real yields: {e}")
            return None

    # ── 2. LOCAL MCX & CDS FETCH (Zerodha Kite) ──────────────────────────────
    def fetch_local_mcx(self, near_token: int, far_token: int, days_back: int = 30) -> dict:
        """Fetches near and far month MCX Silver OHLCV + OI data using Kite API."""
        if not self.kite:
            raise ValueError("KiteConnect client instance is required for local MCX fetching.")

        today = datetime.now(IST).date()
        from_date = today - timedelta(days=days_back)

        # Fetch OHLCV + OI for near month
        near_data = self.kite.historical_data(
            instrument_token=near_token,
            from_date=from_date,
            to_date=today,
            interval="day",
            oi=True
        )

        # Fetch OHLCV for far month (for Basis calculation)
        far_data = self.kite.historical_data(
            instrument_token=far_token,
            from_date=from_date,
            to_date=today,
            interval="day",
            oi=False
        )

        df_near = pd.DataFrame(near_data)
        df_far = pd.DataFrame(far_data)

        return {
            "near_df": df_near,
            "far_df": df_far
        }

    # ── 3. UNIFIED PAYLOAD GENERATOR ─────────────────────────────────────────
    def generate_unified_payload(
            self,
            mcx_near_token: int,
            mcx_far_token: int,
            expiry_date: datetime
    ) -> dict:
        """Combines global and local data streams into a structured JSON payload for Turn 1."""

        # 1. Fetch data
        global_df = self.fetch_global_macro(days_back=10)
        local_mcx = self.fetch_local_mcx(mcx_near_token, mcx_far_token, days_back=10)
        real_yield = self.fetch_real_yields()

        df_near = local_mcx["near_df"]
        df_far = local_mcx["far_df"]

        # 2. Extract latest candles
        latest_near = df_near.iloc[-1]
        prev_near = df_near.iloc[-2]
        latest_far = df_far.iloc[-1]
        latest_global = global_df.iloc[-1]
        prev_global = global_df.iloc[-2]

        # 3. Calculate derived metrics
        mcx_close = float(latest_near["close"])
        mcx_basis = float(latest_far["close"]) - mcx_close  # Contango (>0) or Backwardation (<0)

        # Gold/Silver Ratio (GSR) calculation
        gsr = float(latest_global["comex_gold"]) / float(latest_global["comex_silver"])

        # Days to Expiry and Tender Period safety calculation
        now_ist = datetime.now(IST).date()
        # dte = (expiry_date.date() - now_ist).days

        expiry = expiry_date.date() if hasattr(expiry_date, 'date') else expiry_date
        dte = (expiry - now_ist).days
        tender_cutoff_days = 5
        in_tender_warning = dte <= (tender_cutoff_days + 2)

        # Percentage changes
        comex_silver_change_pct = ((latest_global["comex_silver"] - prev_global["comex_silver"]) / prev_global["comex_silver"]) * 100
        mcx_silver_change_pct = ((mcx_close - float(prev_near["close"])) / float(prev_near["close"])) * 100
        dxy_change_pct = ((latest_global["dxy"] - prev_global["dxy"]) / prev_global["dxy"]) * 100

        # Currency spread variance (Is move driven by global price or local FX?)
        fx_divergence_note = "COMEX and MCX in sync."
        if abs(comex_silver_change_pct - mcx_silver_change_pct) > 0.75:
            fx_divergence_note = f"DIVERGENCE DETECTED: COMEX moved {comex_silver_change_pct:.2f}% while MCX moved {mcx_silver_change_pct:.2f}% (USDINR impact)."

        # Open interest handling (Kite API returns 'oi' key)
        near_oi = int(latest_near["oi"]) if "oi" in latest_near else int(latest_near.get("open_interest", 0))
        prev_oi = int(prev_near["oi"]) if "oi" in prev_near else int(prev_near.get("open_interest", 0))
        oi_change_pct = round(((near_oi - prev_oi) / prev_oi) * 100, 2) if prev_oi != 0 else 0.0

        # 4. Construct Final JSON
        payload = {
            "session_info": {
                "session_date": now_ist.strftime("%Y-%m-%d"),
                "ingested_at_ist": datetime.now(IST).isoformat(),
                "days_to_expiry": dte,
                "in_tender_warning": in_tender_warning,
            },
            "mcx_silver_structure": {
                "close": mcx_close,
                "open": float(latest_near["open"]),
                "high": float(latest_near["high"]),
                "low": float(latest_near["low"]),
                "volume": int(latest_near["volume"]),
                "open_interest": near_oi,
                "oi_change_pct": oi_change_pct,
                "futures_basis_spread": round(mcx_basis, 2),
                "basis_state": "CONTANGO" if mcx_basis > 0 else "BACKWARDATION"
            },
            "global_macro_context": {
                "comex_silver_close": float(latest_global["comex_silver"]),
                "comex_silver_change_pct": round(float(comex_silver_change_pct), 2),
                "dxy_close": float(latest_global["dxy"]),
                "dxy_change_pct": round(float(dxy_change_pct), 2),
                "us_10y_yield": float(latest_global["us_10y_yield"]),
                "us_10y_real_yield": real_yield,
                "usdinr_spot": float(latest_global["usdinr_spot"]),
            },
            "inter_asset_ratios": {
                "gold_silver_ratio": round(gsr, 2),
                "gsr_regime": "SILVER_OUTPERFORMING" if gsr < 80 else "GOLD_OUTPERFORMING" if gsr > 85 else "NEUTRAL_RANGE",
                "fx_divergence_note": fx_divergence_note
            }
        }

        return payload