"""
Telegram Notification Sender
==============================
Bot      : @abhishek_mittal_trade_bot  (ID: 8819449715)
Chat ID  : 6888091818  (Abhishek — private chat)
Library  : requests (direct Bot API calls — no third-party telegram library needed)

CONFIRMED WORKING: 2026-05-23
  Plain text message    → delivered, message_id: 3
  HTML formatted alert  → delivered, message_id: 4

TELEGRAM BOT API BASE URL:
  https://api.telegram.org/bot{BOT_TOKEN}/{method}

METHODS USED:
  getMe         — verify bot token is valid
  sendMessage   — send text (plain or HTML formatted)
  getUpdates    — read incoming messages (used to discover chat_id)

HTML FORMATTING CONFIRMED WORKING (parse_mode="HTML"):
  <b>bold text</b>
  <i>italic text</i>
  <code>monospace / inline code</code>
  <pre>multi-line monospace block</pre>
  NO NESTING — e.g. <b><i>text</i></b> may not render on all clients

FORMATTING THAT DOES NOT WORK IN HTML MODE:
  Markdown asterisks (*bold*) — use HTML tags instead
  Underline <u> — not supported by all Telegram clients

RATE LIMITS (Telegram official):
  30 messages/second across all chats
  1 message/second to the same chat_id
  For a single-user personal bot: 1 message/second is the practical limit
  Burst of 20 messages to the same chat triggers a 429 — add sleep(1) between calls
"""

import time
from datetime import datetime
from pathlib import Path

import requests

# ── Credentials ────────────────────────────────────────────────────────────
# Store in environment or a config file — never commit the token to git

BOT_TOKEN = "8819449715:AAErfcO08JiVHzfiMBF7l_H63YJwtPDWDsI"
CHAT_ID   = 6888091818   # Abhishek's private chat

BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Core send function ──────────────────────────────────────────────────────

def send_message(
    text: str,
    parse_mode: str = "HTML",
    chat_id: int = CHAT_ID,
    disable_notification: bool = False,
) -> int:
    """
    Send a message to the Telegram chat.

    Args:
        text                 : message body — HTML tags supported when parse_mode="HTML"
        parse_mode           : "HTML" (recommended) or "MarkdownV2"
        chat_id              : target chat — defaults to the confirmed personal chat
        disable_notification : True = silent notification (no sound/banner)

    Returns:
        message_id (int) of the sent message

    Raises:
        ConnectionError  on network failure
        ValueError       if Telegram rejects the message (bad token, chat not found, etc.)
    """
    payload = {
        "chat_id":              chat_id,
        "text":                 text,
        "parse_mode":           parse_mode,
        "disable_notification": disable_notification,
    }

    try:
        r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
    except requests.RequestException as e:
        raise ConnectionError(f"Network error sending Telegram message: {e}") from e

    result = r.json()
    if not result.get("ok"):
        raise ValueError(
            f"Telegram rejected message: {result.get('description', result)}"
        )

    return result["result"]["message_id"]


def send_silent(text: str, parse_mode: str = "HTML") -> int:
    """Send without sound — useful for low-priority informational messages."""
    return send_message(text, parse_mode=parse_mode, disable_notification=True)


# ── Pre-built alert formatters ─────────────────────────────────────────────

def format_trade_alert(
    symbol: str,
    signal: str,           # "LONG" or "SHORT"
    entry: float,
    target: float,
    stop_loss: float,
    setup: str,
    oi_change_pct: float | None = None,
    atm_iv: float | None = None,
    fii_flow: str | None = None,
) -> str:
    """
    Build a formatted HTML trade alert message.

    Confirmed rendering correctly in Telegram (tested 2026-05-23).
    """
    now    = datetime.now().strftime("%d-%b-%Y %H:%M")
    rr     = abs(target - entry) / abs(entry - stop_loss) if entry != stop_loss else 0
    pct_t  = (target - entry) / entry * 100
    pct_sl = (stop_loss - entry) / entry * 100

    signal_emoji = "BUY" if signal == "LONG" else "SELL"

    lines = [
        f"<b>SWING TRADE ALERT — {signal_emoji}</b>",
        f"<code>{now} IST</code>",
        "",
        f"<b>{symbol}</b>  |  NSE",
        f"Signal    : <b>{signal}</b>",
        f"Entry     : <code>{entry:,.2f}</code>",
        f"Target    : <code>{target:,.2f}</code>  ({pct_t:+.1f}%)",
        f"Stop Loss : <code>{stop_loss:,.2f}</code>  ({pct_sl:+.1f}%)",
        f"R:R       : <code>1 : {rr:.1f}</code>",
        "",
        f"Setup     : {setup}",
    ]

    if oi_change_pct is not None:
        direction = "buildup" if oi_change_pct > 0 else "unwinding"
        lines.append(f"OI        : {oi_change_pct:+.1f}% ({direction})")

    if atm_iv is not None:
        lines.append(f"ATM IV    : {atm_iv:.1f}%")

    if fii_flow:
        lines.append(f"FII Flow  : <i>{fii_flow}</i>")

    return "\n".join(lines)


