"""
Kite Connect OAuth — production token management.

Redirect URL: https://api.abhishekmittal.in/kite/callback
Daily flow: open https://api.abhishekmittal.in/kite/refresh in browser.

Token stored in Supabase kite_tokens table.
Token expires at midnight IST.
Validation: kite.profile() — run once at pre-flight, not per call.

Credentials from .env: KITE_API_KEY, KITE_API_SECRET
"""
import logging
import os
from datetime import datetime, timezone

import pytz
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()
logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

REDIRECT_URL = "https://api.abhishekmittal.in/kite/callback"


def _get_api_key() -> str:
    key = os.getenv("KITE_API_KEY")
    if not key:
        raise RuntimeError("KITE_API_KEY not set in .env")
    return key


def _get_api_secret() -> str:
    secret = os.getenv("KITE_API_SECRET")
    if not secret:
        raise RuntimeError("KITE_API_SECRET not set in .env")
    return secret


def get_login_url() -> str:
    """Generate the Zerodha login URL for the daily OAuth flow."""
    kite = KiteConnect(api_key=_get_api_key())
    kite.redirect_url = REDIRECT_URL
    url = kite.login_url()
    logger.info("Kite login URL generated")
    return url


def exchange_request_token(request_token: str) -> str:
    """Exchange a request_token for an access_token and store it."""
    from database.queries import upsert_kite_token

    kite = KiteConnect(api_key=_get_api_key())
    session = kite.generate_session(request_token, api_secret=_get_api_secret())

    access_token = session["access_token"]
    generated_at = datetime.now(timezone.utc)

    now_ist    = datetime.now(IST)
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_ist >= midnight_ist:
        from datetime import timedelta
        midnight_ist = midnight_ist + timedelta(days=1)

    expires_at = midnight_ist.astimezone(timezone.utc)

    upsert_kite_token(access_token, generated_at, expires_at)
    logger.info(
        "Kite token exchanged and stored. User: %s. Expires: %s IST",
        session.get("user_id"),
        midnight_ist.strftime("%Y-%m-%d %H:%M"),
    )
    return access_token


def get_authenticated_kite() -> KiteConnect:
    """Load today's Kite token from Supabase and return an authenticated KiteConnect."""
    from database.queries import get_kite_token

    row = get_kite_token()
    if not row:
        raise RuntimeError(
            "No Kite access token in database. "
            "Complete the OAuth flow at /kite/refresh."
        )

    access_token = row["access_token"]
    kite         = KiteConnect(api_key=_get_api_key())
    kite.set_access_token(access_token)
    return kite


def validate_token() -> bool:
    """Validate the stored Kite token via kite.profile()."""
    try:
        kite = get_authenticated_kite()
        kite.profile()
        logger.info("Kite token valid")
        return True
    except Exception as exc:
        logger.warning("Kite token invalid: %s", exc)
        return False
