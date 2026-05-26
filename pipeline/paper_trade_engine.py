"""
Paper Trade Engine (Section 26).

Part A — Entry check: For each new TRADE_READY / WATCH setup, check the next
          2 trading days to see if price traded through the entry zone.

Part B — Exit check: For all open paper trades (entry_triggered=True,
          paper_outcome IS NULL), walk forward each day applying exit rules.

Uses ACTUAL option premium_close from options_snapshots wherever available.
Falls back to underlying OHLCV for entry zone check when option data is absent.

State machine:
  FLAGGED → entry zone hit?
    YES → ACTIVE
      SL hit          → paper_outcome = SL_HIT
      T1 hit (50 lots) → move SL to entry, continue
      T2 hit           → paper_outcome = TARGET_HIT
      Day 5            → paper_outcome = EXPIRED
      Breakeven SL     → paper_outcome = CLOSED_BREAKEVEN
    NO (2 days)      → paper_outcome = ENTRY_MISSED
"""
import logging
from datetime import date, timedelta

from pandas import bdate_range

from database.client import get_client
from database.queries import (
    get_open_trade_setups,
    get_price_history,
    get_trade_setups_by_date,
    update_trade_setup,
)
from integrations.telegram import send_paper_trade_outcome

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_trading_days(from_date: date, n: int) -> list[date]:
    """Return the next N weekday dates after from_date (no holiday calendar)."""
    dates = list(bdate_range(start=from_date, periods=n + 1))[1:]  # skip from_date itself
    return [d.date() for d in dates[:n]]


def _get_option_premiums(
    symbol: str,
    strike,
    expiry_date,
    option_type: str,
    from_date: date,
    to_date: date,
) -> dict[date, float]:
    """
    Fetch daily premium_close from options_snapshots for a specific contract.
    Returns {date: premium_close}. Empty dict if no data.
    """
    try:
        resp = (
            get_client()
            .table("options_snapshots")
            .select("snapshot_date,premium_close")
            .eq("symbol", symbol)
            .eq("strike", float(strike))
            .eq("expiry_date", str(expiry_date))
            .eq("option_type", option_type)
            .gte("snapshot_date", str(from_date))
            .lte("snapshot_date", str(to_date))
            .order("snapshot_date", desc=False)
            .execute()
        )
        return {
            date.fromisoformat(r["snapshot_date"]): float(r["premium_close"])
            for r in resp.data
            if r.get("premium_close") is not None
        }
    except Exception as exc:
        logger.warning("Option premium fetch failed for %s %s %s: %s",
                       symbol, strike, option_type, exc)
        return {}


def _get_underlying_ohlcv(symbol: str, from_date: date, to_date: date) -> dict[date, dict]:
    """
    Return {date: {open, high, low, close}} for the underlying from price_history.
    """
    rows = get_price_history(symbol, days=30)
    result = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        if from_date <= d <= to_date:
            result[d] = {k: float(r[k]) for k in ("open", "high", "low", "close")}
    return result


def _brokerage(lots: int, lot_size: int, entry_price: float) -> float:
    """₹40/lot or 0.03% of trade value — whichever is lower — × 2 legs."""
    trade_value   = entry_price * lots * lot_size
    pct_brokerage = trade_value * 0.0003
    flat          = 40.0 * lots
    per_leg       = min(flat, pct_brokerage)
    return round(per_leg * 2, 2)  # buy + sell


# ── Part A: check new entries ─────────────────────────────────────────────────

