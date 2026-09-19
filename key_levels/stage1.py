"""
Phase 1 — deterministic key-level computation. Zero network/LLM calls.

Public API:
    compute_technicals(ohlcv_df) -> dict
    detect_pivots(ohlcv_df, window=5) -> list[dict]
    cluster_pivots(pivots, atr14, tolerance_atr=0.75) -> list[dict]
    tag_confluence(clusters, ema20, ema50, ohlcv_df) -> list[dict]
    tag_zone_character(clusters, ohlcv_df) -> list[dict]
    extract_oi_levels(chain_df, spot, band_pct=0.20, top_n=5) -> list[dict]
    render_zone_chart(ohlcv_df, clusters, symbol) -> str
    build_stage1_output(symbol, ohlcv_df, chain_df) -> dict
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from indicators.technical import calculate_atr, calculate_ema

logger = logging.getLogger(__name__)

_CHART_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp", "key_level_charts")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _prep_df(df: Any) -> pd.DataFrame:
    """Normalise column names, coerce numeric OHLCV, drop rows without close."""
    if isinstance(df, list):
        df = pd.DataFrame(df)
    else:
        df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    # If date is the index, pull it back as a column
    if "date" not in df.columns and df.index.name == "date":
        df = df.reset_index()
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df


def _round_number_scale(price: float) -> float:
    """Return the round-number grid size appropriate for a given price level."""
    if price < 100:
        return 5.0
    if price < 500:
        return 10.0
    if price < 2000:
        return 50.0
    if price < 5000:
        return 100.0
    return 500.0


def _date_str(val: Any) -> str:
    if isinstance(val, (date, datetime)):
        return str(val)[:10]
    return str(val)[:10]


# ── 1. compute_technicals ──────────────────────────────────────────────────────

def compute_technicals(ohlcv_df: Any) -> dict:
    """
    Return {ema20, ema50, atr14, hv20, hv60} as of latest close.
    Any metric that cannot be computed (insufficient rows) is returned as None.
    """
    df = _prep_df(ohlcv_df)
    n = len(df)

    ema20 = ema50 = atr14 = hv20 = hv60 = None

    if n >= 20:
        ema20 = round(float(calculate_ema(df["close"], 20).iloc[-1]), 2)
    if n >= 50:
        ema50 = round(float(calculate_ema(df["close"], 50).iloc[-1]), 2)
    if n >= 15:
        val = calculate_atr(df, 14).iloc[-1]
        atr14 = round(float(val), 2) if pd.notnull(val) else None

    log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
    if len(log_ret) >= 20:
        hv20 = round(float(log_ret.iloc[-20:].std() * np.sqrt(252)), 2)
    if len(log_ret) >= 60:
        hv60 = round(float(log_ret.iloc[-60:].std() * np.sqrt(252)), 2)

    return {"ema20": ema20, "ema50": ema50, "atr14": atr14, "hv20": hv20, "hv60": hv60}


# ── 2. detect_pivots ──────────────────────────────────────────────────────────

def detect_pivots(ohlcv_df: Any, window: int = 5) -> list[dict]:
    """
    Find local swing lows (LOW pivots) and highs (HIGH pivots).
    Each pivot: {price, date, type ('LOW'|'HIGH'), volume_ratio}.
    """
    df = _prep_df(ohlcv_df)
    n = len(df)
    if n < 2 * window + 1:
        return []

    vol_20d = df["volume"].rolling(20, min_periods=1).mean()
    pivots: list[dict] = []

    for i in range(window, n - window):
        lo = df["low"].iloc[i]
        hi = df["high"].iloc[i]
        window_slice_lo = df["low"].iloc[i - window: i + window + 1]
        window_slice_hi = df["high"].iloc[i - window: i + window + 1]

        avg_vol = float(vol_20d.iloc[i])
        vol_ratio = round(float(df["volume"].iloc[i]) / avg_vol, 2) if avg_vol > 0 else 1.0

        date_val = _date_str(df["date"].iloc[i]) if "date" in df.columns else str(i)

        if lo == window_slice_lo.min():
            pivots.append({"price": round(float(lo), 2), "date": date_val, "type": "LOW", "volume_ratio": vol_ratio})

        if hi == window_slice_hi.max():
            pivots.append({"price": round(float(hi), 2), "date": date_val, "type": "HIGH", "volume_ratio": vol_ratio})

    return pivots


# ── 3. cluster_pivots ─────────────────────────────────────────────────────────

def cluster_pivots(pivots: list[dict], atr14: float | None, tolerance_atr: float = 0.75) -> list[dict]:
    """
    Group same-type pivots within tolerance_atr*atr14 of each other.
    Returns top 5 per type (touch_count DESC, last_touch_date DESC).
    """
    if not pivots or atr14 is None or atr14 <= 0:
        return []

    tolerance = tolerance_atr * atr14
    result: list[dict] = []

    for ptype in ("LOW", "HIGH"):
        typed = sorted([p for p in pivots if p["type"] == ptype], key=lambda x: x["price"])
        if not typed:
            continue

        # Greedy single-pass merge
        groups: list[list[dict]] = []
        current: list[dict] = [typed[0]]

        for p in typed[1:]:
            group_prices = [g["price"] for g in current]
            group_mid = (min(group_prices) + max(group_prices)) / 2
            if abs(p["price"] - group_mid) <= tolerance:
                current.append(p)
            else:
                groups.append(current)
                current = [p]
        groups.append(current)

        for group in groups:
            prices = [p["price"] for p in group]
            dates = sorted(p["date"] for p in group)
            result.append({
                "type": ptype,
                "zone_low": round(min(prices), 2),
                "zone_high": round(max(prices), 2),
                "touch_count": len(group),
                "last_touch_date": dates[-1],
                "avg_volume_ratio_on_touches": round(
                    sum(p["volume_ratio"] for p in group) / len(group), 2
                ),
            })

    # Top 5 per type: touch_count DESC, last_touch_date DESC
    final: list[dict] = []
    for ptype in ("LOW", "HIGH"):
        typed_clusters = [c for c in result if c["type"] == ptype]
        typed_clusters.sort(key=lambda c: (c["touch_count"], c["last_touch_date"]), reverse=True)
        final.extend(typed_clusters[:5])

    return final


# ── 4. tag_confluence ─────────────────────────────────────────────────────────

def tag_confluence(
    clusters: list[dict],
    ema20: float | None,
    ema50: float | None,
    ohlcv_df: Any,
) -> list[dict]:
    """
    Annotate each cluster with:
      is_round_number, near_ema ('EMA20'|'EMA50'|None), is_prior_breakout_level.
    """
    df = _prep_df(ohlcv_df)
    tech = compute_technicals(df)
    atr14 = tech["atr14"] or 1.0
    half_atr = 0.5 * atr14

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(closes)

    result: list[dict] = []
    for c in clusters:
        midpoint = (c["zone_low"] + c["zone_high"]) / 2
        scale = _round_number_scale(midpoint)
        nearest = round(midpoint / scale) * scale
        is_round = abs(midpoint - nearest) / midpoint <= 0.01

        near_ema: str | None = None
        if ema20 is not None and abs(midpoint - ema20) <= half_atr:
            near_ema = "EMA20"
        elif ema50 is not None and abs(midpoint - ema50) <= half_atr:
            near_ema = "EMA50"

        is_breakout = _check_prior_breakout(c, closes, highs, lows, n)

        result.append({**c, "is_round_number": is_round, "near_ema": near_ema, "is_prior_breakout_level": is_breakout})

    return result


def _check_prior_breakout(cluster: dict, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, n: int) -> bool:
    """True if this zone was previously a multi-week extreme that price broke through and hasn't revisited."""
    ptype = cluster["type"]
    zlo, zhi = cluster["zone_low"], cluster["zone_high"]
    idx = np.arange(n)

    if ptype == "LOW":
        # Was previously resistance: price was below zone, broke above zone_high, stayed above zone_low
        below_idx = np.where(closes < zlo)[0]
        if len(below_idx) < 3:
            return False
        last_below = below_idx[-1]
        above_after = np.where((closes > zhi) & (idx > last_below))[0]
        if len(above_after) == 0:
            return False
        breakout = above_after[0]
        post = closes[breakout + 1:]
        return len(post) == 0 or bool(np.all(post > zlo))
    else:
        # Was previously support: price was above zone, broke below zone_low, stayed below zone_high
        above_idx = np.where(closes > zhi)[0]
        if len(above_idx) < 3:
            return False
        last_above = above_idx[-1]
        below_after = np.where((closes < zlo) & (idx > last_above))[0]
        if len(below_after) == 0:
            return False
        breakout = below_after[0]
        post = closes[breakout + 1:]
        return len(post) == 0 or bool(np.all(post < zhi))


