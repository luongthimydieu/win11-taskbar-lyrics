#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task_archive.py 的单元测试：完成归档、按完成时间归类、删除后仍保留、归档 JSON。"""

from __future__ import annotations

import datetime as _dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import core  # noqa: E402
import task_archive  # noqa: E402

BJ = _dt.timezone(_dt.timedelta(hours=8))


def iso(year, month, day, hour, minute=0) -> str:
    return _dt.datetime(year, month, day, hour, minute, tzinfo=BJ).isoformat(timespec="seconds")


class ArchiveTests(unittest.TestCase):
    def test_add_event_and_dedupe(self):
        archive = task_archive.empty_archive()
        todo = {"id": "td-1", "text": "写报告", "priority": "normal", "created_at": iso(2026, 10, 20, 9)}
        archive, event = task_archive.add_event(
            archive, todo, iso(2026, 10, 20, 18, 30), "写完初稿", "先列大纲"
        )
        self.assertEqual(event["text"], "写报告")
        self.assertEqual(event["completed_at"], iso(2026, 10, 20, 18, 30))
        self.assertEqual(event["done_note"], "写完初稿")
        # 同一 todo + 完成时间不重复；空字段可以被补全
        archive, event2 = task_archive.add_event(
            archive, todo, iso(2026, 10, 20, 18, 30), "补记", ""
        )
        self.assertEqual(len(archive["events"]), 1)
        self.assertEqual(event2["done_note"], "写完初稿")

    def test_events_for_day_uses_completion_time(self):
        archive = task_archive.empty_archive()
        todo = {"id": "a", "text": "任务A"}
        archive, _ = task_archive.add_event(archive, todo, iso(2026, 10, 19, 23, 50))
        archive, _ = task_archive.add_event(archive, todo | {"id": "b", "text": "任务B"}, iso(2026, 10, 20, 0, 10))
        day19 = task_archive.events_for_day(archive, _dt.date(2026, 10, 19))
        day20 = task_archive.events_for_day(archive, _dt.date(2026, 10, 20))
        self.assertEqual([item["text"] for item in day19], ["任务A"])
        self.assertEqual([item["text"] for item in day20], ["任务B"])

    def test_sync_doc_completed_after_deletion(self):
        doc, todo = core.add_todo(core.empty_doc(), "会被删除的已完成任务")
        doc = core.set_done(doc, todo["id"], True, "完成情况", "心得")
        archive = task_archive.empty_archive()
        archive, added = task_archive.sync_completed_from_doc(archive, doc)
        self.assertEqual(added, 1)
        self.assertEqual(archive["events"][0]["done_note"], "完成情况")
        # 删除待办后，归档仍在
        doc = core.delete_todo(doc, todo["id"])
        self.assertEqual(doc["todos"], [])
        self.assertEqual(len(task_archive.events_for_day(archive, core.current_day())), 1)

    def test_archive_payload_includes_completed_unfinished_overdue(self):
        archive = task_archive.empty_archive()
        archive, _ = task_archive.add_event(
            archive, {"id": "c1", "text": "完成项"}, iso(2026, 10, 20, 10)
        )
        doc = core.empty_doc()
        doc, _ = core.add_todo(doc, "未完成项", due_date="2026-10-20")
        doc, _ = core.add_todo(doc, "逾期项", due_date="2026-10-18")
        payload = task_archive.archive_payload(archive, _dt.date(2026, 10, 20), doc)
        self.assertEqual([item["text"] for item in payload["completed"]], ["完成项"])
        self.assertEqual([item["text"] for item in payload["unfinished"]], ["未完成项"])
        self.assertEqual([item["text"] for item in payload["overdue"]], ["逾期项"])

    def test_prune_old_events(self):
        archive = task_archive.empty_archive()
        archive, _ = task_archive.add_event(archive, {"id": "old", "text": "旧"}, "2024-01-01T10:00:00+08:00")
        archive, _ = task_archive.add_event(archive, {"id": "new", "text": "新"}, iso(2026, 10, 20, 10))
        pruned = task_archive.prune(archive, keep_days=30)
        self.assertEqual([item["text"] for item in pruned["events"]], ["新"])

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            archive, _ = task_archive.add_event(
                task_archive.empty_archive(), {"id": "x", "text": "任务"}, iso(2026, 10, 20, 9)
            )
            self.assertTrue(task_archive.save_archive(path, archive))
            loaded = task_archive.load_archive(path)
            self.assertEqual(len(loaded["events"]), 1)
            self.assertEqual(loaded["events"][0]["text"], "任务")


if __name__ == "__main__":
    unittest.main()
