#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py 的离线单元测试。

特点：不需要联网、不需要 PyQt6、不影响真实配置，可直接在纯 Python 环境跑：

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop"))

import core  # noqa: E402

T1 = "2026-10-17T08:00:00+08:00"
T2 = "2026-10-17T09:00:00+08:00"
T3 = "2026-10-17T10:00:00+08:00"
T4 = "2026-10-17T11:00:00+08:00"


def make_todo(todo_id="t1", text="写周报", updated=T1, **overrides):
    payload = {
        "id": todo_id,
        "text": text,
        "priority": "normal",
        "done": False,
        "created_at": T1,
        "updated_at": updated,
        "remind_at": "",
        "remind_date": "",
        "notified_at": "",
        "done_at": "",
    }
    payload.update(overrides)
    return payload


def make_doc(*todos, tombstones=None, version=1):
    return core.normalize_doc(
        {
            "schema": 3,
            "version": version,
            "updated_at": T1,
            "todos": list(todos),
            "tombstones": list(tombstones or []),
        }
    )


def make_ledger(*entries, tombstones=None, version=1):
    return core.normalize_ledger(
        {
            "schema": 1,
            "version": version,
            "updated_at": T1,
            "entries": list(entries),
            "tombstones": list(tombstones or []),
        }
    )


def make_entry(
    entry_id="e1",
    date="2026-10-20",
    kind="expense",
    amount=12.5,
    category="餐饮",
    note="",
    created=T1,
    updated=None,
):
    return {
        "id": entry_id,
        "date": date,
        "type": kind,
        "amount": amount,
        "category": category,
        "note": note,
        "created_at": created,
        "updated_at": updated or created,
    }


# ---------------------------------------------------------------------------
# 文档模型与合并协议
# ---------------------------------------------------------------------------

class NormalizeTests(unittest.TestCase):
    def test_legacy_todo_fields(self):
        todo = core.normalize_todo(
            {
                "id": "old-1",
                "text": "  提交周报  ",
                "remind_time": "15:30",
                "priority": "HIGH",
                "createdAt": T1,
                "updatedAt": T2,
            }
        )
        self.assertIsNotNone(todo)
        self.assertEqual(todo["text"], "提交周报")
        self.assertEqual(todo["remind_at"], "15:30")
        self.assertEqual(todo["priority"], "high")
        self.assertEqual(core.iso_rank(todo["created_at"]), core.iso_rank(T1))
        self.assertEqual(core.iso_rank(todo["updated_at"]), core.iso_rank(T2))

    def test_invalid_reminder_fields_dropped(self):
        todo = core.normalize_todo(
            {"text": "x", "remind_at": "25:99", "remind_date": "2026-13-40"}
        )
        self.assertEqual(todo["remind_at"], "")
        self.assertEqual(todo["remind_date"], "")

    def test_empty_text_dropped(self):
        self.assertIsNone(core.normalize_todo({"text": "   "}))
        self.assertIsNone(core.normalize_todo("not a dict"))

    def test_doc_dedup_and_sort(self):
        doc = make_doc(make_todo("b"), make_todo("a"), make_todo("a", text="重复"))
        ids = [item["id"] for item in doc["todos"]]
        self.assertEqual(ids, ["a", "b"])

    def test_done_without_done_at_gets_timestamp(self):
        todo = core.normalize_todo({"id": "d", "text": "done", "done": True, "updated_at": T2})
        self.assertTrue(todo["done"])
        self.assertEqual(core.iso_rank(todo["done_at"]), core.iso_rank(T2))

    def test_new_id_is_unique(self):
        self.assertNotEqual(core.new_id(), core.new_id())

    def test_new_id_timestamp_is_monotonic(self):
        stamps = [int(core.new_id().split("-")[1]) for _ in range(50)]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), len(stamps))

    def test_same_second_todos_keep_creation_order(self):
        # created_at 只有秒级精度：同一秒内连续创建（例如 AI 拆解步骤）也要按顺序
        doc = core.empty_doc()
        doc, first = core.add_todo(doc, "第一项")
        doc, second = core.add_todo(doc, "第二项")
        self.assertEqual([item["id"] for item in core.todos_for_day(doc, core.todo_day(first))],
                         [first["id"], second["id"]])


class MergeTests(unittest.TestCase):
    def test_remote_newer_wins(self):
        local = make_doc(make_todo(text="旧内容", updated=T1))
        remote = make_doc(make_todo(text="新内容", updated=T2))
        merged = core.merge_docs(local, remote)
        self.assertEqual(merged["todos"][0]["text"], "新内容")

    def test_local_newer_wins(self):
        local = make_doc(make_todo(text="本地新内容", updated=T2))
        remote = make_doc(make_todo(text="远端旧内容", updated=T1))
        merged = core.merge_docs(local, remote)
        self.assertEqual(merged["todos"][0]["text"], "本地新内容")

    def test_union_of_both_sides(self):
        local = make_doc(make_todo("t1", "本地条目"))
        remote = make_doc(make_todo("t2", "远端条目"))
        merged = core.merge_docs(local, remote)
        self.assertEqual(sorted(item["id"] for item in merged["todos"]), ["t1", "t2"])

    def test_tombstone_deletes_older_item(self):
        local = make_doc(make_todo("t1", "待删除", updated=T1))
        remote = make_doc(tombstones=[{"id": "t1", "deleted_at": T2}])
        merged = core.merge_docs(local, remote)
        self.assertEqual(merged["todos"], [])

    def test_newer_edit_revives_item(self):
        # 条目在删除之后又被更新 → 以更新为准（复活）
        local = make_doc(make_todo("t1", "复活条目", updated=T3))
        remote = make_doc(tombstones=[{"id": "t1", "deleted_at": T2}])
        merged = core.merge_docs(local, remote)
        self.assertEqual([item["id"] for item in merged["todos"]], ["t1"])
        self.assertEqual(merged["tombstones"], [])

    def test_delete_is_idempotent_across_clients(self):
        doc = make_doc(make_todo("t1"))
        after_delete = core.delete_todo(doc, "t1")
        merged = core.merge_docs(after_delete, doc)
        self.assertEqual(merged["todos"], [])
        self.assertEqual(len(merged["tombstones"]), 1)

    def test_version_increments(self):
        a = make_doc(version=3)
        b = make_doc(version=7)
        self.assertEqual(core.merge_docs(a, b)["version"], 8)

    def test_merge_is_content_idempotent(self):
        doc = make_doc(make_todo("t1"), make_todo("t2", text="另一条"))
        merged = core.merge_docs(doc, doc)
        self.assertEqual(core.canonical(merged), core.canonical(doc))

    def test_canonical_ignores_version_and_updated_at(self):
        a = make_doc(make_todo("t1"), version=1)
        b = make_doc(make_todo("t1"), version=9)
        b["updated_at"] = T4
        self.assertTrue(core.docs_equal(a, b))

    def test_needs_push(self):
        local = make_doc(make_todo("t1", "本地新增"))
        remote = make_doc()
        self.assertTrue(core.needs_push(local, remote))
        self.assertFalse(core.needs_push(remote, remote))


# ---------------------------------------------------------------------------
# 待办操作与提醒调度
# ---------------------------------------------------------------------------