# ── 5. tag_zone_character ─────────────────────────────────────────────────────

def tag_zone_character(clusters: list[dict], ohlcv_df: Any) -> list[dict]:
    """
    Add behavioral features to each cluster:
      reversal_speed, volume_expansion_on_reversal,
      follow_through_strength, wick_rejection_count.
    """
    df = _prep_df(ohlcv_df)
    n = len(df)

    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values.astype(float)

    ema20_vals = (
        calculate_ema(df["close"], 20).values if n >= 20 else np.full(n, np.nan)
    )

    result: list[dict] = []
    for c in clusters:
        ptype = c["type"]
        zlo, zhi = c["zone_low"], c["zone_high"]

        # Rows where price interacted with the zone
        if ptype == "LOW":
            touch_mask = (lows <= zhi) & (highs >= zlo)
        else:
            touch_mask = (highs >= zlo) & (lows <= zhi)
        touch_idx = np.where(touch_mask)[0]

        if len(touch_idx) == 0:
            result.append({
                **c,
                "reversal_speed": 0.0,
                "volume_expansion_on_reversal": 1.0,
                "follow_through_strength": 0.0,
                "wick_rejection_count": 0,
            })
            continue

        reversal_speeds: list[float] = []
        vol_expansions: list[float] = []
        follow_throughs: list[float] = []
        wick_rejections = 0

        for idx in touch_idx:
            # Reversal speed: days until close crosses EMA20
            speed = 0.0
            for j in range(int(idx) + 1, min(int(idx) + 30, n)):
                e = ema20_vals[j]
                if np.isnan(e):
                    continue
                if ptype == "LOW" and closes[j] > e:
                    speed = float(j - idx)
                    break
                if ptype == "HIGH" and closes[j] < e:
                    speed = float(j - idx)
                    break
            reversal_speeds.append(speed)

            # Volume expansion vs preceding 5 days
            start = max(0, int(idx) - 5)
            pre_vols = volumes[start:int(idx)]
            avg_pre = float(np.mean(pre_vols)) if len(pre_vols) > 0 else 0.0
            vol_expansions.append(float(volumes[idx]) / avg_pre if avg_pre > 0 else 1.0)

            # Follow-through: % move in next 5 days
            if int(idx) + 5 < n:
                pct = (closes[int(idx) + 5] - closes[int(idx)]) / closes[int(idx)] * 100
                follow_throughs.append(float(pct))

            # Wick rejection count
            body = abs(closes[int(idx)] - opens[int(idx)])
            wick_range = highs[int(idx)] - lows[int(idx)]
            if wick_range >= 2 * body:
                if ptype == "LOW":
                    lower_wick = min(closes[int(idx)], opens[int(idx)]) - lows[int(idx)]
                    if lower_wick >= 0.5 * wick_range:
                        wick_rejections += 1
                else:
                    upper_wick = highs[int(idx)] - max(closes[int(idx)], opens[int(idx)])
                    if upper_wick >= 0.5 * wick_range:
                        wick_rejections += 1

        result.append({
            **c,
            "reversal_speed": round(float(np.mean(reversal_speeds)), 2),
            "volume_expansion_on_reversal": round(float(np.mean(vol_expansions)), 2),
            "follow_through_strength": round(float(np.mean(follow_throughs)) if follow_throughs else 0.0, 2),
            "wick_rejection_count": wick_rejections,
        })

    return result


