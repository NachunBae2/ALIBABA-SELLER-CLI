# 테스트 전용 파일 — CLI 실행에 쓰이지 않음. 안의 ID·금액·클릭 수는 동작 확인용 예시 값이며 실제 셀러·고객 데이터가 아님.
import datetime as dt
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import pacific_tz  # noqa: E402

UTC = dt.timezone.utc
# (UTC instant, expected Pacific wall time, offset hours) around 2026 DST edges
CASES = [
    (dt.datetime(2026, 1, 15, 12, tzinfo=UTC), dt.datetime(2026, 1, 15, 4), -8),
    (dt.datetime(2026, 3, 8, 9, 59, tzinfo=UTC), dt.datetime(2026, 3, 8, 1, 59), -8),
    (dt.datetime(2026, 3, 8, 10, 0, tzinfo=UTC), dt.datetime(2026, 3, 8, 3, 0), -7),
    (dt.datetime(2026, 7, 4, 6, 30, tzinfo=UTC), dt.datetime(2026, 7, 3, 23, 30), -7),
    (dt.datetime(2026, 11, 1, 8, 30, tzinfo=UTC), dt.datetime(2026, 11, 1, 1, 30), -7),
    (dt.datetime(2026, 11, 1, 9, 30, tzinfo=UTC), dt.datetime(2026, 11, 1, 1, 30), -8),
    (dt.datetime(2026, 11, 1, 10, 0, tzinfo=UTC), dt.datetime(2026, 11, 1, 2, 0), -8),
]


class PacificFallback(unittest.TestCase):
    def test_builtin_rule_matches_known_instants(self):
        tz = pacific_tz.USPacific()
        for utc, wall, hours in CASES:
            local = utc.astimezone(tz)
            self.assertEqual(local.replace(tzinfo=None, fold=0), wall, utc)
            self.assertEqual(local.utcoffset(), dt.timedelta(hours=hours), utc)
            self.assertEqual(local.astimezone(UTC), utc, utc)

    def test_used_when_zone_database_missing(self):
        import zoneinfo
        with patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("x")):
            self.assertIsInstance(pacific_tz.pacific(), pacific_tz.USPacific)

    def test_same_dates_as_zoneinfo_hourly_2026(self):
        try:
            from zoneinfo import ZoneInfo
            ref = ZoneInfo("America/Los_Angeles")
        except Exception:
            self.skipTest("no zone database here")
        tz, t = pacific_tz.USPacific(), dt.datetime(2026, 1, 1, tzinfo=UTC)
        while t.year == 2026:
            self.assertEqual(t.astimezone(tz).utcoffset(), t.astimezone(ref).utcoffset(), t)
            t += dt.timedelta(hours=1)


if __name__ == "__main__":
    unittest.main()