class DocOperationTests(unittest.TestCase):
    def test_add_todo(self):
        doc, todo = core.add_todo(
            core.empty_doc(), "交报告", priority="high", remind_at="18:00", remind_date="2026-10-18"
        )
        self.assertEqual(len(doc["todos"]), 1)
        self.assertEqual(doc["todos"][0]["id"], todo["id"])
        self.assertEqual(todo["priority"], "high")
        self.assertEqual(todo["remind_at"], "18:00")
        self.assertEqual(todo["remind_date"], "2026-10-18")

    def test_set_done_and_undo(self):
        doc, todo = core.add_todo(core.empty_doc(), "任务")
        done = core.set_done(doc, todo["id"], True)
        self.assertTrue(done["todos"][0]["done"])
        self.assertTrue(done["todos"][0]["done_at"])
        notified = core.mark_notified(done, todo["id"])
        self.assertTrue(notified["todos"][0]["notified_at"])
        undone = core.set_done(notified, todo["id"], False)
        self.assertFalse(undone["todos"][0]["done"])
        self.assertEqual(undone["todos"][0]["done_at"], "")
        self.assertEqual(undone["todos"][0]["notified_at"], "")

    def test_delete_writes_tombstone(self):
        doc, todo = core.add_todo(core.empty_doc(), "要删的")
        deleted = core.delete_todo(doc, todo["id"])
        self.assertEqual(deleted["todos"], [])
        self.assertEqual(deleted["tombstones"][0]["id"], todo["id"])

    def test_snooze_moves_reminder_and_clears_notified(self):
        doc, todo = core.add_todo(
            core.empty_doc(), "稍后提醒", remind_at="08:00", remind_date="2026-10-17"
        )
        doc = core.mark_notified(doc, todo["id"])
        snoozed = core.snooze_todo(doc, todo["id"], minutes=10)
        item = snoozed["todos"][0]
        self.assertEqual(item["notified_at"], "")
        target = core.reminder_datetime(item)
        self.assertIsNotNone(target)
        self.assertGreater(target.timestamp(), core.now_local().timestamp() - 5)

    def test_pending_sorted_by_priority_then_reminder(self):
        doc = core.empty_doc()
        doc, _ = core.add_todo(doc, "普通-晚", priority="normal", remind_at="20:00", remind_date="2026-10-17")
        doc, _ = core.add_todo(doc, "紧急-晚", priority="high", remind_at="19:00", remind_date="2026-10-17")
        doc, _ = core.add_todo(doc, "紧急-早", priority="high", remind_at="09:00", remind_date="2026-10-17")
        doc, _ = core.add_todo(doc, "无提醒", priority="normal")
        texts = [item["text"] for item in core.pending_todos(doc)]
        self.assertEqual(texts[:2], ["紧急-早", "紧急-晚"])
        self.assertIn("普通-晚", texts)
        self.assertEqual(texts[-1], "无提醒")


class ReminderTests(unittest.TestCase):
    def _at(self, month, day, hour, minute):
        return _dt.datetime(2026, month, day, hour, minute, 0).astimezone()

    def test_reminder_datetime_uses_explicit_date(self):
        todo = make_todo(remind_at="15:30", remind_date="2026-10-17")
        target = core.reminder_datetime(todo)
        self.assertEqual((target.month, target.day, target.hour, target.minute), (10, 17, 15, 30))

    def test_legacy_reminder_uses_created_date(self):
        todo = make_todo(remind_at="09:00", remind_date="", created=T1)
        self.assertEqual(core.reminder_datetime(todo).date(), _dt.date(2026, 10, 17))

    def test_no_remind_at(self):
        self.assertIsNone(core.reminder_datetime(make_todo(remind_at="")))
        self.assertFalse(core.due_reminder(make_todo(remind_at="")))

    def test_due_after_target(self):
        todo = make_todo(remind_at="15:30", remind_date="2026-10-17")
        self.assertTrue(core.due_reminder(todo, self._at(10, 17, 15, 31)))
        self.assertTrue(core.due_reminder(todo, self._at(10, 17, 15, 30)))

    def test_not_due_before_target(self):
        todo = make_todo(remind_at="15:30", remind_date="2026-10-17")
        self.assertFalse(core.due_reminder(todo, self._at(10, 17, 15, 29)))
        self.assertFalse(core.due_reminder(todo, self._at(10, 16, 23, 59)))

    def test_expired_beyond_grace_window(self):
        todo = make_todo(remind_at="15:30", remind_date="2026-10-17")
        # 第二天 16:00 已超过 24 小时补提醒窗口
        self.assertFalse(core.due_reminder(todo, self._at(10, 18, 16, 0)))
        # 第二天 15:00 仍在窗口内（23.5 小时）
        self.assertTrue(core.due_reminder(todo, self._at(10, 18, 15, 0)))

    def test_notified_or_done_not_due(self):
        todo = make_todo(remind_at="15:30", remind_date="2026-10-17", notified_at=T2)
        self.assertFalse(core.due_reminder(todo, self._at(10, 17, 15, 31)))
        done = make_todo(remind_at="15:30", remind_date="2026-10-17", done=True)
        self.assertFalse(core.due_reminder(done, self._at(10, 17, 15, 31)))

    def test_format_reminder_labels(self):
        today = core.now_local().date()
        todo_today = make_todo(remind_at="15:30", remind_date=today.isoformat())
        self.assertEqual(core.format_reminder(todo_today), "今天 15:30")
        tomorrow = (core.now_local() + _dt.timedelta(days=1)).date()
        todo_tomorrow = make_todo(remind_at="09:00", remind_date=tomorrow.isoformat())
        self.assertEqual(core.format_reminder(todo_tomorrow), "明天 09:00")

    def test_format_todo_line(self):
        todo = core.normalize_todo(
            {
                "id": "t1",
                "text": "交周报",
                "priority": "high",
                "remind_at": "15:30",
                "remind_date": "2026-10-17",
            }
        )
        line = core.format_todo_line(todo, pending_count=3)
        self.assertIn("🔥", line)
        self.assertIn("(3)", line)
        self.assertIn("交周报", line)
        self.assertIn("15:30", line)

    def test_upcoming_reminders(self):
        doc = make_doc(
            make_todo("a", "到点1", remind_at="15:30", remind_date="2026-10-17"),
            make_todo("b", "到点2", remind_at="16:00", remind_date="2026-10-17"),
            make_todo("c", "明天", remind_at="09:00", remind_date="2026-10-18"),
        )
        due = core.upcoming_reminders(doc, self._at(10, 17, 16, 30))
        self.assertEqual([item["id"] for item in due], ["a", "b"])


# ---------------------------------------------------------------------------
# LRC 歌词
# ---------------------------------------------------------------------------

class LrcTests(unittest.TestCase):
    def test_basic_parse(self):
        lyrics = core.parse_lrc("[00:03.50]第二行\n[00:01.00]第一行\n[00:00.00]标题")
        self.assertEqual([item[1] for item in lyrics], ["标题", "第一行", "第二行"])
        self.assertAlmostEqual(lyrics[1][0], 1.0)
        self.assertAlmostEqual(lyrics[2][0], 3.5)

    def test_multi_timestamp_line(self):
        lyrics = core.parse_lrc("[00:01.00][00:05.00]重复歌词")
        self.assertEqual(len(lyrics), 2)
        self.assertAlmostEqual(lyrics[0][0], 1.0)
        self.assertAlmostEqual(lyrics[1][0], 5.0)

    def test_offset_tag(self):
        lyrics = core.parse_lrc("[offset:1000]\n[00:01.00]第一行")
        self.assertAlmostEqual(lyrics[0][0], 2.0)

    def test_empty_and_invalid(self):
        self.assertEqual(core.parse_lrc(""), [])
        self.assertEqual(core.parse_lrc("没有时间戳的纯文本"), [])
        self.assertEqual(core.parse_lrc(None), [])

    def test_current_lyric(self):
        lyrics = core.parse_lrc("[00:01.00]第一行\n[00:03.00]第二行\n[00:05.00]第三行")
        self.assertEqual(core.current_lyric(lyrics, 0.0), "")
        self.assertEqual(core.current_lyric(lyrics, 1.5), "第一行")
        self.assertEqual(core.current_lyric(lyrics, 3.0), "第二行")
        self.assertEqual(core.current_lyric(lyrics, 99.0), "第三行")
        self.assertEqual(core.current_lyric([], 10.0), "")


# ---------------------------------------------------------------------------
# 网络客户端（用假 session，不联网）
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    """极简 requests.Session 替身：按顺序返回预设响应，记录所有调用。"""

    def __init__(self, get_responses=None, post_responses=None, patch_responses=None, put_responses=None):
        self.headers = {}
        self._get = list(get_responses or [])
        self._post = list(post_responses or [])
        self._patch = list(patch_responses or [])
        self.calls = []

    @staticmethod
    def _next(queue):
        if not queue:
            raise AssertionError("伪造的 HTTP 响应队列已耗尽")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next(self._get)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next(self._post)

    def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return self._next(self._patch)