# ── 6. extract_oi_levels ──────────────────────────────────────────────────────

def extract_oi_levels(
    chain_df: Any,
    spot: float,
    band_pct: float = 0.20,
    top_n: int = 5,
) -> list[dict]:
    """
    Strikes within band_pct of spot, top_n by OI per PE/CE type.
    Accepts DataFrame or list-of-dicts; column 'option_type' or 'type' both accepted.
    """
    if chain_df is None:
        return []
    if isinstance(chain_df, list):
        if not chain_df:
            return []
        df = pd.DataFrame(chain_df)
    else:
        if chain_df.empty:
            return []
        df = chain_df.copy()

    df.columns = [str(c).lower() for c in df.columns]

    # Normalise option type column name
    if "option_type" in df.columns and "type" not in df.columns:
        df = df.rename(columns={"option_type": "type"})

    # Normalise OI column
    if "oi" not in df.columns:
        for candidate in ("open_interest", "openinterest"):
            if candidate in df.columns:
                df = df.rename(columns={candidate: "oi"})
                break
    if "oi" not in df.columns:
        return []

    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0)
    df = df.dropna(subset=["strike"])

    band = spot * band_pct
    df = df[abs(df["strike"] - spot) <= band]

    out: list[dict] = []
    for otype in ("PE", "CE"):
        sub = df[df["type"] == otype].nlargest(top_n, "oi")
        for _, row in sub.iterrows():
            out.append({"strike": round(float(row["strike"]), 2), "type": otype, "oi": int(row["oi"])})

    return out


