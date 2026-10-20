#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_schedule.py 的单元测试：高峰/空闲时段、节假日、刷新与缓存。"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import ai_schedule  # noqa: E402

BJ = ai_schedule.BEIJING_TZ


def bj(year: int, month: int, day: int, hour: int, minute: int = 0) -> _dt.datetime:
    return _dt.datetime(year, month, day, hour, minute, tzinfo=BJ)


class PeakWindowTests(unittest.TestCase):
    def test_weekday_peak_windows(self):
        # 2026-10-20 是周二
        for hour in (9, 10, 11, 14, 15, 16, 17):
            self.assertTrue(ai_schedule.HolidayCalendar().is_peak(bj(2026, 10, 20, hour)))
        for hour in (8, 12, 13, 18, 19, 23):
            self.assertFalse(ai_schedule.HolidayCalendar().is_peak(bj(2026, 10, 20, hour)))

    def test_exact_boundaries(self):
        cal = ai_schedule.HolidayCalendar()
        self.assertTrue(cal.is_peak(bj(2026, 10, 20, 9, 0)))
        self.assertTrue(cal.is_peak(bj(2026, 10, 20, 11, 59)))
        self.assertFalse(cal.is_peak(bj(2026, 10, 20, 12, 0)))
        self.assertTrue(cal.is_peak(bj(2026, 10, 20, 14, 0)))
        self.assertTrue(cal.is_peak(bj(2026, 10, 20, 17, 59)))
        self.assertFalse(cal.is_peak(bj(2026, 10, 20, 18, 0)))

    def test_weekend_is_idle(self):
        # 2026-10-24 周六 / 10-25 周日
        for day in (24, 25):
            for hour in (10, 15):
                self.assertFalse(ai_schedule.HolidayCalendar().is_peak(bj(2026, 10, day, hour)))

    def test_holiday_is_idle_even_on_weekday(self):
        cal = ai_schedule.HolidayCalendar([_dt.date(2026, 10, 1)])
        self.assertTrue(cal.is_holiday(_dt.date(2026, 10, 1)))
        self.assertFalse(cal.is_peak(bj(2026, 10, 1, 10)))
        self.assertFalse(cal.is_peak(bj(2026, 10, 1, 15)))

    def test_naive_and_utc_input_are_converted_to_beijing(self):
        cal = ai_schedule.HolidayCalendar()
        # UTC 02:00 == 北京 10:00（高峰）
        utc_moment = _dt.datetime(2026, 10, 20, 2, 0, tzinfo=_dt.timezone.utc)
        self.assertTrue(cal.is_peak(utc_moment))
        # naive 时间按北京时间理解
        self.assertTrue(cal.is_peak(_dt.datetime(2026, 10, 20, 10, 0)))
        self.assertFalse(cal.is_peak(_dt.datetime(2026, 10, 20, 12, 30)))

    def test_next_idle_start(self):
        cal = ai_schedule.HolidayCalendar()
        self.assertEqual(cal.next_idle_start(bj(2026, 10, 20, 10, 30)), bj(2026, 10, 20, 12, 0))
        self.assertEqual(cal.next_idle_start(bj(2026, 10, 20, 15, 5)), bj(2026, 10, 20, 18, 0))
        already_idle = bj(2026, 10, 20, 19, 0)
        self.assertEqual(cal.next_idle_start(already_idle), already_idle)


class HolidayDataTests(unittest.TestCase):
    def test_bundled_data_contains_2026_holidays(self):
        dates = ai_schedule.load_bundled_dates()
        self.assertIn(_dt.date(2026, 10, 1), dates)
        self.assertIn(_dt.date(2026, 10, 7), dates)
        self.assertGreater(len(dates), 20)

    def test_parse_holiday_payload(self):
        payload = {
            "days": [
                {"name": "国庆", "date": "2026-10-01", "isOffDay": True},
                {"name": "调休", "date": "2026-10-10", "isOffDay": False},
                {"name": "坏日期", "date": "2026-02-30", "isOffDay": True},
            ]
        }
        self.assertEqual(ai_schedule.parse_holiday_payload(payload), {_dt.date(2026, 10, 1)})

    def test_refresh_uses_cache_when_fetch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "holidays_cache.json"
            cache.write_text(
                json.dumps({"dates": ["2026-12-31"], "updated_at": "x"}),
                encoding="utf-8",
            )
            with mock.patch.object(ai_schedule, "fetch_holiday_dates", side_effect=RuntimeError("boom")):
                dates = ai_schedule.refresh_holidays(cache, years=(2026,))
            self.assertIn(_dt.date(2026, 12, 31), dates)
            self.assertIn(_dt.date(2026, 10, 1), dates)  # 随包数据仍在

    def test_refresh_fetches_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "holidays_cache.json"
            fake = {_dt.date(2027, 1, 1)}
            with mock.patch.object(ai_schedule, "fetch_holiday_dates", return_value=fake):
                dates = ai_schedule.refresh_holidays(cache, years=(2027,))
            self.assertIn(_dt.date(2027, 1, 1), dates)
            payload = json.loads(cache.read_text(encoding="utf-8"))
            self.assertIn("2027-01-01", payload["dates"])


if __name__ == "__main__":
    unittest.main()