class NetEaseTests(unittest.TestCase):
    def _session(self, comments=None):
        return FakeSession(
            post_responses=[FakeResponse({"result": {"songs": [{"id": 1901371647}]}})],
            get_responses=[
                FakeResponse({"lrc": {"lyric": "[00:01.00]第一行\n[00:03.00]第二行"}}),
                FakeResponse(
                    {
                        "hotComments": comments
                        if comments is not None
                        else [
                            {"content": "评论一\n换行内容", "likedCount": 100, "user": {"nickname": "小明"}},
                            {"content": "评论二", "likedCount": 50, "user": {"nickname": "小红"}},
                        ]
                    }
                ),
            ],
        )

    def test_fetch_bundle_success(self):
        session = self._session()
        client = core.NetEaseClient(session=session)
        bundle = client.fetch_song_bundle("晴天", "周杰伦")
        self.assertTrue(bundle["ok"], bundle.get("error"))
        self.assertEqual(bundle["song_id"], 1901371647)
        self.assertEqual([item[1] for item in bundle["lyrics"]], ["第一行", "第二行"])
        self.assertEqual(len(bundle["hot_comments"]), 2)
        self.assertIn("👍100", bundle["hot_comments"][0])
        self.assertIn("小明", bundle["hot_comments"][0])
        self.assertNotIn("\n", bundle["hot_comments"][0])

    def test_success_is_cached(self):
        session = self._session()
        client = core.NetEaseClient(session=session)
        client.fetch_song_bundle("晴天", "周杰伦")
        calls_after_first = len(session.calls)
        client.fetch_song_bundle("晴天", "周杰伦")
        self.assertEqual(len(session.calls), calls_after_first)

    def test_failure_is_not_cached_and_retries(self):
        import requests

        session = FakeSession(
            post_responses=[requests.ConnectionError("boom"), FakeResponse({"result": {"songs": []}})]
        )
        client = core.NetEaseClient(session=session)
        first = client.fetch_song_bundle("不存在的歌", "")
        self.assertFalse(first["ok"])
        second = client.fetch_song_bundle("不存在的歌", "")
        self.assertFalse(second["ok"])
        # 失败不缓存：两次都发起了网络请求
        self.assertEqual(len([c for c in session.calls if c[0] == "POST"]), 2)

    def test_missing_song_error_message(self):
        session = FakeSession(post_responses=[FakeResponse({"result": {"songs": []}})])
        client = core.NetEaseClient(session=session)
        bundle = client.fetch_song_bundle("不存在的歌", "")
        self.assertFalse(bundle["ok"])
        self.assertIn("未在网易云找到", bundle["error"])

    def test_format_comment_truncates_and_sanitizes(self):
        long_content = "字" * 200
        line = core.NetEaseClient.format_comment(
            {"content": long_content, "likedCount": "1000", "user": {"nickname": "长评论"}}
        )
        self.assertIn("…", line)
        self.assertIn("👍1000", line)
        self.assertLess(len(line), 130)
        self.assertIsNone(core.NetEaseClient.format_comment({"content": "   "}))
        self.assertIsNone(core.NetEaseClient.format_comment("bad"))


class GistStoreTests(unittest.TestCase):
    @staticmethod
    def gist_payload(doc, etag=None):
        return FakeResponse(
            {
                "files": {
                    core.GIST_FILENAME: {
                        "content": json.dumps(core.normalize_doc(doc), ensure_ascii=False)
                    }
                }
            },
            headers={"ETag": etag or '"etag-1"'},
        )

    def test_get_doc_ok(self):
        doc = make_doc(make_todo("t1", "云端待办"))
        session = FakeSession(get_responses=[self.gist_payload(doc)])
        store = core.GitHubGistStore("token", "gist123", session=session)
        fetch = store.get_doc()
        self.assertEqual(fetch.status, "ok")
        self.assertEqual(fetch.doc["todos"][0]["text"], "云端待办")
        self.assertEqual(fetch.etag, '"etag-1"')

    def test_get_doc_not_modified(self):
        session = FakeSession(get_responses=[FakeResponse(status=304)])
        store = core.GitHubGistStore("token", "gist123", session=session)
        fetch = store.get_doc(etag='"etag-1"')
        self.assertEqual(fetch.status, "not_modified")
        self.assertEqual(session.calls[0][2]["headers"].get("If-None-Match"), '"etag-1"')

    def test_get_doc_401_raises_api_error(self):
        session = FakeSession(get_responses=[FakeResponse(status=401)])
        store = core.GitHubGistStore("bad-token", "gist123", session=session)
        with self.assertRaises(core.GitHubApiError) as context:
            store.get_doc()
        self.assertEqual(context.exception.status, 401)

    def test_missing_file_raises(self):
        session = FakeSession(get_responses=[FakeResponse({"files": {}})])
        store = core.GitHubGistStore("token", "gist123", session=session)
        with self.assertRaises(core.GitHubApiError):
            store.get_doc()

    def test_sync_pushes_local_changes(self):
        remote = core.empty_doc()
        local = core.add_todo(core.empty_doc(), "交周报")[0]
        session = FakeSession(
            get_responses=[self.gist_payload(remote)],
            patch_responses=[FakeResponse(self.gist_payload(local).json(), headers={"ETag": '"etag-2"'})],
        )
        store = core.GitHubGistStore("token", "gist123", session=session)
        report = store.sync(local, etag=None)
        self.assertTrue(report.pushed)
        self.assertEqual(report.etag, '"etag-2"')
        patch_calls = [call for call in session.calls if call[0] == "PATCH"]
        self.assertEqual(len(patch_calls), 1)
        body = patch_calls[0][2]["json"]
        pushed_content = json.loads(body["files"][core.GIST_FILENAME]["content"])
        self.assertEqual([item["text"] for item in pushed_content["todos"]], ["交周报"])
        merged_ids = {item["id"] for item in report.doc["todos"]}
        self.assertEqual(merged_ids, {local["todos"][0]["id"]})

    def test_sync_without_changes_does_not_push(self):
        doc = make_doc(make_todo("t1", "已经同步"))
        session = FakeSession(get_responses=[self.gist_payload(doc)])
        store = core.GitHubGistStore("token", "gist123", session=session)
        report = store.sync(doc, etag=None)
        self.assertFalse(report.pushed)
        self.assertEqual([call for call in session.calls if call[0] == "PATCH"], [])

    def test_create_gist(self):
        session = FakeSession(
            post_responses=[FakeResponse({"id": "new-gist-id"}, headers={"ETag": '"e"'})]
        )
        store = core.GitHubGistStore("token", "", session=session)
        gist_id = store.create_gist(core.empty_doc())
        self.assertEqual(gist_id, "new-gist-id")
        self.assertEqual(store.gist_id, "new-gist-id")
        self.assertTrue(store.configured)
        body = session.calls[0][2]["json"]
        self.assertFalse(body["public"])


# ---------------------------------------------------------------------------
# “下一项待办”与“有意思的热评”筛选
# ---------------------------------------------------------------------------

class NextTodoTests(unittest.TestCase):
    def test_next_todo_follows_creation_order(self):
        doc = core.normalize_doc(
            {
                "todos": [
                    make_todo("t3", "第三项", updated=T3, created_at=T3, priority="high"),
                    make_todo("t1", "第一项", updated=T1, created_at=T1),
                    make_todo("t2", "第二项", updated=T2, created_at=T2),
                ]
            }
        )
        # 手机端按顺序上传：始终显示最老的一条未完成，而不是随机轮播
        self.assertEqual(core.next_todo(doc)["id"], "t1")
        doc = core.set_done(doc, "t1", True)
        self.assertEqual(core.next_todo(doc)["id"], "t2")

    def test_next_todo_none_when_all_done(self):
        doc = core.normalize_doc({"todos": [make_todo("t1", "x", done=True)]})
        self.assertIsNone(core.next_todo(doc))

    def test_format_next_todo_line(self):
        todo = make_todo("t1", "交周报", remind_at="15:30", remind_date="2026-10-17")
        line = core.format_next_todo_line(todo, 3)
        self.assertIn("下一项", line)
        self.assertIn("(3)", line)
        self.assertIn("交周报", line)
        self.assertIn("15:30", line)


