"""
Integration-style tests for api_client.py (Phase 3) and db.py (Phase 4).
All external calls (Anthropic API, Supabase) are mocked.

Run:  python -m pytest key_levels/tests/test_api_db.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import json
import unittest
from unittest.mock import MagicMock, patch

# ── Shared fixture ─────────────────────────────────────────────────────────────

MOCK_CLAUDE_RESPONSE_JSON = json.dumps({
    "key_level_analysis": [
        {
            "symbol": "RELIANCE",
            "analysis_date": "2026-09-13",
            "levels": [
                {
                    "level_type": "SUPPORT",
                    "zone_low": 2800.0,
                    "zone_high": 2830.0,
                    "conviction": "HIGH",
                    "touch_count": 3,
                    "last_touch_date": "2026-09-01",
                    "confluence_flags": ["EMA50", "ROUND_NUMBER", "VOLUME_CLIMAX"],
                    "reasoning": "Three clear touches at 2800-2830 with climactic volume on each reversal. EMA50 sits at 2815. Chart shows sharp V-reversals each time price enters this band.",
                },
                {
                    "level_type": "RESISTANCE",
                    "zone_low": 2950.0,
                    "zone_high": 2980.0,
                    "conviction": "MEDIUM",
                    "touch_count": 2,
                    "last_touch_date": "2026-08-20",
                    "confluence_flags": ["OI_WALL"],
                    "reasoning": "Two rejections at 2950-2980. Large CE OI wall at 3000 acts as ceiling. Chart shows upper wick rejection but volume was not climactic.",
                },
            ],
        },
        {
            "symbol": "HDFCBANK",
            "analysis_date": "2026-09-13",
            "levels": [
                {
                    "level_type": "SUPPORT",
                    "zone_low": 1820.0,
                    "zone_high": 1850.0,
                    "conviction": "LOW",
                    "touch_count": 1,
                    "last_touch_date": "2026-09-05",
                    "confluence_flags": ["ROUND_NUMBER"],
                    "reasoning": "Single touch at 1820-1850. Round number support near 1800. Insufficient touch history for high conviction.",
                }
            ],
        },
    ],
    "run_summary": {
        "total_stocks_analyzed": 2,
        "high_conviction_zone_count": 1,
        "notable_observations": "RELIANCE shows strong demand at 2800-2830.",
    },
})


def _make_batch() -> list[dict]:
    return [
        {
            "symbol": "RELIANCE",
            "last_close": 2900.0,
            "ema20": 2870.0,
            "ema50": 2815.0,
            "atr14": 40.0,
            "hv20": 0.18,
            "hv60": 0.16,
            "candidate_zones": [],
            "oi_levels": [],
            "chart_image_path": "",
        },
        {
            "symbol": "HDFCBANK",
            "last_close": 1900.0,
            "ema20": 1880.0,
            "ema50": 1840.0,
            "atr14": 25.0,
            "hv20": 0.14,
            "hv60": 0.13,
            "candidate_zones": [],
            "oi_levels": [],
            "chart_image_path": "",
        },
    ]


# ── Phase 3 tests ──────────────────────────────────────────────────────────────

class TestCallClaudeBatch(unittest.TestCase):

    @patch("key_levels.api_client.anthropic.Anthropic")
    def test_returns_parsed_dict(self, mock_anthropic_cls):
        """Mock API returns valid JSON → call_claude_batch returns parsed dict."""
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE_JSON)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        from key_levels.api_client import call_claude_batch
        result = call_claude_batch(_make_batch())

        self.assertIn("key_level_analysis", result)
        self.assertIn("run_summary", result)
        self.assertEqual(len(result["key_level_analysis"]), 2)

    @patch("key_levels.api_client.anthropic.Anthropic")
    def test_strips_markdown_fences(self, mock_anthropic_cls):
        """If model wraps JSON in markdown fences, they are stripped."""
        fenced = f"```json\n{MOCK_CLAUDE_RESPONSE_JSON}\n```"
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fenced)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        from key_levels.api_client import call_claude_batch
        result = call_claude_batch(_make_batch())
        self.assertIn("key_level_analysis", result)

    @patch("key_levels.api_client.anthropic.Anthropic")
    def test_api_called_with_system_prompt(self, mock_anthropic_cls):
        """Verify messages.create is called with a system message."""
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE_JSON)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_anthropic_cls.return_value = mock_client

        from key_levels.api_client import call_claude_batch
        call_claude_batch(_make_batch())

        call_kwargs = mock_client.messages.create.call_args
        self.assertIn("system", call_kwargs.kwargs)
        self.assertTrue(len(call_kwargs.kwargs["system"]) > 10)

    def test_build_batch_content_structure(self):
        """build_batch_content should produce text+image alternating blocks ending with instructions."""
        from key_levels.api_client import build_batch_content
        batch = _make_batch()
        content = build_batch_content(batch)

        # Last block must be text (instructions)
        self.assertEqual(content[-1]["type"], "text")
        # First block must be text (stock data header)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("=== STOCK: RELIANCE ===", content[0]["text"])

        # Count text blocks (one per stock + final instructions, no images since chart_image_path is empty)
        text_blocks = [b for b in content if b["type"] == "text"]
        self.assertEqual(len(text_blocks), len(batch) + 1)


# ── Phase 4 tests ──────────────────────────────────────────────────────────────

class TestUpsertKeyLevels(unittest.TestCase):

    def _mock_supabase_client(self):
        """Return a MagicMock that chains .table().update().eq().eq().execute()"""
        mock_client = MagicMock()
        # Make every chained call return the same mock so .execute() is accessible
        mock_chain = MagicMock()
        mock_chain.update.return_value = mock_chain
        mock_chain.insert.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = mock_chain
        return mock_client, mock_chain

    @patch("key_levels.db.get_client")
    def test_update_and_insert_called_per_symbol(self, mock_get_client):
        """Each symbol triggers one update (supersede) and one insert."""
        mock_client, mock_chain = self._mock_supabase_client()
        mock_get_client.return_value = mock_client

        from key_levels.db import upsert_key_levels
        response = json.loads(MOCK_CLAUDE_RESPONSE_JSON)
        result = upsert_key_levels(response)

        self.assertEqual(len(result["updated"]), 2)
        self.assertEqual(len(result["failed"]), 0)
        # update() called once per symbol
        self.assertEqual(mock_chain.update.call_count, 2)
        # insert() called once per symbol (both have levels)
        self.assertEqual(mock_chain.insert.call_count, 2)

    @patch("key_levels.db.get_client")
    def test_failed_symbol_logged_not_raised(self, mock_get_client):
        """If insert raises, symbol lands in 'failed'; no exception propagates."""
        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.update.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])
        # Make insert raise
        mock_chain.insert.side_effect = RuntimeError("DB error")
        mock_client.table.return_value = mock_chain
        mock_get_client.return_value = mock_client

        from key_levels.db import upsert_key_levels
        response = json.loads(MOCK_CLAUDE_RESPONSE_JSON)
        result = upsert_key_levels(response)

        self.assertGreater(len(result["failed"]), 0)

    @patch("key_levels.db.get_client")
    def test_confluence_flags_serialised_as_json_string(self, mock_get_client):
        """confluence_flags list is stored as a JSON string in the DB row."""
        captured_rows: list = []

        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.update.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])

        def capture_insert(rows):
            captured_rows.extend(rows)
            return mock_chain

        mock_chain.insert.side_effect = capture_insert
        mock_client.table.return_value = mock_chain
        mock_get_client.return_value = mock_client

        from key_levels.db import upsert_key_levels
        response = json.loads(MOCK_CLAUDE_RESPONSE_JSON)
        upsert_key_levels(response)

        for row in captured_rows:
            flags = row["confluence_flags"]
            self.assertIsInstance(flags, str)
            parsed = json.loads(flags)
            self.assertIsInstance(parsed, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