def format_daily_summary(
    date_str: str,
    signals: list[dict],
    vix: float | None,
    fii_net: float | None,
    dii_net: float | None,
    nifty_change_pct: float | None,
) -> str:
    """
    Build a daily post-market summary message.

    signals: list of dicts with keys: symbol, signal, entry, target, stop_loss
    """
    lines = [
        f"<b>POST-MARKET SUMMARY</b>",
        f"<code>{date_str}</code>",
        "",
    ]

    # Market context
    if nifty_change_pct is not None:
        lines.append(f"NIFTY   : {nifty_change_pct:+.2f}%")
    if vix is not None:
        lines.append(f"VIX     : {vix:.2f}")
    if fii_net is not None:
        lines.append(f"FII     : {fii_net:+,.0f} Cr")
    if dii_net is not None:
        lines.append(f"DII     : {dii_net:+,.0f} Cr")

    lines.append("")

    if signals:
        lines.append(f"<b>Signals ({len(signals)})</b>")
        for s in signals:
            sym    = s.get("symbol", "?")
            sig    = s.get("signal", "?")
            entry  = s.get("entry", 0)
            target = s.get("target", 0)
            sl     = s.get("stop_loss", 0)
            lines.append(
                f"  <code>{sym:<12}</code> {sig}  "
                f"E:{entry:.0f}  T:{target:.0f}  SL:{sl:.0f}"
            )
    else:
        lines.append("<i>No signals today.</i>")

    return "\n".join(lines)


def format_error_alert(component: str, error: str) -> str:
    """Alert message for pipeline failures — sent silently."""
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    return (
        f"<b>PIPELINE ERROR</b>\n"
        f"<code>{now} IST</code>\n\n"
        f"Component : {component}\n"
        f"Error     : <code>{error[:300]}</code>"
    )


# ── Delivery helpers ────────────────────────────────────────────────────────

def send_trade_alerts(alerts: list[dict]) -> list[int]:
    """
    Send multiple trade alert dicts. Spaces each message by 1.1s to respect
    Telegram's 1 message/second limit to the same chat.

    Each dict in alerts must have the same keys as format_trade_alert().
    Returns list of message_ids.
    """
    message_ids = []
    for alert in alerts:
        text = format_trade_alert(**alert)
        mid  = send_message(text)
        message_ids.append(mid)
        time.sleep(1.1)   # Telegram rate limit: 1 msg/sec per chat
    return message_ids


def verify_bot() -> dict:
    """
    Confirm bot token is valid and bot is reachable.
    Returns bot info dict or raises ValueError.
    """
    r = requests.get(f"{BASE_URL}/getMe", timeout=10)
    result = r.json()
    if not result.get("ok"):
        raise ValueError(f"Invalid bot token: {result}")
    return result["result"]


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Verify bot
    bot = verify_bot()
    print(f"Bot     : {bot['first_name']}  (@{bot['username']})")
    print(f"Chat ID : {CHAT_ID}")

    # Send a trade alert
    alert_text = format_trade_alert(
        symbol        = "RELIANCE",
        signal        = "LONG",
        entry         = 1354.50,
        target        = 1410.00,
        stop_loss     = 1325.00,
        setup         = "Breakout above 52-week high on volume surge",
        oi_change_pct = +8.3,
        atm_iv        = 22.4,
        fii_flow      = "Buying (+₹2,100 Cr)",
    )
    mid = send_message(alert_text)
    print(f"\nTrade alert sent — message_id: {mid}")

    # Send daily summary
    summary_text = format_daily_summary(
        date_str         = "22-May-2026",
        signals          = [
            {"symbol": "RELIANCE", "signal": "LONG",  "entry": 1354, "target": 1410, "stop_loss": 1325},
            {"symbol": "HDFCBANK", "signal": "LONG",  "entry": 1912, "target": 1980, "stop_loss": 1875},
            {"symbol": "TATAMOTORS","signal": "SHORT", "entry": 942,  "target": 905,  "stop_loss": 965},
        ],
        vix              = 13.45,
        fii_net          = -4439.76,
        dii_net          = +6002.90,
        nifty_change_pct = +0.48,
    )
    time.sleep(1.1)
    mid2 = send_message(summary_text)
    print(f"Daily summary sent — message_id: {mid2}")
