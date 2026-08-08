"""
POST /api/analyse — on-demand single-stock deep analysis via Claude (Turn 3).

Cache-first: if a MANUAL turn already exists for this symbol in the latest session,
return the cached result immediately (no Claude call, no cost).
Otherwise: build the Turn 3 data package using market context and prescan data from
the latest analysis session, call Claude, and persist as turn_type='MANUAL'.

Feature gate: system_config.manual_analysis_enabled must be "true".
"""
import json
import logging
import os
import time
from datetime import date
from typing import Literal

import anthropic
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from database.queries import (
    get_all_system_config,
    get_claude_turn,
    get_latest_session,
    get_manual_turn_for_symbol,
    get_next_manual_turn_number,
    get_turn_for_symbol,
    save_claude_turn,
)
from new_data_ingestion.nse_bhavcopy import get_nifty50_symbols
from pipeline.claude_session import (
    _build_turn3_data,
    _build_turn3_prompt,
    _validate_position_sizing_turn3,
)
from pipeline.deep_analysis import DEEP_SYSTEM, _MODEL

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class AnalyseRequest(BaseModel):
    symbol:         str
    direction:      Literal["AUTO", "LONG", "SHORT"] = "AUTO"
    save_to_ledger: bool = False
    force_refresh:  bool = False  # bypass cache and re-run even if cached

    @field_validator("symbol")
    @classmethod
    def normalise_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("direction")
    @classmethod
    def normalise_direction(cls, v: str) -> str:
        return v.strip().upper()


class AnalyseResponse(BaseModel):
    symbol:             str
    session_date:       str
    is_nifty50:         bool
    custom_symbol_note: str | None
    analysis:           dict
    estimated_cost_usd: float
    data_quality_notes: list[str]
    duration_seconds:   float
    is_cached:          bool
    setup_id:           str | None


