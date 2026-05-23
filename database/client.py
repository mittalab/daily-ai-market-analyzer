"""
Supabase client — single connection instance for the entire process.

Service key is used throughout the backend: full read/write access.
The frontend uses the anon key (read-only) via Supabase JS client directly.
Keeping both keys separate prevents the dashboard from ever writing data.
"""
import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return the cached Supabase client. Created once at startup, reused for the process lifetime."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env — "
            "copy .env.example to .env and fill in values"
        )

    client = create_client(url, key)
    logger.info("Supabase client initialised (service key)")
    return client