def _check_entry(setup: dict, today: date) -> None:
    """
    For a single FLAGGED setup, check whether the option premium touched the
    entry zone in the 2 trading days after setup_date. Updates DB if triggered.
    """
    setup_id   = setup["id"]
    symbol     = setup["symbol"]
    strike     = setup.get("strike")
    expiry     = setup.get("expiry_date")
    opt_type   = setup.get("option_type", "CE")
    entry_low  = float(setup.get("entry_zone_low") or 0)
    entry_high = float(setup.get("entry_zone_high") or 0)
    setup_date = date.fromisoformat(str(setup["setup_date"]))

    check_days = _next_trading_days(setup_date, 2)
    future_days = [d for d in check_days if d <= today]

    if not future_days:
        return  # not yet time to check

    # Try option premium data first
    if strike and expiry:
        premiums = _get_option_premiums(
            symbol, strike, date.fromisoformat(str(expiry)), opt_type,
            future_days[0], future_days[-1],
        )
    else:
        premiums = {}

    # Fallback: underlying price for entry zone check
    if not premiums:
        underlying = _get_underlying_ohlcv(symbol, future_days[0], future_days[-1])
        for d in future_days:
            row = underlying.get(d)
            if row:
                # Entry zone here is the underlying price zone (use entry_zone_low/high as proxy)
                if row["low"] <= entry_high and row["high"] >= entry_low:
                    mid   = (entry_low + entry_high) / 2
                    entry = round(mid * 1.005, 2)  # 0.5% slippage
                    logger.info("Paper entry triggered (underlying fallback): %s on %s @ %.2f", symbol, d, entry)
                    update_trade_setup(setup_id, {
                        "entry_triggered":   True,
                        "entry_date":        str(d),
                        "actual_entry_price": entry,
                    })
                    return
        # Check if 2 days elapsed without trigger
        if len(future_days) == len(check_days):
            logger.info("Paper entry missed (no underlying data): %s", symbol)
            update_trade_setup(setup_id, {"paper_outcome": "ENTRY_MISSED"})
        return

    # Option premium path
    for d in future_days:
        ltp = premiums.get(d)
        if ltp is None:
            continue
        if ltp > entry_high:
            # Gapped past zone — missed
            logger.info("Paper entry gapped past zone: %s on %s LTP=%.2f > %.2f",
                        symbol, d, ltp, entry_high)
            update_trade_setup(setup_id, {"paper_outcome": "ENTRY_MISSED"})
            return
        if entry_low <= ltp <= entry_high:
            entry = round(((entry_low + entry_high) / 2) * 1.005, 2)
            logger.info("Paper entry triggered: %s on %s @ %.2f", symbol, d, entry)
            update_trade_setup(setup_id, {
                "entry_triggered":    True,
                "entry_date":         str(d),
                "actual_entry_price": entry,
            })
            return

    # All check days passed — no trigger
    if len(future_days) == len(check_days):
        logger.info("Paper entry missed: %s (2 days elapsed, never touched zone)", symbol)
        update_trade_setup(setup_id, {"paper_outcome": "ENTRY_MISSED"})


# ── Part B: check exits ────────────────────────────────────────────────────────

_OUTCOME_NOTE = "Daily candle simulation — SL checked before target"


def _sl_hit_intraday(is_long: bool, candle: dict, sl_price: float) -> bool:
    """
    Check whether the SL level was breached intraday using underlying OHLC.
    LONG (bought CE): SL when underlying LOW <= underlying_stop (stock fell to stop).
    SHORT (bought PE): SL when underlying HIGH >= underlying_stop (stock rose to stop).
    sl_price here is the underlying_stop level, not the option premium SL.
    Returns False if candle data is absent (conservative — don't trigger without data).
    """
    if not candle or sl_price <= 0:
        return False
    if is_long:
        return float(candle.get("low", float("inf"))) <= sl_price
    else:
        return float(candle.get("high", 0.0)) >= sl_price


def _gap_favourable(is_long: bool, candle: dict, t2_premium: float, prev_close: float | None) -> bool:
    """
    Gap open in favour: option opened beyond T2 before any intraday action.
    We detect this when the option's first available close is already past T2
    AND the underlying opened with a significant gap in the favourable direction.
    With daily candles only, use underlying open as the gap proxy.
    """
    if not candle:
        return False
    u_open = float(candle.get("open", 0.0))
    if prev_close is None:
        return False
    # A true gap means the underlying opened beyond the previous day's close by >0.5%
    gap_pct = (u_open - prev_close) / prev_close if prev_close > 0 else 0
    if is_long:
        return gap_pct > 0.005   # stock gapped up — CE likely opened past T2
    else:
        return gap_pct < -0.005  # stock gapped down — PE likely opened past T2


