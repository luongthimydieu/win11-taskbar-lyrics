#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成模块（ai_client / reporting / memory_sync）的离线单元测试。

特点：**不联网、不需要 PyQt6、不读写真实配置**。所有 HTTP 都用
``unittest.mock`` 模拟 ``requests.Session``，SMTP 用 ``unittest.mock`` 模拟
``smtplib.SMTP_SSL``，因此可以直接在纯 Python 环境跑：

    python -m unittest tests.test_integrations -v
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import datetime as _dt
import email
import email.policy
import json
import os
import smtplib
import sys
import unittest
from unittest import mock

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop"))

import ai_client  # noqa: E402
import memory_sync  # noqa: E402
import reporting  # noqa: E402

TODAY = _dt.date(2026, 10, 20)
WEEK_START = _dt.date(2026, 10, 19)
WEEK_END = _dt.date(2026, 10, 25)
NOW = _dt.datetime(2026, 10, 20, 23, 30, 0)


# ---------------------------------------------------------------------------
# 通用替身：MagicMock(spec=requests.Session)
# ---------------------------------------------------------------------------

def make_response(status=200, payload=None, headers=None):
    """模拟 requests.Response：只实现代码真正用到的属性。"""
    response = mock.MagicMock()
    response.status_code = status
    response.headers = dict(headers or {})
    response.json.return_value = payload if payload is not None else {}
    return response


def make_session(*, get=None, post=None, put=None):
    """按调用顺序返回预设响应的假 requests.Session（不产生任何网络 IO）。"""
    session = mock.MagicMock(spec=requests.Session)
    session.get.side_effect = list(get or [])
    session.post.side_effect = list(post or [])
    session.put.side_effect = list(put or [])
    return session


def make_client(session):
    return ai_client.OpenAICompatibleClient(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-chat",
        session=session,
    )


def chat_response(content):
    return make_response(payload={"choices": [{"message": {"content": content}}]})


