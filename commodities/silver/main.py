import os
import json
import logging
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from supabase import create_client, Client

# Import the SilverDataIngester class from your module
from silver_data_ingestor import SilverDataIngester

# ── LOGGING SETUP ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("SilverIngesterMain")

IST = pytz.timezone("Asia/Kolkata")


# ── INSTRUMENT TOKEN RESOLVER ─────────────────────────────────────────────────
def get_mcx_silver_tokens(kite: KiteConnect) -> tuple[int, int, datetime]:
    """
    Dynamically fetches active MCX Silver near-month and far-month
    instrument tokens and contract expiry date from Zerodha Kite.
    """
    logger.info("Fetching MCX instruments list from Zerodha Kite...")
    instruments = kite.instruments("MCX")

    # Filter for active MCX Silver (Big Contract 30kg) futures
    silver_futs = [
        inst for inst in instruments
        if inst["name"] == "SILVER" and inst["segment"] == "MCX-FUT"
    ]

    # Sort contracts by expiry date ascending
    silver_futs = sorted(silver_futs, key=lambda x: x["expiry"])

    if len(silver_futs) < 2:
        raise ValueError("Could not locate active near and far month MCX Silver contracts.")

    near_contract = silver_futs[0]
    far_contract = silver_futs[1]

    near_token = near_contract["instrument_token"]
    far_token = far_contract["instrument_token"]
    expiry_date = near_contract["expiry"]  # datetime object

    logger.info(f"Active Near Contract: {near_contract['tradingsymbol']} (Token: {near_token}, Expiry: {expiry_date.strftime('%Y-%m-%d')})")
    logger.info(f"Active Far Contract:  {far_contract['tradingsymbol']} (Token: {far_token})")

    return near_token, far_token, expiry_date


# ── MAIN EXECUTION FUNCTION ───────────────────────────────────────────────────
def main():
    """
    Nightly MCX Silver Data Pipeline Orchestrator (Triggers at 12:15 AM IST).
    """
    logger.info("Starting MCX Silver Ingestion Pipeline...")

    # 1. Load environment configurations
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_API_SECRET")
    fred_api_key = os.getenv("FRED_API_KEY")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not api_key or not access_token:
        logger.error("KITE_API_KEY or KITE_API_SECRET environment variables missing.")
        return

    # 2. Initialize KiteConnect & Supabase Clients
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

    supabase: Client | None = None
    if supabase_url and supabase_key:
        try:
            supabase = create_client(supabase_url, supabase_key)
            logger.info("Supabase client initialized successfully.")
        except Exception as e:
            logger.warning(f"Supabase connection warning: {e}")

    # 3. Dynamically resolve contract tokens
    try:
        near_token, far_token, expiry_date = get_mcx_silver_tokens(kite)
    except Exception as e:
        logger.error(f"Failed to resolve contract tokens: {e}")
        return

    # 4. Instantiate Ingester and Generate Payload
    ingester = SilverDataIngester(kite_client=kite, fred_api_key=fred_api_key)

    try:
        logger.info("Fetching global macro feeds and local MCX market data...")
        payload = ingester.generate_unified_payload(
            mcx_near_token=near_token,
            mcx_far_token=far_token,
            expiry_date=expiry_date
        )

        # 5. Output formatted JSON to stdout
        json_output = json.dumps(payload, indent=2)
        logger.info("Successfully generated Silver Turn 1 Data Payload:")
        print("\n" + "="*60)
        print(json_output)
        print("="*60 + "\n")

    except Exception as e:
        logger.error(f"Error during data ingestion and payload generation: {e}", exc_info=True)
        return

    # 6. Save payload to Supabase database (Optional / Production)
    # if supabase:
    #     try:
    #         session_date = payload["session_info"]["session_date"]
    #         record = {
    #             "session_date": session_date,
    #             "created_at": datetime.now(pytz.utc).isoformat(),
    #             "data_payload": payload
    #         }
    #
    #         # Upsert into commodity_daily_data table
    #         response = supabase.table("commodity_daily_data").upsert(record, on_conflict="session_date").execute()
    #         logger.info(f"Successfully saved Turn 1 data payload for {session_date} into Supabase.")
    #     except Exception as e:
    #         logger.error(f"Failed to persist payload to Supabase: {e}")

    logger.info("MCX Silver Ingestion Pipeline completed successfully.")


if __name__ == "__main__":
    main()