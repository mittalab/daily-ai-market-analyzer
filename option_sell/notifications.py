"""
Telegram notifications for the option-sell recommendation engine.
"""
from __future__ import annotations

from datetime import date

from new_notifications.telegram import safe_html, send_loud, send_silent


def send_option_sell_summary(analysis_date: date, recs: list[dict]) -> None:
    """Send a summary of qualifying recommendations to Telegram."""
    date_str = str(analysis_date)

    if not recs:
        send_silent(f"Option Sell — {date_str}: No qualifying setups today.")
        return

    lines = [f"<b>Option Sell Recommendations — {safe_html(date_str)}</b>"]

    for rec in recs:
        symbol       = safe_html(rec["symbol"])
        action       = safe_html(rec["action"])         # SELL_PE / SELL_CE
        strike       = rec["strike"]
        opt_type     = action.split("_")[-1]            # PE / CE
        expiry       = safe_html(rec["expiry_date"])
        delta        = rec["computed_delta"]
        premium      = rec["premium"]
        roi_pct      = rec["roi_pct"] * 100
        ann_pct      = rec["annualized_pct"] * 100
        conviction   = safe_html(rec["conviction"])

        lines.append(
            f"\n<b>{symbol}</b> — SELL <code>{strike:.0f} {opt_type}</code> | Exp <code>{expiry}</code>\n"
            f"  Delta <code>{delta:+.2f}</code> · Prem <code>₹{premium:.2f}</code> · "
            f"ROI <code>{roi_pct:.2f}%</code> (<code>~{ann_pct:.1f}% ann</code>) · <i>{conviction}</i>"
        )

    text = "\n".join(lines)
    # Split at 4096-char Telegram limit — send_loud already handles retries
    send_loud(text[:4096])


def send_option_sell_failed(analysis_date: date, error: str) -> None:
    """Send a loud failure alert when the job crashes."""
    date_str = str(analysis_date)
    send_loud(f"⚠ Option Sell job failed ({safe_html(date_str)}): {safe_html(error)}")
