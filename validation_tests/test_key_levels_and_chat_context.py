"""
Unit tests for:
1. _parse_confluence_flags in api/key_levels.py
2. HEAD and GET requests on /api/session/today/chat-context in api/dashboard.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from starlette.testclient import TestClient

from api.key_levels import _parse_confluence_flags
from api.dashboard import router as dashboard_router


class TestParseConfluenceFlags(unittest.TestCase):
    def test_list_input(self):
        self.assertEqual(
            _parse_confluence_flags(["EMA50", "ROUND_NUMBER"]),
            ["EMA50", "ROUND_NUMBER"]
        )

    def test_json_string_input(self):
        self.assertEqual(
            _parse_confluence_flags('["EMA50", "ROUND_NUMBER", "OI_WALL"]'),
            ["EMA50", "ROUND_NUMBER", "OI_WALL"]
        )

    def test_comma_separated_string(self):
        self.assertEqual(
            _parse_confluence_flags("EMA50, ROUND_NUMBER, OI_WALL"),
            ["EMA50", "ROUND_NUMBER", "OI_WALL"]
        )

    def test_empty_string(self):
        self.assertEqual(_parse_confluence_flags(""), [])
        self.assertEqual(_parse_confluence_flags("   "), [])

    def test_none_input(self):
        self.assertEqual(_parse_confluence_flags(None), [])

    def test_invalid_json_fallback(self):
        self.assertEqual(
            _parse_confluence_flags("invalid_json, flag_b"),
            ["invalid_json", "flag_b"]
        )


class TestChatContextHeadAndGet(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dashboard_router)
        self.client = TestClient(app)

    @patch("database.client.get_client")
    def test_chat_context_404_on_no_session(self, mock_get_client):
        """When no session exists, HEAD returns 404 (not 405 Method Not Allowed)."""
        mock_db = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_db.table.return_value = mock_chain
        mock_get_client.return_value = mock_db

        res = self.client.head("/api/session/today/chat-context")
        self.assertEqual(res.status_code, 404)

    @patch("api.dashboard.get_latest_fii_dii")
    @patch("api.dashboard.get_trade_setups_by_date")
    @patch("database.client.get_client")
    def test_chat_context_head_returns_200_and_headers(
        self, mock_get_client, mock_get_setups, mock_fii
    ):
        """HEAD request succeeds with 200 and includes session headers."""
        mock_get_setups.return_value = []
        mock_fii.return_value = None

        mock_db = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.in_.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.side_effect = [
            MagicMock(data=[{"session_id": "sess_123", "session_date": "2026-09-19"}]),
            MagicMock(data=[]),
        ]
        mock_db.table.return_value = mock_chain
        mock_get_client.return_value = mock_db

        head_res = self.client.head("/api/session/today/chat-context")
        self.assertEqual(head_res.status_code, 200)
        self.assertEqual(head_res.headers.get("x-session-id"), "sess_123")
        self.assertEqual(head_res.headers.get("x-session-date"), "2026-09-19")
        self.assertIn("x-generated-at", head_res.headers)

        # GET request also returns 200
        mock_chain.execute.side_effect = [
            MagicMock(data=[{"session_id": "sess_123", "session_date": "2026-09-19"}]),
            MagicMock(data=[]),
        ]
        get_res = self.client.get("/api/session/today/chat-context")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.headers.get("x-session-id"), "sess_123")


if __name__ == "__main__":
    unittest.main()
