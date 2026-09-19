"""
Phase 3 — Multimodal Claude API caller for batched key-level synthesis.

call_claude_batch(batch, sector_map) -> dict
    Builds a single Messages API request interleaving per-stock text+image
    content blocks, sends it to Claude, and returns parsed JSON matching the
    Phase 2 schema.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import anthropic

from key_levels.prompts import SATURDAY_SYSTEM_PROMPT, SATURDAY_USER_TEMPLATE

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 16384


def _load_image_b64(path: str) -> str | None:
    """Read a PNG from disk and return its base64-encoded bytes."""
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("ascii")


def build_batch_content(batch: list[dict], sector_map: dict | None = None) -> list[dict]:
    """
    Build the 'content' list for the user message.

    Structure per stock:
      1. Text block — "=== STOCK: {symbol} ===" + JSON of Stage-1 data.
      2. Image block — base64 PNG of the annotated candlestick chart.
    Followed by one final text block with SATURDAY_USER_TEMPLATE (instructions).
    """
    sector_map = sector_map or {}
    content: list[dict] = []

    for stock in batch:
        symbol = stock["symbol"]
        sector_info = sector_map.get(symbol, {})

        # Strip chart_image_path from the text payload (image sent separately)
        text_data = {k: v for k, v in stock.items() if k != "chart_image_path"}
        text_data["sector"] = sector_info.get("sector", "UNKNOWN")
        text_data["sector_stance"] = sector_info.get("sector_stance", "NEUTRAL")

        content.append({
            "type": "text",
            "text": f"=== STOCK: {symbol} ===\n{json.dumps(text_data, default=str)}",
        })

        chart_path = stock.get("chart_image_path", "")
        img_b64 = _load_image_b64(chart_path)
        if img_b64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })
        else:
            logger.warning("No chart image for %s (path=%r); sending text only.", symbol, chart_path)

    # Final block: shared instructions
    content.append({"type": "text", "text": SATURDAY_USER_TEMPLATE})
    return content


def _merge_responses(a: dict, b: dict) -> dict:
    """Merge two call_claude_batch results into one combined response."""
    sa, sb = a.get("run_summary", {}), b.get("run_summary", {})
    return {
        "key_level_analysis": (
            a.get("key_level_analysis", []) + b.get("key_level_analysis", [])
        ),
        "run_summary": {
            "total_stocks_analyzed": (
                sa.get("total_stocks_analyzed", 0) + sb.get("total_stocks_analyzed", 0)
            ),
            "high_conviction_zone_count": (
                sa.get("high_conviction_zone_count", 0) + sb.get("high_conviction_zone_count", 0)
            ),
            "notable_observations": " | ".join(
                filter(None, [sa.get("notable_observations", ""), sb.get("notable_observations", "")])
            ),
        },
    }


def call_claude_batch(
    batch: list[dict],
    sector_map: dict | None = None,
) -> dict:
    """
    Send one batched multimodal request to Claude and return the parsed JSON.

    Returns a dict with keys:
      key_level_analysis: list[dict]
      run_summary: dict
    Raises on API error or JSON parse failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("call_claude_batch: ANTHROPIC_API_KEY is not set — API call will fail")
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    model = os.getenv("CLAUDE_MODEL", _DEFAULT_MODEL)

    if not batch:
        logger.warning("call_claude_batch: received empty batch — nothing to send")
        return {"key_level_analysis": [], "run_summary": {"total_stocks_analyzed": 0, "high_conviction_zone_count": 0, "notable_observations": ""}}

    client = anthropic.Anthropic(api_key=api_key)
    content = build_batch_content(batch, sector_map)

    logger.info(
        "call_claude_batch: %d stocks, %d content blocks, model=%s",
        len(batch),
        len(content),
        model,
    )

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=SATURDAY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    if not response.content:
        logger.error("call_claude_batch: Claude returned an empty content list — stop_reason=%s", response.stop_reason)
        raise ValueError(f"Empty response from Claude (stop_reason={response.stop_reason})")

    usage = response.usage
    logger.info(
        "Claude response received — input_tokens=%d, output_tokens=%d, stop_reason=%s",
        usage.input_tokens,
        usage.output_tokens,
        response.stop_reason,
    )

    if response.stop_reason == "max_tokens":
        if len(batch) > 1:
            mid = len(batch) // 2
            logger.warning(
                "call_claude_batch: TRUNCATED (max_tokens=%d) with %d stocks — "
                "auto-splitting into sub-batches of %d + %d",
                _MAX_TOKENS, len(batch), mid, len(batch) - mid,
            )
            left = call_claude_batch(batch[:mid], sector_map)
            right = call_claude_batch(batch[mid:], sector_map)
            return _merge_responses(left, right)
        logger.warning(
            "call_claude_batch: TRUNCATED (max_tokens=%d) on single-stock batch — "
            "JSON may be incomplete; proceeding with partial parse",
            _MAX_TOKENS,
        )

    raw = response.content[0].text.strip()

    # Strip accidental markdown fences if the model adds them despite instructions
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "call_claude_batch: JSON parse failed (%s). Raw response (first 500 chars): %r",
            exc, raw[:500],
        )
        raise

    if "key_level_analysis" not in parsed:
        logger.error(
            "call_claude_batch: 'key_level_analysis' key missing from response. Keys present: %s",
            list(parsed.keys()),
        )
        raise ValueError("Claude response missing 'key_level_analysis' key")

    n_returned = len(parsed["key_level_analysis"])
    n_sent = len(batch)
    if n_returned != n_sent:
        logger.warning(
            "call_claude_batch: sent %d stocks but got analysis for %d — missing: %s",
            n_sent,
            n_returned,
            sorted(set(s["symbol"] for s in batch) - {e["symbol"] for e in parsed["key_level_analysis"]}),
        )

    run_summary = parsed.get("run_summary", {})
    logger.info(
        "Parsed: %d stocks, %d high-conviction zones",
        n_returned,
        run_summary.get("high_conviction_zone_count", 0),
    )
    return parsed
