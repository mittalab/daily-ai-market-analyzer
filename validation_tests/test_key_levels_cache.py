"""
Unit tests for _next_saturday_11am_ist() in api/key_levels.py.

Edge cases tested:
  - Mid-week (Wednesday)
  - Friday night
  - Saturday 10:59 AM IST (before cutoff → same Saturday)
  - Saturday 11:01 AM IST (after cutoff → next Saturday)
  - Sunday (should jump 6 days to next Saturday)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime
import pytz

from api.key_levels import _next_saturday_11am_ist

IST = pytz.timezone("Asia/Kolkata")


def ist(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Construct an IST-aware datetime."""
    return IST.localize(datetime(year, month, day, hour, minute, second))


class TestNextSaturday11amIST(unittest.TestCase):
    """
    Reference calendar (September 2026):
      Mon 14 | Tue 15 | Wed 16 | Thu 17 | Fri 18 | Sat 19 | Sun 20
      Mon 21 | ...                                | Sat 26
    """

    def _expect(self, now: datetime, expected: datetime) -> None:
        result = _next_saturday_11am_ist(now)
        self.assertEqual(
            result,
            expected,
            f"\n  now      = {now}\n  expected = {expected}\n  got      = {result}",
        )

    def test_midweek_wednesday(self):
        # Wed 2026-09-16 14:00 IST → Sat 2026-09-19 11:00 IST (3 days away)
        self._expect(
            now=ist(2026, 9, 16, 14, 0),
            expected=ist(2026, 9, 19, 11, 0),
        )

    def test_friday_night(self):
        # Fri 2026-09-18 23:59 IST → Sat 2026-09-19 11:00 IST (1 day away)
        self._expect(
            now=ist(2026, 9, 18, 23, 59),
            expected=ist(2026, 9, 19, 11, 0),
        )

    def test_saturday_before_cutoff(self):
        # Sat 2026-09-19 10:59 IST (before 11:00) → SAME Saturday 11:00 IST
        self._expect(
            now=ist(2026, 9, 19, 10, 59),
            expected=ist(2026, 9, 19, 11, 0),
        )

    def test_saturday_after_cutoff(self):
        # Sat 2026-09-19 11:01 IST (after 11:00) → NEXT Saturday 2026-09-26 11:00 IST
        self._expect(
            now=ist(2026, 9, 19, 11, 1),
            expected=ist(2026, 9, 26, 11, 0),
        )

    def test_saturday_exactly_at_cutoff(self):
        # Sat 2026-09-19 11:00:00 IST — the boundary itself is NOT before the deadline
        # so the cache should have already expired and we target NEXT Saturday
        self._expect(
            now=ist(2026, 9, 19, 11, 0, 0),
            expected=ist(2026, 9, 26, 11, 0),
        )

    def test_sunday(self):
        # Sun 2026-09-20 09:00 IST → Sat 2026-09-26 11:00 IST (6 days away)
        self._expect(
            now=ist(2026, 9, 20, 9, 0),
            expected=ist(2026, 9, 26, 11, 0),
        )

    def test_monday(self):
        # Mon 2026-09-21 08:00 IST → Sat 2026-09-26 11:00 IST (5 days away)
        self._expect(
            now=ist(2026, 9, 21, 8, 0),
            expected=ist(2026, 9, 26, 11, 0),
        )


if __name__ == "__main__":
    unittest.main()