class CommentSelectionTests(unittest.TestCase):
    def test_boring_and_ad_comments_are_rejected(self):
        self.assertLess(core.score_interesting_comment("好听", 1000), 0)
        self.assertLess(core.score_interesting_comment("打卡打卡打卡", 500), 0)
        self.assertLess(core.score_interesting_comment("加微信 xxx 领福利", 900), 0)
        self.assertLess(core.score_interesting_comment("😂😂😂😂😂😂😂😂", 900), 0)

    def test_story_comment_scores_positive(self):
        content = "高考结束那天下午，我在操场耳机里循环这首歌，突然就哭了。"
        self.assertGreater(core.score_interesting_comment(content, 800), 0)

    def test_select_prefers_interesting_and_filters(self):
        items = [
            {"content": "好听", "likedCount": 99999, "user": {"nickname": "a"}},
            {
                "content": "那年夏天我们一起听这首歌，现在她已经不在了。",
                "likedCount": 300,
                "user": {"nickname": "b"},
            },
            {"content": "加V领券：abc123", "likedCount": 5000, "user": {"nickname": "c"}},
            {
                "content": "毕业后第一次重逢，是在车站的耳机里听到这首。",
                "likedCount": 120,
                "user": {"nickname": "d"},
            },
        ]
        selected = core.select_interesting_comment_items(items, limit=5)
        contents = [item["content"] for item in selected]
        self.assertEqual(len(selected), 2)
        self.assertIn("那年夏天我们一起听这首歌，现在她已经不在了。", contents)
        self.assertNotIn("好听", contents)
        self.assertNotIn("加V领券：abc123", contents)

    def test_likes_short_format(self):
        self.assertEqual(core.NetEaseClient._format_likes(12000), "1.2万")
        self.assertEqual(core.NetEaseClient._format_likes(100000), "10万")
        self.assertEqual(core.NetEaseClient._format_likes(999), "999")


class DailyTodoTests(unittest.TestCase):
    """每天 22:00 填“明天”、凌晨 2:00 自动翻篇的日期规则。"""

    def _moment(self, text: str) -> _dt.datetime:
        value = core.parse_iso(text)
        self.assertIsNotNone(value)
        return value  # type: ignore[return-value]

    def test_current_day_rolls_at_2am(self):
        self.assertEqual(core.current_day(self._moment("2026-10-18T01:30:00+08:00")), _dt.date(2026, 10, 17))
        self.assertEqual(core.current_day(self._moment("2026-10-18T02:00:00+08:00")), _dt.date(2026, 10, 18))
        self.assertEqual(core.current_day(self._moment("2026-10-18T21:59:00+08:00")), _dt.date(2026, 10, 18))

    def test_target_day_evening_belongs_to_tomorrow(self):
        self.assertEqual(core.target_day(self._moment("2026-10-17T21:59:00+08:00")), _dt.date(2026, 10, 17))
        self.assertEqual(core.target_day(self._moment("2026-10-17T22:00:00+08:00")), _dt.date(2026, 10, 18))
        self.assertEqual(core.target_day(self._moment("2026-10-18T01:30:00+08:00")), _dt.date(2026, 10, 18))

    def test_todo_day_derived_from_created_at(self):
        morning = make_todo("a", "上午填的", created_at="2026-10-17T10:00:00+08:00")
        evening = make_todo("b", "晚上填的", created_at="2026-10-17T22:30:00+08:00")
        self.assertEqual(core.todo_day(morning), _dt.date(2026, 10, 17))
        self.assertEqual(core.todo_day(evening), _dt.date(2026, 10, 18))

    def test_next_todo_for_day_filters_tomorrow_tasks(self):
        doc = make_doc(
            make_todo("today", "今天的", created_at="2026-10-18T08:00:00+08:00"),
            make_todo("tomorrow", "明天填的", created_at="2026-10-18T22:30:00+08:00"),
        )
        day = _dt.date(2026, 10, 18)
        today_items = core.todos_for_day(doc, day)
        self.assertEqual([item["id"] for item in today_items], ["today"])
        self.assertEqual(core.next_todo_for_day(doc, day)["id"], "today")
        # 第二天的任务要到 2:00 之后才算“今天”
        self.assertEqual(
            [item["id"] for item in core.todos_for_day(doc, _dt.date(2026, 10, 19))], ["tomorrow"]
        )

    def test_next_todo_for_day_falls_back_to_overdue(self):
        doc = make_doc(make_todo("old", "昨天没做完", created_at="2026-10-16T09:00:00+08:00"))
        picked = core.next_todo_for_day(doc, _dt.date(2026, 10, 18))
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], "old")
        self.assertEqual(core.todos_for_day(doc, _dt.date(2026, 10, 18)), [])

    def test_fill_reminder_needed(self):
        empty = make_doc()
        evening = self._moment("2026-10-17T22:05:00+08:00")
        early = self._moment("2026-10-18T01:30:00+08:00")
        day = self._moment("2026-10-18T10:00:00+08:00")
        self.assertTrue(core.fill_reminder_needed(empty, evening))
        self.assertTrue(core.fill_reminder_needed(empty, early))
        self.assertFalse(core.fill_reminder_needed(empty, day))
        filled = make_doc(make_todo("t", "明天的任务", created_at="2026-10-17T22:30:00+08:00"))
        self.assertFalse(core.fill_reminder_needed(filled, evening))

    def test_day_text(self):
        today = _dt.date(2026, 10, 17)
        self.assertEqual(core.day_text(today, today), "今天")
        self.assertEqual(core.day_text(today + _dt.timedelta(days=1), today), "明天")
        self.assertEqual(core.day_text(today + _dt.timedelta(days=3), today), "10-20")

    def test_format_next_todo_line_with_overdue_lead(self):
        todo = make_todo("t1", "交周报")
        line = core.format_next_todo_line(todo, 2, lead="逾期")
        self.assertIn("逾期", line)
        self.assertNotIn("下一项", line)


class FullscreenDetectionTests(unittest.TestCase):
    """全屏应用检测：窗口矩形是否覆盖显示器。"""

    def test_exact_fullscreen(self):
        self.assertTrue(core.rect_covers_monitor((0, 0, 1920, 1080), (0, 0, 1920, 1080)))

    def test_tolerates_small_border(self):
        self.assertTrue(core.rect_covers_monitor((-1, 0, 1921, 1079), (0, 0, 1920, 1080)))
        self.assertTrue(core.rect_covers_monitor((2, 2, 1918, 1078), (0, 0, 1920, 1080)))

    def test_windowed_not_fullscreen(self):
        self.assertFalse(core.rect_covers_monitor((100, 100, 1300, 800), (0, 0, 1920, 1080)))
        self.assertFalse(core.rect_covers_monitor((0, 0, 1920, 1000), (0, 0, 1920, 1080)))

    def test_invalid_rect(self):
        self.assertFalse(core.rect_covers_monitor(None, (0, 0, 1920, 1080)))
        self.assertFalse(core.rect_covers_monitor((0, 0, 0, 0), (0, 0, 1920, 1080)))


# ---------------------------------------------------------------------------
# 待办 schema v3：目标日期 / 父任务 / 预计耗时
# ---------------------------------------------------------------------------

TODAY = _dt.date(2026, 10, 20)
WEEK_START = _dt.date(2026, 10, 5)