def plan_json(**overrides):
    data = {
        "title": "准备答辩",
        "steps": [
            {"text": "整理提纲", "due_date": "2026-10-21", "estimate_minutes": 60},
            {"text": "制作幻灯片", "due_date": "2026-10-23", "estimate_minutes": 120},
        ],
        "note": "每天推进一点",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def weekly_report_data():
    return {
        "week_start": WEEK_START,
        "week_end": WEEK_END,
        "completed": [
            {
                "id": "t1",
                "text": "写周报",
                "done": True,
                "done_at": "2026-10-20T10:00:00+08:00",
                "due_date": "2026-10-20",
                "estimate_minutes": 30,
            }
        ],
        "pending": [{"id": "t2", "text": "交报告", "done": False, "due_date": "2026-10-23"}],
        "overdue": [{"id": "t3", "text": "整理桌面", "done": False, "due_date": "2026-10-18"}],
        "created": [{"id": "t4", "text": "新任务", "created_at": "2026-10-20T09:00:00+08:00"}],
        # 与 core.weekly_report_data 的真实结构一致：list[{"id","text","done","total"}]
        "subtask_progress": [{"id": "td-parent", "text": "写报告", "done": 1, "total": 3}],
        "improvements": ["每天固定时间复盘", "减少多任务切换"],
        "ledger": {
            "income": 100.0,
            "expense": 37.5,
            "net": 62.5,
            "by_category": {
                "工资": {"income": 100.0, "expense": 0.0},
                "餐饮": {"income": 0.0, "expense": 37.5},
            },
            "entries": [
                {
                    "date": "2026-10-20",
                    "type": "expense",
                    "amount": 12.5,
                    "category": "餐饮",
                    "note": "午饭",
                },
                {
                    "date": "2026-10-19",
                    "type": "income",
                    "amount": 100.0,
                    "category": "工资",
                    "note": "",
                },
            ],
        },
    }


# ===========================================================================
# ai_client：OpenAICompatibleClient
# ===========================================================================

class OpenAIClientTests(unittest.TestCase):
    def test_chat_success_payload_and_headers(self):
        session = make_session(post=[chat_response(" 正常 ")])
        client = make_client(session)
        reply = client.chat([{"role": "user", "content": "hi"}], temperature=0)
        self.assertEqual(reply, "正常")
        url = session.post.call_args[0][0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        kwargs = session.post.call_args[1]
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(kwargs["json"]["model"], "deepseek-chat")
        self.assertEqual(kwargs["json"]["temperature"], 0)
        self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(kwargs["timeout"], ai_client.DEFAULT_TIMEOUT)
        self.assertNotIn("response_format", kwargs["json"])

    def test_base_url_trailing_slash_is_normalized(self):
        session = make_session(post=[chat_response("ok")])
        client = ai_client.OpenAICompatibleClient(
            "https://api.deepseek.com/", "sk", "m", session=session
        )
        client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(session.post.call_args[0][0], "https://api.deepseek.com/chat/completions")

    def test_json_mode_adds_response_format(self):
        session = make_session(post=[chat_response('{"a": 1}')])
        client = make_client(session)
        client.chat([{"role": "user", "content": "hi"}], json_mode=True)
        body = session.post.call_args[1]["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_auto_creates_session_when_not_injected(self):
        with mock.patch("requests.Session") as session_cls:
            client = ai_client.OpenAICompatibleClient("https://x", "k", "m")
        session_cls.assert_called_once_with()
        self.assertIs(client.session, session_cls.return_value)

    def test_missing_config_raises_before_network(self):
        cases = [
            ("", "sk", "m", "Base URL"),
            ("https://x", "", "m", "API Key"),
            ("https://x", "sk", "", "模型"),
        ]
        for base_url, api_key, model, keyword in cases:
            with self.subTest(keyword=keyword):
                session = make_session()
                client = ai_client.OpenAICompatibleClient(
                    base_url, api_key, model, session=session
                )
                with self.assertRaises(ai_client.AIError) as context:
                    client.chat([{"role": "user", "content": "hi"}])
                self.assertIn(keyword, str(context.exception))
                session.post.assert_not_called()
        self.assertFalse(ai_client.OpenAICompatibleClient("", "", "").configured)

    def test_empty_messages_raises_value_error(self):
        client = make_client(make_session())
        with self.assertRaises(ValueError):
            client.chat([])

    def test_http_error_contains_status_and_detail(self):
        session = make_session(
            post=[make_response(status=401, payload={"error": {"message": "invalid key"}})]
        )
        with self.assertRaises(ai_client.AIError) as context:
            make_client(session).chat([{"role": "user", "content": "hi"}])
        message = str(context.exception)
        self.assertIn("401", message)
        self.assertIn("invalid key", message)

    def test_network_error_wrapped(self):
        session = make_session()
        session.post.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(ai_client.AIError) as context:
            make_client(session).chat([{"role": "user", "content": "hi"}])
        self.assertIn("boom", str(context.exception))

    def test_invalid_response_json_raises(self):
        response = make_response()
        response.json.side_effect = ValueError("not json")
        session = make_session(post=[response])
        with self.assertRaises(ai_client.AIError):
            make_client(session).chat([{"role": "user", "content": "hi"}])

    def test_response_missing_choices_raises(self):
        for payload in ({}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]}):
            with self.subTest(payload=payload):
                session = make_session(post=[make_response(payload=payload)])
                with self.assertRaises(ai_client.AIError):
                    make_client(session).chat([{"role": "user", "content": "hi"}])

    def test_empty_content_raises(self):
        session = make_session(post=[chat_response("   ")])
        with self.assertRaises(ai_client.AIError) as context:
            make_client(session).chat([{"role": "user", "content": "hi"}])
        self.assertIn("为空", str(context.exception))


# ===========================================================================
# ai_client：plan_complex_task
# ===========================================================================

class PlanComplexTaskTests(unittest.TestCase):
    def _client(self, content):
        session = make_session(post=[chat_response(content)])
        return make_client(session), session

    def _plan(self, content, **kwargs):
        client, session = self._client(content)
        plan = ai_client.plan_complex_task(client, "帮我准备答辩", TODAY, **kwargs)
        return plan, session

    def test_plan_success(self):
        plan, session = self._plan(plan_json())
        self.assertEqual(
            plan,
            {
                "title": "准备答辩",
                "steps": [
                    {"text": "整理提纲", "due_date": "2026-10-21", "estimate_minutes": 60},
                    {"text": "制作幻灯片", "due_date": "2026-10-23", "estimate_minutes": 120},
                ],
                "note": "每天推进一点",
            },
        )
        body = session.post.call_args[1]["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        system_prompt = body["messages"][0]["content"]
        self.assertIn("2026-10-20", system_prompt)
        self.assertIn("2026-11-03", system_prompt)  # today + 14 天
        self.assertIn("帮我准备答辩", body["messages"][1]["content"])

    def test_plan_parses_json_fence(self):
        content = "```json\n" + plan_json() + "\n```"
        plan, _ = self._plan(content)
        self.assertEqual(plan["steps"][0]["text"], "整理提纲")

    def test_plan_parses_fence_with_prose(self):
        content = "好的，这是计划：\n```json\n" + plan_json() + "\n```\n请确认。"
        plan, _ = self._plan(content)
        self.assertEqual(plan["title"], "准备答辩")

    def test_plan_parses_json_with_prose_without_fence(self):
        content = "这是计划：" + plan_json() + " 以上。"
        plan, _ = self._plan(content)
        self.assertEqual(len(plan["steps"]), 2)

    def test_plan_missing_title_falls_back_to_task_text(self):
        plan, _ = self._plan(plan_json(title=""))
        self.assertEqual(plan["title"], "帮我准备答辩")

    def test_plan_missing_note_is_empty(self):
        data = json.loads(plan_json())
        data.pop("note")
        plan, _ = self._plan(json.dumps(data, ensure_ascii=False))
        self.assertEqual(plan["note"], "")

    def test_plan_boundary_dates_allowed(self):
        data = json.loads(plan_json())
        data["steps"][0]["due_date"] = TODAY.isoformat()
        data["steps"][1]["due_date"] = "2026-11-03"  # today + 14
        plan, _ = self._plan(json.dumps(data, ensure_ascii=False))
        self.assertEqual([step["due_date"] for step in plan["steps"]], ["2026-10-20", "2026-11-03"])

    def test_plan_estimate_coercion(self):
        data = json.loads(plan_json())
        data["steps"][0]["estimate_minutes"] = "45"
        data["steps"][1].pop("estimate_minutes")
        plan, _ = self._plan(json.dumps(data, ensure_ascii=False))
        self.assertEqual(plan["steps"][0]["estimate_minutes"], 45)
        self.assertEqual(plan["steps"][1]["estimate_minutes"], 0)

    def test_plan_estimate_clamped_to_480(self):
        data = json.loads(plan_json())
        data["steps"][0]["estimate_minutes"] = 999
        plan, _ = self._plan(json.dumps(data, ensure_ascii=False))
        self.assertEqual(plan["steps"][0]["estimate_minutes"], 480)

    def test_plan_today_datetime_accepted(self):
        client, _ = self._client(plan_json())
        plan = ai_client.plan_complex_task(
            client, "任务", _dt.datetime(2026, 10, 20, 18, 0), horizon_days=5
        )
        self.assertEqual(plan["steps"][0]["due_date"], "2026-10-21")

    def test_plan_invalid_json_raises_plan_error(self):
        for content in ("这不是 JSON", "{broken json}", "前缀 ```json {broken} ``` 后缀"):
            with self.subTest(content=content):
                with self.assertRaises(ai_client.PlanError):
                    self._plan(content)

    def test_plan_empty_model_reply_raises_ai_error(self):
        # 空回复在 chat 层就会失败（AIError），不会走到 JSON 解析
        with self.assertRaises(ai_client.AIError):
            self._plan("   ")

    def test_plan_not_a_json_object_raises(self):
        for content in ("[1, 2, 3]", '"字符串"', "123"):
            with self.subTest(content=content):
                with self.assertRaises(ai_client.PlanError):
                    self._plan(content)

    def test_plan_steps_must_be_non_empty_list(self):
        for steps in ({}, [], None, "abc"):
            with self.subTest(steps=steps):
                with self.assertRaises(ai_client.PlanError):
                    self._plan(plan_json(steps=steps))

    def test_plan_step_must_be_object(self):
        with self.assertRaises(ai_client.PlanError):
            self._plan(plan_json(steps=["整理提纲"]))

    def test_plan_step_text_required(self):
        with self.assertRaises(ai_client.PlanError):
            self._plan(plan_json(steps=[{"text": "  ", "due_date": "2026-10-21"}]))

    def test_plan_due_date_required_and_valid(self):
        bad_steps = [
            [{"text": "x", "estimate_minutes": 10}],
            [{"text": "x", "due_date": ""}],
            [{"text": "x", "due_date": "2026/10/21"}],
            [{"text": "x", "due_date": "2026-13-01"}],
        ]
        for steps in bad_steps:
            with self.subTest(steps=steps):
                with self.assertRaises(ai_client.PlanError):
                    self._plan(plan_json(steps=steps))

    def test_plan_due_date_before_today_raises(self):
        steps = [{"text": "x", "due_date": "2026-10-19"}]
        with self.assertRaises(ai_client.PlanError) as context:
            self._plan(plan_json(steps=steps))
        self.assertIn("超出", str(context.exception))

    def test_plan_due_date_beyond_horizon_raises(self):
        steps = [{"text": "x", "due_date": "2026-11-04"}]
        with self.assertRaises(ai_client.PlanError) as context:
            self._plan(plan_json(steps=steps))
        self.assertIn("超出", str(context.exception))

    def test_plan_negative_estimate_raises(self):
        steps = [{"text": "x", "due_date": "2026-10-21", "estimate_minutes": -5}]
        with self.assertRaises(ai_client.PlanError):
            self._plan(plan_json(steps=steps))

    def test_plan_bad_estimate_raises(self):
        steps = [{"text": "x", "due_date": "2026-10-21", "estimate_minutes": "很久"}]
        with self.assertRaises(ai_client.PlanError):
            self._plan(plan_json(steps=steps))

    def test_plan_bad_horizon_raises(self):
        client, _ = self._client(plan_json())
        for horizon in (-1, "十几天"):
            with self.subTest(horizon=horizon):
                with self.assertRaises(ai_client.PlanError):
                    ai_client.plan_complex_task(client, "任务", TODAY, horizon_days=horizon)

    def test_plan_invalid_today_raises(self):
        client, _ = self._client(plan_json())
        with self.assertRaises(ai_client.PlanError):
            ai_client.plan_complex_task(client, "任务", "明天")

    def test_plan_empty_task_text_raises(self):
        client, _ = self._client(plan_json())
        with self.assertRaises(ai_client.PlanError):
            ai_client.plan_complex_task(client, "   ", TODAY)

    def test_plan_client_none_raises(self):
        with self.assertRaises(ai_client.AIError):
            ai_client.plan_complex_task(None, "任务", TODAY)

    def test_plan_network_error_propagates_ai_error(self):
        session = make_session()
        session.post.side_effect = requests.Timeout("timeout")
        client = make_client(session)
        with self.assertRaises(ai_client.AIError):
            ai_client.plan_complex_task(client, "任务", TODAY)


# ===========================================================================
# ai_client：evaluate
# ===========================================================================

class EvaluateTests(unittest.TestCase):
    def test_evaluate_day(self):
        session = make_session(post=[chat_response("今天完成了 2 项，节奏不错。建议明天先做最难的那件事。")])
        client = make_client(session)
        reply = ai_client.evaluate("day", client, {"done": 2, "pending": 1})
        self.assertIn("建议", reply)
        body = session.post.call_args[1]["json"]
        self.assertNotIn("response_format", body)
        user_message = body["messages"][-1]["content"]
        self.assertIn("今天", user_message)
        self.assertIn('"done": 2', user_message)

    def test_evaluate_week_accepts_string_context(self):
        session = make_session(post=[chat_response("本周整体完成度不错。建议把复盘固定到周日晚上。")])
        client = make_client(session)
        reply = ai_client.evaluate("week", client, "完成 5 项，逾期 1 项")
        self.assertIn("本周", session.post.call_args[1]["json"]["messages"][-1]["content"])
        self.assertIn("建议", reply)

    def test_evaluate_invalid_kind_raises(self):
        session = make_session()
        for kind in ("month", "", None):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    ai_client.evaluate(kind, make_client(session), {})
        session.post.assert_not_called()

    def test_evaluate_none_client_raises(self):
        with self.assertRaises(ai_client.AIError):
            ai_client.evaluate("day", None, {})

    def test_evaluate_empty_reply_raises(self):
        session = make_session(post=[chat_response("   ")])
        with self.assertRaises(ai_client.AIError):
            ai_client.evaluate("day", make_client(session), {})

    def test_evaluate_context_none_uses_placeholder(self):
        session = make_session(post=[chat_response("不错。建议保持。")])
        ai_client.evaluate("day", make_client(session), None)
        self.assertIn("（无数据）", session.post.call_args[1]["json"]["messages"][-1]["content"])


# ===========================================================================
# reporting：format_week_range
# ===========================================================================

class FormatWeekRangeTests(unittest.TestCase):
    def test_date_input(self):
        self.assertEqual(reporting.format_week_range(WEEK_START), "2026-10-19 ~ 2026-10-25")

    def test_string_and_datetime_input(self):
        self.assertEqual(reporting.format_week_range("2026-10-19"), "2026-10-19 ~ 2026-10-25")
        self.assertEqual(
            reporting.format_week_range(_dt.datetime(2026, 10, 19, 8, 0)), "2026-10-19 ~ 2026-10-25"
        )

    def test_invalid_input_raises(self):
        for value in ("", "不是日期", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    reporting.format_week_range(value)


# ===========================================================================
# reporting：send_email（mock smtplib.SMTP_SSL）
# ===========================================================================

def parse_sent_message(smtp_cls):
    raw = smtp_cls.return_value.sendmail.call_args[0][2]
    return email.message_from_string(raw, policy=email.policy.default)


def message_parts(message):
    return {part.get_content_type(): part.get_content() for part in message.iter_parts()}


class SendEmailTests(unittest.TestCase):
    CONFIG = {
        "mail_user": "me@gmail.com",
        "mail_password": "app-password",
        "mail_to": "to@example.com",
    }

    def _send(self, config=None, subject="测试邮件", html="<p>你好</p>", text="你好"):
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            result = reporting.send_email(config or dict(self.CONFIG), subject, html, text)
        return result, smtp_cls

    def test_gmail_defaults_and_message(self):
        result, smtp_cls = self._send()
        self.assertTrue(result)
        smtp_cls.assert_called_once_with("smtp.gmail.com", 465, timeout=30)
        server = smtp_cls.return_value
        server.login.assert_called_once_with("me@gmail.com", "app-password")
        sendmail = server.sendmail.call_args[0]
        self.assertEqual(sendmail[0], "me@gmail.com")
        self.assertEqual(sendmail[1], ["to@example.com"])
        server.quit.assert_called_once_with()

        message = parse_sent_message(smtp_cls)
        self.assertEqual(message["Subject"], "测试邮件")
        self.assertEqual(message["To"], "to@example.com")
        parts = message_parts(message)
        self.assertEqual(parts["text/plain"].strip(), "你好")
        self.assertIn("你好", parts["text/html"])

    def test_custom_host_and_port(self):
        config = dict(self.CONFIG, mail_host="smtp.qq.com", mail_port=465)
        _, smtp_cls = self._send(config=config)
        smtp_cls.assert_called_once_with("smtp.qq.com", 465, timeout=30)

    def test_mail_to_defaults_to_sender(self):
        config = {"mail_user": "me@gmail.com", "mail_password": "pw"}
        _, smtp_cls = self._send(config=config)
        self.assertEqual(smtp_cls.return_value.sendmail.call_args[0][1], ["me@gmail.com"])

    def test_multiple_recipients_are_deduped(self):
        config = dict(
            self.CONFIG, mail_to="a@x.com, b@y.com；a@x.com;c@z.com"
        )
        _, smtp_cls = self._send(config=config)
        self.assertEqual(
            smtp_cls.return_value.sendmail.call_args[0][1],
            ["a@x.com", "b@y.com", "c@z.com"],
        )

    def test_missing_user_raises_without_connecting(self):
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            with self.assertRaises(reporting.MailError) as context:
                reporting.send_email({"mail_password": "pw", "mail_to": "a@x.com"}, "s", "<p>x</p>")
        self.assertIn("mail_user", str(context.exception))
        smtp_cls.assert_not_called()

    def test_missing_password_raises(self):
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            with self.assertRaises(reporting.MailError):
                reporting.send_email({"mail_user": "me@gmail.com", "mail_to": "a@x.com"}, "s", "<p>x</p>")
        smtp_cls.assert_not_called()

    def test_invalid_port_raises(self):
        config = dict(self.CONFIG, mail_port="不是端口")
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            with self.assertRaises(reporting.MailError):
                reporting.send_email(config, "s", "<p>x</p>")
        smtp_cls.assert_not_called()

    def test_connection_error_wrapped(self):
        with mock.patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")) as smtp_cls:
            with self.assertRaises(reporting.MailError) as context:
                reporting.send_email(dict(self.CONFIG), "s", "<p>x</p>")
        self.assertIn("connection refused", str(context.exception))
        smtp_cls.assert_called_once()

    def test_auth_error_wrapped_and_quit_attempted(self):
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            server = smtp_cls.return_value
            server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
            with self.assertRaises(reporting.MailError) as context:
                reporting.send_email(dict(self.CONFIG), "s", "<p>x</p>")
            server.quit.assert_called_once_with()
        self.assertIn("535", str(context.exception))

    def test_html_only_builds_plain_fallback(self):
        _, smtp_cls = self._send(html="<p>纯 <b>HTML</b> 内容</p>", text="")
        parts = message_parts(parse_sent_message(smtp_cls))
        self.assertIn("纯", parts["text/plain"])
        self.assertNotIn("<p>", parts["text/plain"])
        self.assertIn("<b>HTML</b>", parts["text/html"])

    def test_empty_content_raises(self):
        with mock.patch("smtplib.SMTP_SSL") as smtp_cls:
            with self.assertRaises(reporting.MailError):
                reporting.send_email(dict(self.CONFIG), "s", "", "")
        smtp_cls.assert_not_called()


# ===========================================================================
# reporting：build_weekly_email
# ===========================================================================

class BuildWeeklyEmailTests(unittest.TestCase):
    def test_full_report_sections(self):
        subject, html, text = reporting.build_weekly_email(
            weekly_report_data(), "整体不错，建议优先处理逾期任务。"
        )
        self.assertEqual(subject, "【周报】2026-10-19 ~ 2026-10-25 待办与账单")
        for keyword in (
            "本周完成",
            "未完成",
            "逾期",
            "改进建议",
            "本周账单",
            "AI 评价",
            "写周报",
            "交报告",
            "整理桌面",
            "每天固定时间复盘",
            "减少多任务切换",
            "整体不错",
            "收入 ¥100.00",
            "支出 ¥37.50",
            "结余 ¥62.50",
            "餐饮",
            "¥12.50",
            "午饭",
            "写报告：1/3",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, html)
                self.assertIn(keyword, text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("</", text)
        self.assertIn("完成 1 项", html)
        self.assertIn("未完成 1 项", html)
        self.assertIn("逾期 1 项", html)

    def test_empty_report_data_does_not_crash(self):
        subject, html, text = reporting.build_weekly_email({})
        self.assertEqual(subject, "【周报】待办与账单")
        self.assertIn("完成 0 项", html)
        self.assertIn("本周还没有完成任何待办", text)
        self.assertIn("本周没有账单明细", text)

    def test_none_report_data_does_not_crash(self):
        subject, html, text = reporting.build_weekly_email(None)
        self.assertEqual(subject, "【周报】待办与账单")
        self.assertIn("AI 评价", html)

    def test_html_escapes_user_content(self):
        data = {
            "week_start": WEEK_START,
            "completed": [{"text": "<script>alert(1)</script>", "done_at": "2026-10-20T10:00:00"}],
        }
        _, html, text = reporting.build_weekly_email(data)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        # 纯文本不需要转义
        self.assertIn("<script>alert(1)</script>", text)

    def test_ai_comment_newlines_become_br(self):
        _, html, _ = reporting.build_weekly_email(
            {"week_start": WEEK_START}, "第一行\n第二行"
        )
        self.assertIn("第一行<br>第二行", html)

    def test_ai_comment_placeholder(self):
        _, html, text = reporting.build_weekly_email({"week_start": WEEK_START})
        self.assertIn("（暂无 AI 评价）", html)
        self.assertIn("（暂无 AI 评价）", text)

    def test_subtask_progress_list_form(self):
        data = {
            "week_start": WEEK_START,
            "subtask_progress": [{"text": "写报告", "done": 2, "total": 5}],
        }
        _, html, _ = reporting.build_weekly_email(data)
        self.assertIn("写报告：2/5", html)

    def test_week_start_string_accepted(self):
        subject, _, _ = reporting.build_weekly_email({"week_start": "2026-10-19"})
        self.assertIn("2026-10-19 ~ 2026-10-25", subject)

    def test_invalid_week_start_does_not_crash(self):
        subject, _, _ = reporting.build_weekly_email({"week_start": "不是日期"})
        self.assertIn("【周报】", subject)

    def test_ledger_entries_render_type_and_note(self):
        _, html, text = reporting.build_weekly_email(weekly_report_data())
        self.assertIn("收入 · 工资 ¥100.00", html)
        self.assertIn("支出 · 餐饮 ¥12.50（午饭）", html)
        self.assertIn("支出 · 餐饮 ¥12.50（午饭）", text)

    def test_negative_net_matches_core_money_format(self):
        data = {
            "week_start": WEEK_START,
            "ledger": {
                "income": 0.0,
                "expense": 30.0,
                "net": -30.0,
                "by_category": {"购物": {"income": 0.0, "expense": 30.0}},
                "entries": [
                    {"date": "2026-10-20", "type": "expense", "amount": 30.0, "category": "购物"}
                ],
            },
        }
        _, html, text = reporting.build_weekly_email(data)
        self.assertIn("结余 -¥30.00", html)
        self.assertIn("结余 -¥30.00", text)
        self.assertNotIn("¥-30.00", html)


# ===========================================================================
# memory_sync：MemoryRepo（mock requests.Session）
# ===========================================================================

class MemoryRepoTests(unittest.TestCase):
    TOKEN = "gh-token"

    def _repo(self, session, **kwargs):
        return memory_sync.MemoryRepo(self.TOKEN, session=session, **kwargs)

    def test_configured_property(self):
        self.assertTrue(self._repo(make_session()).configured)
        self.assertFalse(memory_sync.MemoryRepo("", session=make_session()).configured)
        self.assertFalse(self._repo(make_session(), repo="not-a-repo").configured)

    def test_ensure_repo_existing(self):
        session = make_session(
            get=[make_response(payload={"full_name": memory_sync.DEFAULT_REPO, "private": True})]
        )
        repo = self._repo(session)
        info = repo.ensure_repo()
        self.assertEqual(info["full_name"], memory_sync.DEFAULT_REPO)
        url = session.get.call_args[0][0]
        self.assertEqual(url, "https://api.github.com/repos/luongthimydieu/taskbar-memory")
        headers = session.get.call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer gh-token")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        session.post.assert_not_called()

    def test_ensure_repo_creates_private_repo_on_404(self):
        created = {
            "full_name": memory_sync.DEFAULT_REPO,
            "private": True,
            "default_branch": "main",
        }
        session = make_session(
            get=[make_response(status=404)],
            post=[make_response(status=201, payload=created)],
        )
        repo = self._repo(session)
        info = repo.ensure_repo()
        self.assertEqual(info["full_name"], memory_sync.DEFAULT_REPO)
        url = session.post.call_args[0][0]
        self.assertEqual(url, "https://api.github.com/user/repos")
        body = session.post.call_args[1]["json"]
        self.assertEqual(body["name"], "taskbar-memory")
        self.assertTrue(body["private"])
        self.assertTrue(body["auto_init"])
        self.assertIn("Win11", body["description"])
        self.assertEqual(
            session.post.call_args[1]["headers"]["Content-Type"], "application/json"
        )

    def test_ensure_repo_detects_wrong_token_account(self):
        session = make_session(
            get=[make_response(status=404)],
            post=[make_response(status=201, payload={"full_name": "someone/taskbar-memory"})],
        )
        with self.assertRaises(memory_sync.MemorySyncError) as context:
            self._repo(session).ensure_repo()
        self.assertIn("someone/taskbar-memory", str(context.exception))

    def test_ensure_repo_401_raises(self):
        session = make_session(get=[make_response(status=401)])
        with self.assertRaises(memory_sync.MemorySyncError) as context:
            self._repo(session).ensure_repo()
        self.assertEqual(context.exception.status, 401)
        self.assertIn("Token", str(context.exception))

    def test_ensure_repo_missing_token_no_network(self):
        session = make_session()
        repo = memory_sync.MemoryRepo("", session=session)
        with self.assertRaises(memory_sync.MemorySyncError):
            repo.ensure_repo()
        session.get.assert_not_called()

    def test_invalid_repo_format_raises(self):
        session = make_session()
        repo = memory_sync.MemoryRepo(self.TOKEN, repo="taskbar-memory", session=session)
        with self.assertRaises(memory_sync.MemorySyncError):
            repo.ensure_repo()
        session.get.assert_not_called()

    def test_get_file_decodes_base64(self):
        content = base64.b64encode("# 每日记忆\n".encode("utf-8")).decode("ascii")
        session = make_session(
            get=[make_response(payload={"content": content, "encoding": "base64", "sha": "abc"})]
        )
        repo = self._repo(session, branch="dev")
        self.assertEqual(repo.get_file("memories/2026-10-20.md"), "# 每日记忆\n")
        url = session.get.call_args[0][0]
        self.assertEqual(
            url,
            "https://api.github.com/repos/luongthimydieu/taskbar-memory/contents/memories/2026-10-20.md",
        )
        self.assertEqual(session.get.call_args[1]["params"], {"ref": "dev"})

    def test_get_file_missing_returns_none(self):
        session = make_session(get=[make_response(status=404)])
        self.assertIsNone(self._repo(session).get_file("memories/index.json"))

    def test_get_file_directory_raises(self):
        session = make_session(get=[make_response(payload=[{"name": "a.md"}])])
        with self.assertRaises(memory_sync.MemorySyncError):
            self._repo(session).get_file("memories")

    def test_get_file_corrupt_base64_raises(self):
        session = make_session(get=[make_response(payload={"content": "abc", "encoding": "base64"})])
        with self.assertRaises(memory_sync.MemorySyncError):
            self._repo(session).get_file("memories/x.md")

    def test_get_file_http_error_raises(self):
        session = make_session(get=[make_response(status=500)])
        with self.assertRaises(memory_sync.MemorySyncError) as context:
            self._repo(session).get_file("memories/x.md")
        self.assertEqual(context.exception.status, 500)

    def test_put_file_creates_new_file(self):
        html_url = "https://github.com/luongthimydieu/taskbar-memory/blob/main/memories/x.md"
        session = make_session(
            get=[make_response(status=404)],
            put=[make_response(status=201, payload={"content": {"html_url": html_url}})],
        )
        repo = self._repo(session, branch="main")
        self.assertEqual(
            repo.put_file("memories/x.md", "# 内容\n", "memory: 2026-10-20"), html_url
        )
        body = session.put.call_args[1]["json"]
        self.assertEqual(body["branch"], "main")
        self.assertEqual(body["message"], "memory: 2026-10-20")
        self.assertEqual(base64.b64decode(body["content"]).decode("utf-8"), "# 内容\n")
        self.assertNotIn("sha", body)

    def test_put_file_updates_with_sha(self):
        html_url = "https://github.com/x/y/blob/main/memories/x.md"
        session = make_session(
            get=[make_response(payload={"sha": "old-sha"})],
            put=[make_response(status=200, payload={"content": {"html_url": html_url}})],
        )
        repo = self._repo(session)
        repo.put_file("memories/x.md", "new")
        self.assertEqual(session.put.call_args[1]["json"]["sha"], "old-sha")

    def test_put_file_422_raises(self):
        session = make_session(
            get=[make_response(status=404)],
            put=[make_response(status=422, payload={"message": "sha missing"})],
        )
        with self.assertRaises(memory_sync.MemorySyncError) as context:
            self._repo(session).put_file("memories/x.md", "x")
        self.assertEqual(context.exception.status, 422)

    def test_put_file_empty_path_raises(self):
        session = make_session()
        with self.assertRaises(memory_sync.MemorySyncError):
            self._repo(session).put_file("", "x")
        session.get.assert_not_called()


# ===========================================================================
# memory_sync：build_daily_memory
# ===========================================================================

def daily_doc():
    return {
        "schema": 3,
        "todos": [
            {
                "id": "t1",
                "text": "写周报",
                "done": True,
                "done_at": "2026-10-20T18:00:00+08:00",
                "due_date": "2026-10-20",
                "estimate_minutes": 30,
            },
            {"id": "t2", "text": "交报告", "done": False, "due_date": "2026-10-20"},
            {"id": "t3", "text": "整理桌面", "done": False, "due_date": "2026-10-18"},
            {"id": "t4", "text": "未来的事", "done": False, "due_date": "2026-10-25"},
            {
                "id": "t5",
                "text": "今天创建的",
                "done": False,
                "created_at": "2026-10-20T10:00:00+08:00",
            },
            {
                "id": "t6",
                "text": "昨天完成的",
                "done": True,
                "done_at": "2026-10-19T19:00:00+08:00",
            },
        ],
    }


def daily_ledger():
    return {
        "schema": 1,
        "entries": [
            {
                "id": "lg-1",
                "date": "2026-10-20",
                "type": "income",
                "amount": 100.0,
                "category": "工资",
                "note": "",
            },
            {
                "id": "lg-2",
                "date": "2026-10-20",
                "type": "expense",
                "amount": 12.5,
                "category": "餐饮",
                "note": "午饭",
            },
            {
                "id": "lg-3",
                "date": "2026-10-19",
                "type": "expense",
                "amount": 5.0,
                "category": "交通",
                "note": "昨天",
            },
        ],
    }


class BuildDailyMemoryTests(unittest.TestCase):
    def test_full_memory(self):
        markdown = memory_sync.build_daily_memory(
            daily_doc(), daily_ledger(), NOW, "今天效率不错。"
        )
        self.assertIn("# 每日记忆 · 2026-10-20（周二）", markdown)
        self.assertIn("## 今日完成（1）", markdown)
        self.assertIn("✅ 写周报", markdown)
        self.assertIn("预计 30 分钟", markdown)
        self.assertNotIn("昨天完成的", markdown)
        self.assertIn("## 未完成 / 逾期（3）", markdown)
        self.assertIn("⏳ 交报告", markdown)
        self.assertIn("⚠️ 整理桌面", markdown)
        self.assertIn("逾期 2 天", markdown)
        self.assertNotIn("未来的事", markdown)
        self.assertIn("## 今日账单（2 笔）", markdown)
        self.assertIn("收入 ¥100.00 · 支出 ¥12.50 · 结余 ¥87.50", markdown)
        self.assertIn("支出 · 餐饮 ¥12.50（午饭）", markdown)
        self.assertNotIn("¥5.00", markdown)
        self.assertIn("## AI 评价", markdown)
        self.assertIn("今天效率不错。", markdown)
        self.assertIn("23:30", markdown)

    def test_empty_data(self):
        markdown = memory_sync.build_daily_memory({}, {}, "2026-10-20")
        self.assertIn("2026-10-20", markdown)
        self.assertIn("今天没有完成记录", markdown)
        self.assertIn("今天没有记账", markdown)
        self.assertIn("（今日暂无 AI 评价）", markdown)

    def test_none_data(self):
        markdown = memory_sync.build_daily_memory(None, None, NOW)
        self.assertIn("每日记忆", markdown)

    def test_moment_as_iso_string(self):
        markdown = memory_sync.build_daily_memory({}, {}, "2026-10-20T10:00:00+08:00")
        self.assertIn("2026-10-20", markdown)

    def test_done_without_done_at_uses_due_date(self):
        doc = {
            "todos": [
                {"id": "x", "text": "补记完成", "done": True, "due_date": "2026-10-20"}
            ]
        }
        markdown = memory_sync.build_daily_memory(doc, {}, NOW)
        self.assertIn("✅ 补记完成", markdown)

    def test_multiline_todo_text_is_flattened(self):
        doc = {"todos": [{"id": "x", "text": "第一行\n## 假标题", "done": True, "done_at": "2026-10-20T08:00:00"}]}
        markdown = memory_sync.build_daily_memory(doc, {}, NOW)
        self.assertNotIn("\n## 假标题", markdown)

    def test_negative_balance_matches_core_money_format(self):
        ledger = {
            "entries": [
                {
                    "id": "lg-1",
                    "date": "2026-10-20",
                    "type": "expense",
                    "amount": 30.0,
                    "category": "购物",
                    "note": "",
                }
            ]
        }
        markdown = memory_sync.build_daily_memory({}, ledger, NOW)
        self.assertIn("结余 -¥30.00", markdown)
        self.assertNotIn("¥-30.00", markdown)


# ===========================================================================
# memory_sync：push_daily_memory（patch requests.Session，绝不联网）
# ===========================================================================

class PushDailyMemoryTests(unittest.TestCase):
    REPO = "luongthimydieu/taskbar-memory"
    MD_URL = "https://github.com/luongthimydieu/taskbar-memory/blob/main/memories/2026-10-20.md"
    INDEX_URL = "https://github.com/luongthimydieu/taskbar-memory/blob/main/memories/index.json"

    def config(self, **overrides):
        config = {
            "memory_enabled": True,
            "memory_token": "gh-token",
            "memory_repo": self.REPO,
            "memory_branch": "main",
        }
        config.update(overrides)
        return config

    @staticmethod
    def index_payload(entries):
        raw = json.dumps(entries, ensure_ascii=False)
        return make_response(
            payload={
                "sha": "idx-sha-1",
                "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
                "encoding": "base64",
            }
        )

    def _push(self, config, session):
        with mock.patch("requests.Session", return_value=session) as session_cls:
            result = memory_sync.push_daily_memory(
                config, daily_doc(), daily_ledger(), NOW, "今天效率不错。"
            )
        session_cls.assert_called_once()
        return result

    def test_push_writes_memory_and_index(self):
        old_entry = {
            "date": "2026-10-19",
            "file": "memories/2026-10-19.md",
            "url": "https://old/2026-10-19.md",
        }
        session = make_session(
            get=[
                make_response(payload={"full_name": self.REPO}),
                make_response(status=404),  # memories/2026-10-20.md 不存在
                self.index_payload([old_entry]),
                make_response(payload={"sha": "idx-sha-1"}),  # 写入 index 前取 sha
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=200, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        result = self._push(self.config(), session)
        self.assertEqual(result["date"], "2026-10-20")
        self.assertEqual(result["path"], "memories/2026-10-20.md")
        self.assertEqual(result["markdown_url"], self.MD_URL)
        self.assertEqual(result["index_url"], self.INDEX_URL)
        self.assertEqual(result["index_count"], 2)

        get_urls = [call[0][0] for call in session.get.call_args_list]
        self.assertEqual(
            get_urls,
            [
                "https://api.github.com/repos/luongthimydieu/taskbar-memory",
                "https://api.github.com/repos/luongthimydieu/taskbar-memory/contents/memories/2026-10-20.md",
                "https://api.github.com/repos/luongthimydieu/taskbar-memory/contents/memories/index.json",
                "https://api.github.com/repos/luongthimydieu/taskbar-memory/contents/memories/index.json",
            ],
        )

        memory_put = session.put.call_args_list[0][1]["json"]
        markdown = base64.b64decode(memory_put["content"]).decode("utf-8")
        self.assertIn("每日记忆 · 2026-10-20", markdown)
        self.assertEqual(memory_put["message"], "memory: 2026-10-20")
        self.assertEqual(memory_put["branch"], "main")

        index_put = session.put.call_args_list[1][1]["json"]
        self.assertEqual(index_put["sha"], "idx-sha-1")
        entries = json.loads(base64.b64decode(index_put["content"]).decode("utf-8"))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["date"], "2026-10-20")
        self.assertEqual(entries[0]["url"], self.MD_URL)
        self.assertEqual(entries[1]["date"], "2026-10-19")

    def test_index_missing_creates_first_entry(self):
        session = make_session(
            get=[
                make_response(payload={"full_name": self.REPO}),
                make_response(status=404),
                make_response(status=404),
                make_response(status=404),
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=201, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        result = self._push(self.config(), session)
        self.assertEqual(result["index_count"], 1)
        index_put = session.put.call_args_list[1][1]["json"]
        self.assertNotIn("sha", index_put)
        entries = json.loads(base64.b64decode(index_put["content"]).decode("utf-8"))
        self.assertEqual(entries, [{"date": "2026-10-20", "file": "memories/2026-10-20.md", "url": self.MD_URL}])

    def test_index_same_date_is_replaced(self):
        stale = {
            "date": "2026-10-20",
            "file": "memories/2026-10-20.md",
            "url": "https://old/2026-10-20.md",
        }
        session = make_session(
            get=[
                make_response(payload={"full_name": self.REPO}),
                make_response(status=404),
                self.index_payload([stale]),
                make_response(payload={"sha": "idx-sha-1"}),
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=200, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        result = self._push(self.config(), session)
        self.assertEqual(result["index_count"], 1)
        entries = json.loads(
            base64.b64decode(session.put.call_args_list[1][1]["json"]["content"]).decode("utf-8")
        )
        self.assertEqual(entries[0]["url"], self.MD_URL)

    def test_index_sorted_desc_and_capped_at_400(self):
        dates = [(WEEK_START - _dt.timedelta(days=offset)).isoformat() for offset in range(400)]
        session = make_session(
            get=[
                make_response(payload={"full_name": self.REPO}),
                make_response(status=404),
                self.index_payload(dates),  # 旧索引允许是纯日期字符串
                make_response(payload={"sha": "idx-sha-1"}),
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=200, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        result = self._push(self.config(), session)
        entries = json.loads(
            base64.b64decode(session.put.call_args_list[1][1]["json"]["content"]).decode("utf-8")
        )
        self.assertEqual(result["index_count"], memory_sync.MAX_INDEX_ENTRIES)
        self.assertEqual(len(entries), memory_sync.MAX_INDEX_ENTRIES)
        self.assertEqual(entries[0]["date"], "2026-10-20")
        rendered_dates = [entry["date"] for entry in entries]
        self.assertNotIn(dates[-1], rendered_dates)  # 最旧一条被裁掉
        self.assertIn(dates[0], rendered_dates)
        self.assertEqual(rendered_dates, sorted(rendered_dates, reverse=True))

    def test_corrupt_index_is_rebuilt(self):
        session = make_session(
            get=[
                make_response(payload={"full_name": self.REPO}),
                make_response(status=404),
                make_response(payload={"content": base64.b64encode(b"not json").decode(), "encoding": "base64"}),
                make_response(status=404),
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=201, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        with self.assertLogs("taskbar.memory", level="WARNING"):
            result = self._push(self.config(), session)
        self.assertEqual(result["index_count"], 1)

    def test_disabled_returns_none_without_network(self):
        session = make_session()
        with mock.patch("requests.Session", return_value=session) as session_cls:
            result = memory_sync.push_daily_memory(
                self.config(memory_enabled=False), daily_doc(), daily_ledger(), NOW
            )
        self.assertIsNone(result)
        session_cls.assert_not_called()

    def test_missing_token_raises_without_network(self):
        session = make_session()
        with mock.patch("requests.Session", return_value=session) as session_cls:
            with self.assertRaises(memory_sync.MemorySyncError):
                memory_sync.push_daily_memory(
                    self.config(memory_token=""), daily_doc(), daily_ledger(), NOW
                )
        session_cls.assert_not_called()

    def test_api_failure_propagates(self):
        session = make_session(
            get=[make_response(payload={"full_name": self.REPO}), make_response(status=404)],
            put=[make_response(status=403)],
        )
        with self.assertRaises(memory_sync.MemorySyncError) as context:
            self._push(self.config(), session)
        self.assertEqual(context.exception.status, 403)

    def test_custom_repo_and_branch(self):
        session = make_session(
            get=[
                make_response(payload={"full_name": "me/my-memory"}),
                make_response(status=404),
                make_response(status=404),
                make_response(status=404),
            ],
            put=[
                make_response(status=201, payload={"content": {"html_url": self.MD_URL}}),
                make_response(status=201, payload={"content": {"html_url": self.INDEX_URL}}),
            ],
        )
        result = self._push(
            self.config(memory_repo="me/my-memory", memory_branch="dev"), session
        )
        self.assertEqual(result["index_count"], 1)
        md_get = session.get.call_args_list[1]
        self.assertEqual(
            md_get[0][0],
            "https://api.github.com/repos/me/my-memory/contents/memories/2026-10-20.md",
        )
        self.assertEqual(md_get[1]["params"], {"ref": "dev"})
        self.assertEqual(session.put.call_args_list[0][1]["json"]["branch"], "dev")


if __name__ == "__main__":
    unittest.main(verbosity=2)