def _check_exits(setup: dict, today: date) -> None:
    """
    Walk forward from entry_date to today applying direction-aware exit rules
    using daily OHLC candles. SL is always checked before target on the same candle.

    SL detection  : underlying OHLC vs underlying_stop (intraday accuracy)
    Target detection: option premium_close vs target_1/2_premium (EOD)
    Gap open      : underlying open gap > 0.5% triggers TARGET_HIT at t2_premium

    outcome_note = "Daily candle simulation — SL checked before target"
    """
    setup_id         = setup["id"]
    symbol           = setup["symbol"]
    strike           = setup.get("strike")
    expiry           = setup.get("expiry_date")
    opt_type         = setup.get("option_type", "CE")
    direction        = setup.get("direction", "LONG")
    is_long          = direction == "LONG"
    entry_price      = float(setup.get("actual_entry_price") or 0)
    sl_premium       = float(setup.get("stop_loss_premium") or 0)
    t1_premium       = float(setup.get("target_1_premium") or 0)
    t2_premium       = float(setup.get("target_2_premium") or 0)
    underlying_stop  = float(setup.get("underlying_stop") or 0)
    lots             = int(setup.get("lots") or 1)
    lot_size         = int(setup.get("lot_size") or 1)
    entry_date       = date.fromisoformat(str(setup["entry_date"]))

    trading_days = [d.date() for d in bdate_range(start=entry_date, end=today)
                    if d.date() > entry_date]
    if not trading_days:
        return

    # ── Data fetching ─────────────────────────────────────────────────────────
    # Option premium_close: used for TARGET detection (EOD accuracy)
    if strike and expiry:
        premiums = _get_option_premiums(
            symbol, strike, date.fromisoformat(str(expiry)), opt_type,
            trading_days[0], trading_days[-1],
        )
    else:
        premiums = {}

    # Underlying OHLC: used for SL detection (intraday accuracy) + gap detection
    # Fetch from entry_date - 1 so we have prev_close for gap check on day 1
    fetch_from = trading_days[0] - timedelta(days=5)
    underlying = _get_underlying_ohlcv(symbol, fetch_from, trading_days[-1])

    # Find underlying close on entry_date for gap baseline
    entry_underlying = underlying.get(entry_date)
    prev_underlying_close: float | None = entry_underlying["close"] if entry_underlying else None
    # Underlying close on entry_date — used as breakeven stop after T1 hit
    underlying_entry_price: float = entry_underlying["close"] if entry_underlying else 0.0

    # ── State ─────────────────────────────────────────────────────────────────
    t1_hit         = False
    sl_level       = sl_premium        # option SL — may shift to entry_price after T1
    underlying_sl  = underlying_stop   # underlying SL — may shift after T1
    remaining_lots = lots
    half_lots      = max(1, lots // 2)
    t1_partial_pnl = 0.0
    brk            = _brokerage(lots, lot_size, entry_price)

    def _record(outcome: str, exit_price: float, day_num: int, d: date) -> None:
        gross = (exit_price - entry_price) * remaining_lots * lot_size + t1_partial_pnl
        net   = round(gross - brk, 2)
        logger.info("Paper %s: %s on %s exit=%.2f pnl=₹%.0f", outcome, symbol, d, exit_price, net)
        update_trade_setup(setup_id, {
            "paper_outcome":       outcome,
            "paper_exit_date":     str(d),
            "paper_exit_price":    exit_price,
            "paper_pnl_inr":       net,
            "paper_holding_days":  day_num,
            "outcome_note":        _OUTCOME_NOTE,
            "t1_hit":              t1_hit,
            "t1_exit_price":       t1_premium if t1_hit else None,
            "t1_pnl_inr":         round(t1_partial_pnl, 2) if t1_hit else None,
        })
        if outcome in ("TARGET_HIT", "SL_HIT"):
            send_paper_trade_outcome(
                outcome=outcome,
                symbol=symbol,
                direction=direction,
                option_type=opt_type,
                strike=int(strike) if strike else None,
                exit_price=exit_price,
                pnl_inr=net,
                holding_days=day_num,
            )

    for day_num, d in enumerate(trading_days, start=1):
        candle    = underlying.get(d, {})
        prem_close = premiums.get(d)

        # ── Step 1: Gap open in favour (checked before SL — gap past T2 is profit) ──
        if prem_close is not None and prem_close >= t2_premium:
            if _gap_favourable(is_long, candle, t2_premium, prev_underlying_close):
                # Exit at T2 premium (gapped through — filled at target, not beyond)
                _record("TARGET_HIT", t2_premium, day_num, d)
                return

        # ── Step 2: SL check — FIRST, using underlying intraday OHLC ────────────
        if _sl_hit_intraday(is_long, candle, underlying_sl):
            _record("CLOSED_BREAKEVEN" if t1_hit else "SL_HIT", sl_level, day_num, d)
            return

        # ── Step 3: Target checks using option premium_close (EOD) ──────────────
        # Use entry_price as fallback close if option data unavailable on day 5
        close = prem_close if prem_close is not None else (entry_price if day_num >= 5 else None)
        if close is None:
            prev_underlying_close = candle.get("close", prev_underlying_close)
            continue

        if is_long:
            # LONG (CE): higher close = profit
            if close >= t2_premium:
                _record("TARGET_HIT", t2_premium, day_num, d)
                return
            if not t1_hit and close >= t1_premium:
                t1_hit          = True
                sl_level        = entry_price          # move option SL to breakeven
                underlying_sl   = underlying_entry_price  # move underlying SL to entry (not removed)
                t1_partial_pnl  = (t1_premium - entry_price) * half_lots * lot_size
                remaining_lots  = lots - half_lots
                logger.info("Paper T1 hit (LONG): %s on %s close=%.2f — SL to entry %.2f", symbol, d, close, underlying_entry_price)
        else:
            # SHORT (PE): lower close = profit
            if close <= t2_premium:
                _record("TARGET_HIT", t2_premium, day_num, d)
                return
            if not t1_hit and close <= t1_premium:
                t1_hit          = True
                sl_level        = entry_price
                underlying_sl   = underlying_entry_price  # move underlying SL to entry (not removed)
                t1_partial_pnl  = (t1_premium - entry_price) * half_lots * lot_size
                remaining_lots  = lots - half_lots
                logger.info("Paper T1 hit (SHORT): %s on %s close=%.2f — SL to entry %.2f", symbol, d, close, underlying_entry_price)

        # ── Step 4: Day 5 — time stop, exit at close ──────────────────────────
        if day_num >= 5:
            _record("EXPIRED", close, day_num, d)
            return

        # Advance prev_close for next day's gap check
        prev_underlying_close = candle.get("close", prev_underlying_close)


# ── Public entry point ────────────────────────────────────────────────────────

def run_paper_trade_engine(session_date: date) -> dict:
    """
    Run both parts of the paper trade engine for session_date.
    Called at midnight after the main Claude pipeline completes.
    Returns a summary dict for logging.
    """
    today = session_date
    summary = {"entries_checked": 0, "exits_checked": 0, "errors": []}

    # ── Part A: new entry checks ──────────────────────────────────────────────
    new_setups = get_trade_setups_by_date(session_date)
    actionable = [s for s in new_setups
                  if s.get("stage") in ("TRADE_READY", "WATCH")
                  and not s.get("entry_triggered")
                  and s.get("paper_outcome") is None]

    for setup in actionable:
        try:
            _check_entry(setup, today)
            summary["entries_checked"] += 1
        except Exception as exc:
            msg = f"Entry check failed for {setup.get('symbol')}: {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    # ── Part B: open position exits ───────────────────────────────────────────
    open_setups = get_open_trade_setups()
    for setup in open_setups:
        try:
            _check_exits(setup, today)
            summary["exits_checked"] += 1
        except Exception as exc:
            msg = f"Exit check failed for {setup.get('symbol')} ({setup.get('id')}): {exc}"
            logger.error(msg)
            summary["errors"].append(msg)

    logger.info(
        "Paper trade engine complete: %d entries checked, %d exits checked, %d errors",
        summary["entries_checked"], summary["exits_checked"], len(summary["errors"]),
    )
    return summary