class SchemaV3Tests(unittest.TestCase):
    def test_schema_version_is_3(self):
        self.assertEqual(core.SCHEMA_VERSION, 3)
        self.assertEqual(core.empty_doc()["schema"], 3)
        # 旧版 schema 2 文档归一化后升级为 3
        self.assertEqual(core.normalize_doc({"schema": 2})["schema"], 3)

    def test_new_todo_fields_default(self):
        todo = core.normalize_todo({"id": "t1", "text": "任务"})
        self.assertEqual(todo["due_date"], "")
        self.assertEqual(todo["parent_id"], "")
        self.assertEqual(todo["estimate_minutes"], 0)

    def test_legacy_todo_migrates_to_v3(self):
        todo = core.normalize_todo(make_todo("t1", "老数据"))
        self.assertIn("due_date", todo)
        self.assertIn("parent_id", todo)
        self.assertIn("estimate_minutes", todo)
        self.assertEqual(todo["due_date"], "")

    def test_camel_case_aliases(self):
        todo = core.normalize_todo(
            {
                "id": "t1",
                "text": "别名",
                "dueDate": "2026-11-01",
                "parentId": "p-1",
                "estimateMinutes": "30",
            }
        )
        self.assertEqual(todo["due_date"], "2026-11-01")
        self.assertEqual(todo["parent_id"], "p-1")
        self.assertEqual(todo["estimate_minutes"], 30)

    def test_invalid_new_fields_normalized(self):
        todo = core.normalize_todo(
            {
                "id": "t1",
                "text": "脏数据",
                "due_date": "2026-13-40",
                "estimate_minutes": "-5",
            }
        )
        self.assertEqual(todo["due_date"], "")
        self.assertEqual(todo["estimate_minutes"], 0)
        self.assertEqual(core.normalize_todo({"text": "x", "estimate_minutes": "abc"})["estimate_minutes"], 0)
        self.assertEqual(core.normalize_todo({"text": "x", "estimate_minutes": "12.9"})["estimate_minutes"], 12)

    def test_self_parent_is_dropped(self):
        todo = core.normalize_todo({"id": "p1", "text": "父任务", "parent_id": "p1"})
        self.assertEqual(todo["parent_id"], "")

    def test_doc_keeps_new_fields(self):
        doc = make_doc(
            make_todo("t1", "买年货", due_date="2026-10-25", estimate_minutes=120),
            make_todo("t2", "列清单", parent_id="t1"),
        )
        first = next(item for item in doc["todos"] if item["id"] == "t1")
        self.assertEqual(first["due_date"], "2026-10-25")
        self.assertEqual(first["estimate_minutes"], 120)
        second = next(item for item in doc["todos"] if item["id"] == "t2")
        self.assertEqual(second["parent_id"], "t1")

    def test_canonical_sorted_and_includes_new_keys(self):
        doc = make_doc(
            make_todo("t1", "买年货", due_date="2026-11-01", parent_id="p1", estimate_minutes=30)
        )
        text = core.canonical(doc)
        self.assertIn('"due_date":"2026-11-01"', text)
        self.assertIn('"parent_id":"p1"', text)
        self.assertIn('"estimate_minutes":30', text)
        payload = json.loads(text)
        for item in payload["todos"]:
            # canonical 与前端一致：键按字母顺序排列
            self.assertEqual(list(item.keys()), sorted(item.keys()))

    def test_canonical_reacts_to_new_fields(self):
        base = make_doc(make_todo("t1", "任务", due_date="2026-11-01"))
        changed = make_doc(make_todo("t1", "任务", due_date="2026-11-02"))
        self.assertNotEqual(core.canonical(base), core.canonical(changed))

    def test_canonical_matches_frontend_json(self):
        # 模拟前端 canonical()：canonicalItem 的键按字母顺序、JSON.stringify 无空格
        doc = make_doc(
            make_todo("t1", "买年货", due_date="2026-11-01", estimate_minutes=0)
        )
        todo = doc["todos"][0]
        frontend_item = {
            "created_at": todo["created_at"],
            "done": todo["done"],
            "done_at": todo["done_at"],
            "due_date": todo["due_date"],
            "estimate_minutes": todo["estimate_minutes"],
            "id": todo["id"],
            "notified_at": todo["notified_at"],
            "parent_id": todo["parent_id"],
            "priority": todo["priority"],
            "remind_at": todo["remind_at"],
            "remind_date": todo["remind_date"],
            "text": todo["text"],
            "updated_at": todo["updated_at"],
        }
        expected = json.dumps(
            {"todos": [frontend_item], "tombstones": []},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertEqual(core.canonical(doc), expected)

    def test_merge_keeps_new_fields(self):
        local = make_doc(make_todo("t1", "旧", updated=T1))
        remote = make_doc(
            make_todo("t1", "新", updated=T2, due_date="2026-12-01", estimate_minutes=45)
        )
        merged = core.merge_docs(local, remote)
        self.assertEqual(merged["todos"][0]["due_date"], "2026-12-01")
        self.assertEqual(merged["todos"][0]["estimate_minutes"], 45)


class TodoV3OperationTests(unittest.TestCase):
    def test_add_todo_with_v3_fields(self):
        doc, todo = core.add_todo(
            core.empty_doc(),
            "买年货",
            due_date="2026-12-01",
            parent_id="p-1",
            estimate_minutes=90,
        )
        self.assertEqual(todo["due_date"], "2026-12-01")
        self.assertEqual(todo["parent_id"], "p-1")
        self.assertEqual(todo["estimate_minutes"], 90)
        self.assertEqual(doc["todos"][0]["due_date"], "2026-12-01")

    def test_add_todo_invalid_v3_fields(self):
        _, todo = core.add_todo(
            core.empty_doc(), "任务", due_date="bad-date", estimate_minutes=-8
        )
        self.assertEqual(todo["due_date"], "")
        self.assertEqual(todo["estimate_minutes"], 0)

    def test_update_todo_v3_whitelist(self):
        doc, todo = core.add_todo(core.empty_doc(), "任务")
        updated = core.update_todo(
            doc,
            todo["id"],
            due_date="2026-12-31",
            parent_id="p-9",
            estimate_minutes="45",
        )
        item = updated["todos"][0]
        self.assertEqual(item["due_date"], "2026-12-31")
        self.assertEqual(item["parent_id"], "p-9")
        self.assertEqual(item["estimate_minutes"], 45)

    def test_update_todo_normalizes_and_detaches(self):
        doc, todo = core.add_todo(core.empty_doc(), "任务", parent_id="p-1")
        updated = core.update_todo(
            doc, todo["id"], due_date="2026-99-99", parent_id="", estimate_minutes=-1
        )
        item = updated["todos"][0]
        self.assertEqual(item["due_date"], "")
        self.assertEqual(item["parent_id"], "")
        self.assertEqual(item["estimate_minutes"], 0)

    def test_update_todo_self_parent_dropped(self):
        doc, todo = core.add_todo(core.empty_doc(), "任务")
        updated = core.update_todo(doc, todo["id"], parent_id=todo["id"])
        self.assertEqual(updated["todos"][0]["parent_id"], "")

    def test_todo_day_prefers_due_date(self):
        todo = make_todo("a", "有目标日期", created_at="2026-10-17T22:30:00+08:00", due_date="2026-10-20")
        self.assertEqual(core.todo_day(todo), _dt.date(2026, 10, 20))
        # 非法 due_date 回退到 created_at 规则
        invalid = make_todo(
            "b", "坏目标日期", created_at="2026-10-17T22:30:00+08:00", due_date="2026-02-30"
        )
        self.assertEqual(core.todo_day(invalid), _dt.date(2026, 10, 18))

    def test_daily_queries_use_due_date(self):
        doc = make_doc(
            make_todo(
                "due-today", "今天目标", created_at="2026-10-10T08:00:00+08:00",
                due_date="2026-10-20",
            ),
            make_todo(
                "due-later", "以后目标", created_at="2026-10-10T08:00:00+08:00",
                due_date="2026-10-25",
            ),
            make_todo(
                "overdue-due", "目标逾期", created_at="2026-10-10T08:00:00+08:00",
                due_date="2026-10-15",
            ),
        )
        today = _dt.date(2026, 10, 20)
        self.assertEqual([item["id"] for item in core.todos_for_day(doc, today)], ["due-today"])
        self.assertEqual([item["id"] for item in core.overdue_todos(doc, today)], ["overdue-due"])
        self.assertEqual(core.next_todo_for_day(doc, today)["id"], "due-today")


class SubtaskTests(unittest.TestCase):
    def _doc(self):
        return make_doc(
            make_todo("p1", "买年货", created_at=T1),
            make_todo("c1", "列清单", parent_id="p1", created_at=T2, done=True),
            make_todo("c2", "去超市", parent_id="p1", created_at=T3),
            make_todo("c3", "记账", parent_id="p1", created_at=T4),
            make_todo("other", "独立任务"),
        )

    def test_subtasks_ordered_by_created(self):
        doc = self._doc()
        self.assertEqual([item["id"] for item in core.subtasks(doc, "p1")], ["c1", "c2", "c3"])
        self.assertEqual(core.subtasks(doc, "nope"), [])
        self.assertEqual(core.subtasks(doc, ""), [])

    def test_subtasks_keep_creation_order_in_same_second(self):
        doc = core.empty_doc()
        doc, parent = core.add_todo(doc, "父任务")
        steps = ["步骤1", "步骤2", "步骤3", "步骤4"]
        for step in steps:
            doc, _ = core.add_todo(doc, step, parent_id=parent["id"])
        self.assertEqual(
            [item["text"] for item in core.subtasks(doc, parent["id"])], steps
        )

    def test_parent_progress(self):
        doc = self._doc()
        self.assertEqual(core.parent_progress(doc, "p1"), (1, 3))
        self.assertEqual(core.parent_progress(doc, "other"), (0, 0))

    def test_upcoming_todos_range_and_order(self):
        doc = make_doc(
            make_todo("a", "今天到期", due_date="2026-10-20", created_at=T1),
            make_todo("b", "后天紧急", due_date="2026-10-22", created_at=T1, priority="high"),
            make_todo("c", "太远", due_date="2026-11-10", created_at=T1),
            make_todo("d", "无日期", created_at="2026-10-20T09:00:00+08:00"),
            make_todo("e", "已完成", due_date="2026-10-21", done=True),
        )
        picked = core.upcoming_todos(doc, TODAY, days=14)
        self.assertEqual([item["id"] for item in picked], ["a", "d", "b"])

    def test_upcoming_accepts_string_start_day(self):
        doc = make_doc(make_todo("a", "今天到期", due_date="2026-10-20"))
        picked = core.upcoming_todos(doc, "2026-10-20", days=1)
        self.assertEqual([item["id"] for item in picked], ["a"])

    def test_upcoming_excludes_out_of_range(self):
        doc = make_doc(make_todo("a", "明天", due_date="2026-10-21"))
        self.assertEqual(core.upcoming_todos(doc, TODAY, days=1), [])


class TodoLineV3Tests(unittest.TestCase):
    def test_parent_progress_from_doc(self):
        doc = make_doc(
            make_todo("p1", "买年货"),
            make_todo("c1", "列清单", parent_id="p1", done=True),
            make_todo("c2", "去超市", parent_id="p1"),
        )
        parent = next(item for item in doc["todos"] if item["id"] == "p1")
        line = core.format_todo_line(parent, doc=doc)
        self.assertIn("[1/2]", line)

    def test_progress_tuple_and_dict(self):
        todo = make_todo("p1", "父任务")
        self.assertIn("[2/3]", core.format_todo_line(todo, subtask_progress=(2, 3)))
        self.assertIn("[2/3]", core.format_todo_line(todo, subtask_progress={"done": 2, "total": 3}))
        # 没有子任务时不显示进度
        self.assertNotIn("[", core.format_todo_line(todo, subtask_progress=(0, 0)))

    def test_due_date_labels(self):
        cases = [
            ("2026-10-20", "📅 今天"),
            ("2026-10-21", "📅 明天"),
            ("2026-10-25", "📅 10-25"),
            ("2026-10-19", "逾期"),
        ]
        for due, expected in cases:
            line = core.format_todo_line(make_todo("t1", "任务", due_date=due), today=TODAY)
            self.assertIn(expected, line)
        self.assertNotIn("📅", core.format_todo_line(make_todo("t1", "无日期"), today=TODAY))

    def test_next_todo_line_forwards_progress(self):
        doc = make_doc(
            make_todo("p1", "父任务"),
            make_todo("c1", "子任务1", parent_id="p1", done=True),
            make_todo("c2", "子任务2", parent_id="p1"),
        )
        parent = next(item for item in doc["todos"] if item["id"] == "p1")
        line = core.format_next_todo_line(parent, 3, doc=doc, today=TODAY)
        self.assertIn("下一项", line)
        self.assertIn("(3)", line)
        self.assertIn("[1/2]", line)


# ---------------------------------------------------------------------------
# 记账（schema 1，独立 Gist）
# ---------------------------------------------------------------------------

class LedgerNormalizeTests(unittest.TestCase):
    def test_empty_ledger(self):
        ledger = core.empty_ledger()
        self.assertEqual(ledger["schema"], 1)
        self.assertEqual(ledger["version"], 0)
        self.assertEqual(ledger["entries"], [])
        self.assertEqual(ledger["tombstones"], [])
        self.assertEqual(core.LEDGER_SCHEMA_VERSION, 1)
        self.assertEqual(core.LEDGER_FILENAME, "taskbar_ledger.json")

    def test_normalize_entry_defaults_and_invalid(self):
        entry = core.normalize_ledger_entry({"amount": "12.5"})
        self.assertTrue(entry["id"].startswith("lg-"))
        self.assertEqual(entry["type"], "expense")
        self.assertEqual(entry["amount"], 12.5)
        # 与前端 normalizeLedgerEntry 一致：缺失分类保留空串（新增时再由 add 兜底）
        self.assertEqual(entry["category"], "")
        self.assertEqual(entry["note"], "")
        # 没有 date / created_at 时用“今天”兜底
        self.assertEqual(entry["date"], core.current_day().isoformat())
        self.assertIsNone(core.normalize_ledger_entry("bad"))

    def test_normalize_entry_clamps_amount(self):
        self.assertEqual(core.normalize_ledger_entry({"amount": -8})["amount"], 0.0)
        self.assertEqual(core.normalize_ledger_entry({"amount": "abc"})["amount"], 0.0)
        self.assertEqual(core.normalize_ledger_entry({"amount": 12.345})["amount"], 12.35)
        # 与前端 Math.round 一致：0.125 -> 0.13（Python round 会得到 0.12）
        self.assertEqual(core.normalize_ledger_entry({"amount": 0.125})["amount"], 0.13)

    def test_ledger_time_normalization(self):
        # 9:05 -> 09:05
        self.assertEqual(
            core.normalize_ledger_entry({"amount": 1, "time": "9:05"})["time"], "09:05"
        )
        # 缺失时间时从 created_at 的 HH:MM 回退
        entry = core.normalize_ledger_entry(
            {"amount": 1, "created_at": "2026-10-17T21:34:00+08:00"}
        )
        self.assertEqual(entry["time"], "21:34")
        # 非法时间同样回退到 created_at
        invalid = core.normalize_ledger_entry(
            {"amount": 1, "time": "25:99", "created_at": "2026-10-17T07:08:00+08:00"}
        )
        self.assertEqual(invalid["time"], "07:08")

    def test_canonical_ledger_matches_frontend_json(self):
        ledger = make_ledger(make_entry("e1", note="午饭"))
        entry = ledger["entries"][0]
        frontend_item = {
            "amount": entry["amount"],
            "category": entry["category"],
            "created_at": entry["created_at"],
            "date": entry["date"],
            "id": entry["id"],
            "note": entry["note"],
            "time": entry["time"],
            "type": entry["type"],
            "updated_at": entry["updated_at"],
        }
        expected = json.dumps(
            {"entries": [frontend_item], "tombstones": []},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertEqual(core.canonical_ledger(ledger), expected)

    def test_doc_dedup_and_sort(self):
        ledger = make_ledger(
            make_entry("b"), make_entry("a"), make_entry("a", amount=99)
        )
        self.assertEqual([item["id"] for item in ledger["entries"]], ["a", "b"])
        self.assertEqual(ledger["entries"][0]["amount"], 12.5)

    def test_canonical_stable_and_key_sorted(self):
        a = make_ledger(make_entry("e1"), version=1)
        b = make_ledger(make_entry("e1"), version=9)
        b["updated_at"] = T4
        self.assertEqual(core.canonical_ledger(a), core.canonical_ledger(b))
        payload = json.loads(core.canonical_ledger(a))
        for entry in payload["entries"]:
            self.assertEqual(list(entry.keys()), sorted(entry.keys()))
        changed = make_ledger(make_entry("e1", note="备注变了"), version=9)
        self.assertNotEqual(core.canonical_ledger(a), core.canonical_ledger(changed))

    def test_merge_lww_and_union(self):
        local = make_ledger(make_entry("e1", amount=10, updated=T1))
        remote = make_ledger(make_entry("e1", amount=20, updated=T2), make_entry("e2"))
        merged = core.merge_ledgers(local, remote)
        amounts = {item["id"]: item["amount"] for item in merged["entries"]}
        self.assertEqual(amounts["e1"], 20)
        self.assertEqual(amounts["e2"], 12.5)

    def test_merge_tombstone_deletes_and_revives(self):
        local = make_ledger(make_entry("e1", updated=T1))
        remote = make_ledger(tombstones=[{"id": "e1", "deleted_at": T2}])
        merged = core.merge_ledgers(local, remote)
        self.assertEqual(merged["entries"], [])
        self.assertEqual(len(merged["tombstones"]), 1)
        # 删除之后又更新 → 复活
        revived = core.merge_ledgers(
            make_ledger(make_entry("e1", updated=T3)),
            make_ledger(tombstones=[{"id": "e1", "deleted_at": T2}]),
        )
        self.assertEqual([item["id"] for item in revived["entries"]], ["e1"])
        self.assertEqual(revived["tombstones"], [])

    def test_merge_idempotent_content_and_version(self):
        ledger = make_ledger(make_entry("e1"))
        merged = core.merge_ledgers(ledger, ledger)
        self.assertEqual(core.canonical_ledger(merged), core.canonical_ledger(ledger))
        self.assertEqual(merged["version"], 2)


class LedgerCrudTests(unittest.TestCase):
    def test_add_and_update_ledger_time(self):
        ledger, entry = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-20", "expense", 12, "餐饮", time="12:34"
        )
        self.assertEqual(entry["time"], "12:34")
        ledger = core.update_ledger_entry(ledger, entry["id"], time="23:05")
        self.assertEqual(ledger["entries"][0]["time"], "23:05")

    def test_add_and_week_summary(self):
        ledger, income = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "income", 100, "工资", "周薪"
        )
        ledger, expense = core.add_ledger_entry(
            ledger, "2026-10-07", "expense", 20.5, "餐饮", "午饭", entry_id="lg-fixed"
        )
        ledger, outside = core.add_ledger_entry(
            ledger, "2026-10-20", "expense", 99, "购物"
        )
        self.assertEqual(income["type"], "income")
        self.assertEqual(expense["id"], "lg-fixed")
        self.assertEqual(len(ledger["entries"]), 3)

        summary = core.ledger_week_summary(ledger, WEEK_START)
        self.assertEqual(summary["income"], 100.0)
        self.assertEqual(summary["expense"], 20.5)
        self.assertEqual(summary["net"], 79.5)
        self.assertEqual(
            summary["by_category"],
            {
                "工资": {"income": 100.0, "expense": 0.0},
                "餐饮": {"income": 0.0, "expense": 20.5},
            },
        )
        self.assertEqual([item["date"] for item in summary["entries"]], ["2026-10-06", "2026-10-07"])
        self.assertNotIn(outside["id"], [item["id"] for item in summary["entries"]])

    def test_add_normalizes_input(self):
        ledger, entry = core.add_ledger_entry(
            core.empty_ledger(), "bad-date", "unknown", -8, "", " 备注 "
        )
        self.assertEqual(entry["date"], core.current_day().isoformat())
        self.assertEqual(entry["type"], "expense")
        self.assertEqual(entry["amount"], 0.0)
        self.assertEqual(entry["category"], "其他")
        self.assertEqual(entry["note"], "备注")

    def test_add_same_id_replaces(self):
        ledger, _ = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "expense", 10, "餐饮", entry_id="lg-1"
        )
        ledger, _ = core.add_ledger_entry(
            ledger, "2026-10-06", "expense", 20, "餐饮", entry_id="lg-1"
        )
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["amount"], 20.0)

    def test_update_entry(self):
        ledger, entry = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "expense", 10, "餐饮"
        )
        updated = core.update_ledger_entry(
            ledger, entry["id"], amount="25.5", category="交通", note="打车", date="2026-10-08"
        )
        item = updated["entries"][0]
        self.assertEqual(item["amount"], 25.5)
        self.assertEqual(item["category"], "交通")
        self.assertEqual(item["note"], "打车")
        self.assertEqual(item["date"], "2026-10-08")
        self.assertEqual(updated["version"], ledger["version"] + 1)
        # 非法日期保留原值，未知字段忽略
        kept = core.update_ledger_entry(updated, entry["id"], date="bad", unknown="x")
        self.assertEqual(kept["entries"][0]["date"], "2026-10-08")
        self.assertNotIn("unknown", kept["entries"][0])

    def test_delete_entry(self):
        ledger, entry = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "expense", 10, "餐饮"
        )
        deleted = core.delete_ledger_entry(ledger, entry["id"])
        self.assertEqual(deleted["entries"], [])
        self.assertEqual(deleted["tombstones"][0]["id"], entry["id"])

    def test_week_summary_empty(self):
        summary = core.ledger_week_summary(core.empty_ledger(), WEEK_START)
        self.assertEqual(
            summary,
            {"income": 0.0, "expense": 0.0, "net": 0.0, "by_category": {}, "entries": []},
        )

    def test_format_money(self):
        self.assertEqual(core.format_money(12.5), "¥12.50")
        self.assertEqual(core.format_money(0), "¥0.00")
        # 与前端 formatMoney 一致：符号在最前
        self.assertEqual(core.format_money(-3.5), "¥-3.50")
        self.assertEqual(core.format_money(-0.0), "¥0.00")
        self.assertEqual(core.format_money("abc"), "¥0.00")
        self.assertEqual(core.format_money(1234.5), "¥1234.50")


