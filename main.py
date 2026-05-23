"""
FastAPI application entry point.
Embeds APScheduler for all scheduled jobs (Week 2+).
Week 1: health check only — no business logic yet.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import pytz
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.client import get_client

load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Scheduler is module-level so pipeline modules can import and register jobs.
# Timezone set to IST because all cron triggers in the spec use IST times.
scheduler = AsyncIOScheduler(timezone=IST)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: verify DB reachable, start scheduler. Shutdown: stop scheduler cleanly."""
    logger.info("Starting Swing Trading API...")

    # Fail fast: if DB is misconfigured we want the error at boot, not on first request.
    try:
        get_client()
        logger.info("Database connection verified")
    except RuntimeError as exc:
        logger.error("Cannot start: %s", exc)
        raise

    scheduler.start()
    logger.info("APScheduler started — jobs will be registered in Week 2")

    yield

    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")


app = FastAPI(
    title="Swing Trading Analysis API",
    description="Automated post-market analysis for Nifty 50 swing trades",
    version="1.0.0",
    lifespan=lifespan,
)

# Dashboard origin only. During local dev override CORS via environment if needed.
_dashboard_origin = "https://trading.rankachieversclasses.in"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_dashboard_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """
    Basic health check. Confirms API is running and database is reachable.
    Cloudflare Tunnel and NSSM both use this endpoint to verify the service.
    """
    from database.queries import keepalive

    db_ok = keepalive()
    now_ist = datetime.now(IST)

    return {
        "status":      "healthy" if db_ok else "degraded",
        "timestamp":   now_ist.isoformat(),
        "database":    "connected" if db_ok else "unreachable",
        "version":     "1.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload=True only for local dev, never in production service
    )