# ── OHLCV helper ─────────────────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, session_date: date) -> list:
    """Fetch OHLCV rows from price_history for chart rendering."""
    from datetime import timedelta
    from database.client import get_client
    try:
        cutoff = str(session_date - timedelta(days=200))
        res = (
            get_client()
            .table("price_history")
            .select("date,open,high,low,close,volume")
            .eq("symbol", symbol)
            .gte("date", cutoff)
            .order("date", desc=True)
            .execute()
        )
        rows = list(reversed(res.data))
        return [
            {
                "date":   r["date"],
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": int(r["volume"] or 0),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("ohlcv_data fetch failed for %s: %s", symbol, exc)
        return []


# ── Claude helpers ────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    t = raw.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())


def _call_claude(client: anthropic.Anthropic, prompt: str) -> tuple[dict, int, int]:
    """Call Claude with the Turn 3 prompt; retries 3× on transient errors."""
    backoff  = [5, 10, 20]
    last_exc = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=16000,
                system=[{
                    "type": "text",
                    "text": DEEP_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            raw    = resp.content[0].text
            parsed = _parse_json(raw)
            return parsed, resp.usage.input_tokens, resp.usage.output_tokens
        except anthropic.RateLimitError as exc:
            last_exc = exc
            time.sleep(backoff[min(attempt, 2)])
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                time.sleep(backoff[min(attempt, 2)])
            else:
                raise HTTPException(status_code=502, detail=f"Claude API error: {exc.message}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail=f"Claude returned non-JSON: {exc}")
    raise HTTPException(status_code=503, detail=f"Claude unavailable after 3 attempts: {last_exc}")


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.post("/analyse", response_model=AnalyseResponse)
async def analyse_stock(req: AnalyseRequest) -> AnalyseResponse:
    t_start = time.monotonic()
    symbol  = req.symbol

    config = get_all_system_config()
    if config.get("manual_analysis_enabled", "true").lower() != "true":
        raise HTTPException(status_code=503, detail="Manual analysis disabled (budget control)")

    nifty50_symbols = set(get_nifty50_symbols())
    is_nifty50      = symbol in nifty50_symbols
    custom_note     = (
        "Custom symbol — data availability not guaranteed. "
        "Ensure price history exists in the database."
        if not is_nifty50 else None
    )
    quality_notes: list[str] = []
    if custom_note:
        quality_notes.append(custom_note)

    # ── 1. Latest session ─────────────────────────────────────────────────────
    session = get_latest_session()
    if not session:
        raise HTTPException(status_code=503, detail="No analysis session found in DB — run the pipeline first")

    latest_session_id = session["session_id"]
    session_date_raw  = session["session_date"]
    session_date = (
        date.fromisoformat(str(session_date_raw))
        if isinstance(session_date_raw, str)
        else session_date_raw
    )

    # ── 2. Cache check ────────────────────────────────────────────────────────
    # Priority: MANUAL turn (re-analysis) → deep_analysis turn (nightly pipeline)
    if not req.force_refresh:
        for turn_type in ("MANUAL", "deep_analysis"):
            cached_turn = get_turn_for_symbol(latest_session_id, symbol, turn_type)
            if cached_turn:
                logger.info(
                    "Cache hit: %s turn for %s in session %s",
                    turn_type, symbol, latest_session_id,
                )
                analysis = json.loads(cached_turn["output_text"])
                analysis["symbol"]     = symbol
                analysis["ohlcv_data"] = _fetch_ohlcv(symbol, session_date)
                source_note = (
                    "Returned from cache (manual re-analysis)"
                    if turn_type == "MANUAL"
                    else "Returned from nightly pipeline analysis — no new Claude call"
                )
                return AnalyseResponse(
                    symbol=symbol,
                    session_date=str(session_date),
                    is_nifty50=is_nifty50,
                    custom_symbol_note=custom_note,
                    analysis=analysis,
                    estimated_cost_usd=0.0,
                    data_quality_notes=[source_note],
                    duration_seconds=round(time.monotonic() - t_start, 3),
                    is_cached=True,
                    setup_id=None,
                )

    # ── 3. Load Turn 1 (market context) ──────────────────────────────────────
    t1_row = get_claude_turn(latest_session_id, 1)
    if not t1_row:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Market context (Turn 1) not found for session {latest_session_id}. "
                "Run the nightly pipeline first."
            ),
        )
    turn1_result = json.loads(t1_row["output_text"])

    # ── 4. Load Turn 2 (prescan) ──────────────────────────────────────────────
    t2_row = get_claude_turn(latest_session_id, 2)
    turn2_result: list = []
    if t2_row:
        raw2 = json.loads(t2_row["output_text"])
        if isinstance(raw2, dict):
            turn2_result = raw2.get("stock_assessments") or raw2.get("stocks") or []
        else:
            turn2_result = raw2

    # ── 5. Apply direction override if symbol is in Turn 2 ───────────────────
    t2_entry = next((a for a in turn2_result if a.get("symbol") == symbol), None)
    if t2_entry is None:
        quality_notes.append(f"{symbol} was not in tonight's prescan — analysis uses defaults")
        logger.info("%s not in Turn 2 prescan; proceeding without prescan context", symbol)
    elif req.direction != "AUTO":
        direction = req.direction
        t2_entry["preliminary_direction"] = direction
        t2_entry["direction"]             = direction
        logger.info("Overriding Turn 2 direction for %s → %s", symbol, direction)

    # ── 6. Build data package and prompt (reuses pipeline Turn 3 functions) ───
    config.setdefault("claude_capital_inr", "500000")
    stock_pkg = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
    if not stock_pkg:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No price history for {symbol}. "
                "Run the nightly pipeline first to populate data."
            ),
        )
    prompt = _build_turn3_prompt(stock_pkg)

    # ── 7. Call Claude ────────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=api_key)
    logger.info("Calling Claude (Turn 3) for manual analysis: %s direction=%s", symbol, req.direction)
    analysis, input_tok, output_tok = _call_claude(client, prompt)
    analysis["symbol"] = symbol

    # ── 8. Position sizing ────────────────────────────────────────────────────
    analysis = _validate_position_sizing_turn3(analysis, config)

    # ── 8.5. Attach OHLCV data for chart rendering ────────────────────────────
    analysis["ohlcv_data"] = _fetch_ohlcv(symbol, session_date)

    # ── 9. Persist as MANUAL turn in session_claude_turns ────────────────────
    turn_number = get_next_manual_turn_number(latest_session_id)
    try:
        save_claude_turn(
            session_id=latest_session_id,
            turn_number=turn_number,
            turn_type="MANUAL",
            symbol=symbol,
            input_tokens=input_tok,
            output_tokens=output_tok,
            input_text=prompt,
            output_text=json.dumps(analysis),
        )
        logger.info(
            "MANUAL turn persisted: session=%s symbol=%s turn_number=%d",
            latest_session_id, symbol, turn_number,
        )
    except Exception as exc:
        logger.warning("Could not persist MANUAL turn: %s", exc)
        quality_notes.append(f"Warning: result not cached — {exc}")

    cost_usd = round(input_tok / 1_000_000 * 3.00 + output_tok / 1_000_000 * 15.00, 6)
    logger.info(
        "Manual analysis complete: %s stage=%s score=%s cost=$%.4f",
        symbol, analysis.get("stage"), analysis.get("conviction_score"), cost_usd,
    )

    return AnalyseResponse(
        symbol=symbol,
        session_date=str(session_date),
        is_nifty50=is_nifty50,
        custom_symbol_note=custom_note,
        analysis=analysis,
        estimated_cost_usd=cost_usd,
        data_quality_notes=quality_notes,
        duration_seconds=round(time.monotonic() - t_start, 2),
        is_cached=False,
        setup_id=None,
    )


# ── F&O stocks list endpoint (unchanged) ──────────────────────────────────────

@router.get("/fo-stocks", response_model=list[str])
async def get_fo_stocks():
    """Return all stock symbols that have active F&O contracts."""
    from new_utils.stock_list import fetch_kite_fo_stocks
    try:
        stocks = fetch_kite_fo_stocks()
        if stocks:
            return stocks
    except Exception as e:
        logger.warning("Failed to fetch F&O stocks from Kite: %s", e)

    try:
        from pipeline.claude_session import _load_sector_map
        sector_map = _load_sector_map()
        stocks = list(sector_map.get("stocks", {}).keys())
        if stocks:
            return sorted(stocks)
    except Exception as e:
        logger.warning("Failed to fetch F&O stocks from sector map: %s", e)

    try:
        from new_data_ingestion.nse_bhavcopy import get_nifty50_symbols
        symbols = list(get_nifty50_symbols())
        if symbols:
            return sorted(symbols)
    except Exception:
        pass

    return []