class LedgerStoreTests(unittest.TestCase):
    @staticmethod
    def ledger_payload(doc, etag=None):
        return FakeResponse(
            {
                "files": {
                    core.LEDGER_FILENAME: {
                        "content": json.dumps(core.normalize_ledger(doc), ensure_ascii=False)
                    }
                }
            },
            headers={"ETag": etag or '"lg-etag-1"'},
        )

    def test_configured(self):
        self.assertFalse(core.LedgerStore("", "gid").configured)
        self.assertFalse(core.LedgerStore("token", "").configured)
        self.assertTrue(core.LedgerStore("token", "gid").configured)

    def test_fetch_ok(self):
        ledger, _ = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "expense", 10, "餐饮"
        )
        session = FakeSession(get_responses=[self.ledger_payload(ledger)])
        store = core.LedgerStore("token", "gid", session=session)
        fetch = store.fetch()
        self.assertEqual(fetch.status, "ok")
        self.assertEqual(fetch.doc["entries"][0]["amount"], 10.0)
        self.assertEqual(fetch.etag, '"lg-etag-1"')

    def test_custom_filename(self):
        session = FakeSession(
            get_responses=[
                FakeResponse(
                    {"files": {"my_ledger.json": {"content": json.dumps(core.empty_ledger())}}}
                )
            ]
        )
        store = core.LedgerStore("token", "gid", filename="my_ledger.json", session=session)
        self.assertEqual(store.fetch().status, "ok")
        self.assertEqual(store.filename, "my_ledger.json")

    def test_fetch_404_raises_api_error(self):
        session = FakeSession(get_responses=[FakeResponse(status=404)])
        store = core.LedgerStore("token", "gid", session=session)
        with self.assertRaises(core.GitHubApiError) as context:
            store.fetch()
        self.assertEqual(context.exception.status, 404)

    def test_sync_pushes_ledger_changes(self):
        remote = core.empty_ledger()
        local, entry = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "income", 100, "工资"
        )
        session = FakeSession(
            get_responses=[self.ledger_payload(remote)],
            patch_responses=[FakeResponse({}, headers={"ETag": '"lg-etag-2"'})],
        )
        store = core.LedgerStore("token", "gid", session=session)
        report = store.sync(local, etag=None)
        self.assertTrue(report.pushed)
        self.assertEqual(report.etag, '"lg-etag-2"')
        patch_calls = [call for call in session.calls if call[0] == "PATCH"]
        self.assertEqual(len(patch_calls), 1)
        body = patch_calls[0][2]["json"]
        pushed = json.loads(body["files"][core.LEDGER_FILENAME]["content"])
        self.assertEqual(pushed["entries"][0]["amount"], 100.0)
        self.assertEqual([item["id"] for item in report.doc["entries"]], [entry["id"]])

    def test_sync_without_changes_does_not_push(self):
        ledger = make_ledger(make_entry("e1"))
        session = FakeSession(get_responses=[self.ledger_payload(ledger)])
        store = core.LedgerStore("token", "gid", session=session)
        report = store.sync(ledger, etag=None)
        self.assertFalse(report.pushed)
        self.assertEqual([call for call in session.calls if call[0] == "PATCH"], [])

    def test_sync_network_error_wrapped(self):
        import requests

        session = FakeSession(get_responses=[requests.ConnectionError("boom")])
        store = core.LedgerStore("token", "gid", session=session)
        with self.assertRaises(core.GitHubApiError) as context:
            store.sync(core.empty_ledger())
        self.assertIn("网络错误", str(context.exception))

    def test_create_gist_uses_ledger_filename(self):
        session = FakeSession(post_responses=[FakeResponse({"id": "lg-gist-id"})])
        store = core.LedgerStore("token", "", session=session)
        gist_id = store.create_gist(core.empty_ledger())
        self.assertEqual(gist_id, "lg-gist-id")
        self.assertEqual(store.gist_id, "lg-gist-id")
        body = session.calls[0][2]["json"]
        self.assertFalse(body["public"])
        self.assertIn(core.LEDGER_FILENAME, body["files"])


