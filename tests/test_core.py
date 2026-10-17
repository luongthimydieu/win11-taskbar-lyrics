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
            "schema": 2,
            "version": version,
            "updated_at": T1,
            "todos": list(todos),
            "tombstones": list(tombstones or []),
        }
    )


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