# ── 7. render_zone_chart ──────────────────────────────────────────────────────

def render_zone_chart(ohlcv_df: Any, clusters: list[dict], symbol: str) -> str:
    """
    Candlestick + volume chart with support/resistance zone bands.
    Saves PNG (longest side ≤ 1568 px at dpi=100). Returns absolute file path.
    """
    import mplfinance as mpf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = _prep_df(ohlcv_df)
    if "date" not in df.columns:
        df["date"] = pd.date_range(end=date.today(), periods=len(df), freq="B")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]

    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", wick="inherit", volume="in")
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", gridcolor="#e0e0e0", figcolor="#fafafa")

    # figsize: 15.68 wide × 8 tall → 1568×800 px at dpi=100 (longest side capped)
    fig, axes = mpf.plot(
        df,
        type="candle",
        volume=True,
        style=style,
        figsize=(15.68, 8.0),
        returnfig=True,
        title=f"\n{symbol} — Key Support & Resistance Levels",
    )
    ax_price = axes[0]

    for c in clusters:
        color = "#4caf50" if c["type"] == "LOW" else "#f44336"
        ax_price.axhspan(c["zone_low"], c["zone_high"], alpha=0.18, color=color, zorder=0)

    os.makedirs(_CHART_DIR, exist_ok=True)
    filename = f"{symbol}_{date.today().isoformat()}.png"
    path = os.path.join(_CHART_DIR, filename)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)

    return os.path.abspath(path)


# ── 8. build_stage1_output ────────────────────────────────────────────────────

def build_stage1_output(symbol: str, ohlcv_df: Any, chain_df: Any) -> dict:
    """
    Orchestrate functions 1-7 and return the full Stage-1 output dict.
    Handles stocks with < 180 rows gracefully.
    """
    df = _prep_df(ohlcv_df)

    tech = compute_technicals(df)
    ema20 = tech["ema20"]
    ema50 = tech["ema50"]
    atr14 = tech["atr14"]

    last_close = round(float(df["close"].iloc[-1]), 2) if not df.empty else 0.0

    # Stage 1→5: pivot detection, clustering, tagging
    pivots = detect_pivots(df)
    clusters = cluster_pivots(pivots, atr14)
    clusters = tag_confluence(clusters, ema20, ema50, df)
    clusters = tag_zone_character(clusters, df)

    # Build candidate_zones list (add zone midpoint as 'price')
    candidate_zones: list[dict] = []
    for c in clusters:
        midpoint = round((c["zone_low"] + c["zone_high"]) / 2, 2)
        candidate_zones.append({
            "price": midpoint,
            "zone_low": c["zone_low"],
            "zone_high": c["zone_high"],
            "touch_count": c["touch_count"],
            "last_touch_date": c["last_touch_date"],
            "avg_volume_ratio_on_touches": c["avg_volume_ratio_on_touches"],
            "is_round_number": c.get("is_round_number", False),
            "near_ema": c.get("near_ema"),
            "is_prior_breakout_level": c.get("is_prior_breakout_level", False),
            "reversal_speed": c.get("reversal_speed", 0.0),
            "volume_expansion_on_reversal": c.get("volume_expansion_on_reversal", 1.0),
            "follow_through_strength": c.get("follow_through_strength", 0.0),
            "wick_rejection_count": c.get("wick_rejection_count", 0),
        })

    oi_levels = extract_oi_levels(chain_df, last_close)

    chart_path = ""
    try:
        chart_path = render_zone_chart(df, clusters, symbol)
    except Exception as exc:
        logger.warning("render_zone_chart failed for %s: %s", symbol, exc)

    return {
        "symbol": symbol,
        "last_close": last_close,
        "ema20": ema20,
        "ema50": ema50,
        "atr14": atr14,
        "hv20": tech["hv20"],
        "hv60": tech["hv60"],
        "candidate_zones": candidate_zones,
        "oi_levels": oi_levels,
        "chart_image_path": chart_path,
    }