# ---------------------------------------------------------------------------
# 周报数据层
# ---------------------------------------------------------------------------

class WeeklyReportTests(unittest.TestCase):
    def _doc(self):
        parent = make_todo(
            "p1",
            "买年货",
            created_at="2026-10-06T09:00:00+08:00",
            due_date="2026-10-09",
            estimate_minutes=120,
        )
        child_done = make_todo(
            "c1",
            "列清单",
            parent_id="p1",
            done=True,
            done_at="2026-10-06T18:00:00+08:00",
            created_at="2026-10-06T09:10:00+08:00",
        )
        child_pending = make_todo(
            "c2", "去超市", parent_id="p1", created_at="2026-10-06T09:20:00+08:00"
        )
        completed = make_todo(
            "t2",
            "交周报",
            done=True,
            done_at="2026-10-07T20:00:00+08:00",
            created_at="2026-10-05T08:00:00+08:00",
        )
        overdue = make_todo(
            "t3", "还书", due_date="2020-01-01", created_at="2026-09-01T08:00:00+08:00"
        )
        future = make_todo(
            "t4", "体检", due_date="2026-12-01", created_at="2026-10-06T08:00:00+08:00"
        )
        outside_done = make_todo(
            "t5",
            "上上周完成",
            done=True,
            done_at="2026-09-20T10:00:00+08:00",
            created_at="2026-09-19T08:00:00+08:00",
        )
        return make_doc(parent, child_done, child_pending, completed, overdue, future, outside_done)

    def _ledger(self):
        ledger, _ = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "income", 100, "工资"
        )
        ledger, _ = core.add_ledger_entry(ledger, "2026-10-07", "expense", 20.5, "餐饮", "午饭")
        ledger, _ = core.add_ledger_entry(ledger, "2026-10-20", "expense", 99, "购物")
        return ledger

    def test_weekly_report_data_structure(self):
        data = core.weekly_report_data(
            self._doc(), self._ledger(), WEEK_START, today=_dt.date(2026, 10, 8)
        )
        self.assertEqual(data["week_start"], "2026-10-05")
        self.assertEqual(data["week_end"], "2026-10-11")
        self.assertEqual([item["id"] for item in data["completed"]], ["c1", "t2"])
        self.assertEqual(
            {item["id"] for item in data["pending"]}, {"p1", "c2", "t3", "t4"}
        )
        self.assertEqual([item["id"] for item in data["overdue"]], ["t3"])
        self.assertEqual(
            {item["id"] for item in data["created"]}, {"p1", "c1", "c2", "t2", "t4"}
        )
        self.assertEqual(
            data["subtask_progress"], [{"id": "p1", "text": "买年货", "done": 1, "total": 2}]
        )
        self.assertIsInstance(data["improvements"], list)
        self.assertTrue(all(isinstance(item, str) and item for item in data["improvements"]))
        self.assertTrue(any("逾期" in item for item in data["improvements"]))

        ledger = data["ledger"]
        self.assertEqual(ledger["income"], 100.0)
        self.assertEqual(ledger["expense"], 20.5)
        self.assertEqual(ledger["net"], 79.5)
        self.assertEqual(len(ledger["entries"]), 2)
        self.assertEqual(ledger["by_category"]["工资"]["income"], 100.0)
        # JSON 可序列化（后续邮件 / 记忆模块要用）
        json.dumps(data, ensure_ascii=False)

    def test_weekly_report_week_start_accepts_string(self):
        data = core.weekly_report_data(self._doc(), self._ledger(), "2026-10-05")
        self.assertEqual(data["week_start"], "2026-10-05")

    def test_weekly_report_markdown(self):
        data = core.weekly_report_data(
            self._doc(), self._ledger(), WEEK_START, today=_dt.date(2026, 10, 8)
        )
        markdown = core.weekly_report_markdown(data, "本周整体完成得不错，继续加油。")
        self.assertIn("# 周报 2026-10-05 ~ 2026-10-11", markdown)
        self.assertIn("✅ 本周完成（2）", markdown)
        self.assertIn("⏳ 未完成（4）", markdown)
        self.assertIn("⚠️ 逾期（1）", markdown)
        self.assertIn("🧩 子任务进度", markdown)
        self.assertIn("买年货  [1/2]", markdown)
        self.assertIn("🆕 本周新增（5）", markdown)
        self.assertIn("💡 改进建议", markdown)
        self.assertIn("💰 本周账单", markdown)
        self.assertIn("收入：¥100.00", markdown)
        self.assertIn("支出：¥20.50", markdown)
        self.assertIn("结余：¥79.50", markdown)
        self.assertIn("餐饮", markdown)
        self.assertIn("🤖 AI 评价", markdown)
        self.assertIn("本周整体完成得不错", markdown)

    def test_weekly_report_markdown_empty(self):
        data = core.weekly_report_data(core.empty_doc(), core.empty_ledger(), WEEK_START)
        markdown = core.weekly_report_markdown(data)
        self.assertIn("周报", markdown)
        self.assertIn("- 无", markdown)
        self.assertIn("收入：¥0.00", markdown)
        self.assertNotIn("AI 评价", markdown)

    def test_improvements_rule_for_overspending(self):
        ledger, _ = core.add_ledger_entry(
            core.empty_ledger(), "2026-10-06", "income", 10, "工资"
        )
        ledger, _ = core.add_ledger_entry(ledger, "2026-10-07", "expense", 50, "餐饮")
        data = core.weekly_report_data(
            self._doc(), ledger, WEEK_START, today=_dt.date(2026, 10, 8)
        )
        joined = " ".join(data["improvements"])
        self.assertIn("支出", joined)
        self.assertIn("高于收入", joined)

    def test_improvements_rule_for_empty_week(self):
        data = core.weekly_report_data(
            core.empty_doc(), core.empty_ledger(), WEEK_START, today=_dt.date(2026, 10, 8)
        )
        joined = " ".join(data["improvements"])
        self.assertIn("还没有完成", joined)
        self.assertIn("还没有记账", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
