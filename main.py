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
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from api.manual_analysis import router as manual_analysis_router
from api.dashboard import router as dashboard_router
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
    from scheduler import register_jobs
    register_jobs(scheduler)
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))

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
_dashboard_origin = "https://trading.abhishekmittal.in"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_dashboard_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(manual_analysis_router)
app.include_router(dashboard_router)


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


@app.get("/kite/refresh", tags=["kite-auth"])
async def kite_refresh():
    """
    Start the Kite OAuth flow.
    Opens Zerodha login page — after login, Zerodha redirects to /kite/callback.
    """
    from integrations.kite_oauth import get_login_url
    url = get_login_url()
    logger.info("Kite OAuth: redirecting to Zerodha login")
    return RedirectResponse(url)


@app.get("/kite/callback", tags=["kite-auth"])
async def kite_callback(
    request_token: str = Query(..., description="Single-use token from Zerodha OAuth"),
    action: str       = Query("login", description="Zerodha passes action=login on success"),
    status: str       = Query("success", description="Zerodha passes status=success on success"),
):
    """
    Zerodha OAuth callback — exchanges request_token for access_token and stores in Supabase.
    request_token is single-use: exchanged exactly once here, never logged.
    """
    from integrations.kite_oauth import exchange_request_token

    if status != "success":
        logger.warning("Kite callback: Zerodha returned status=%s", status)
        return HTMLResponse(_html_result(
            ok=False,
            title="Login Failed",
            message=f"Zerodha returned status: {status}",
        ), status_code=400)

    try:
        access_token = exchange_request_token(request_token)
        logger.info("Kite OAuth: token stored successfully")
        return HTMLResponse(_html_result(
            ok=True,
            title="Token Stored",
            message="Kite access token saved to database. Tonight's pipeline is ready.",
        ))
    except Exception as exc:
        logger.error("Kite callback exchange failed: %s", exc)
        return HTMLResponse(_html_result(
            ok=False,
            title="Exchange Failed",
            message=str(exc),
        ), status_code=500)


def _html_result(ok: bool, title: str, message: str) -> str:
    colour = "#22c55e" if ok else "#ef4444"
    icon   = "&#10003;" if ok else "&#10007;"
    return f"""<!DOCTYPE html>
<html><head><title>Kite Auth</title>
<style>
  body {{ font-family: sans-serif; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0; background: #0f172a; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 2rem 3rem;
           text-align: center; color: #f1f5f9; max-width: 420px; }}
  .icon {{ font-size: 3rem; color: {colour}; }}
  h1 {{ color: {colour}; margin: 0.5rem 0; }}
  p {{ color: #94a3b8; margin: 0; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{message}</p>
</div></body></html>"""


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload=True only for local dev, never in production service
    )
