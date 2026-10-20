#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reporting.py — 周报邮件的渲染与发送（Gmail SMTP_SSL）。

契约见 ``docs/08-待办模块升级设计.md`` 第 4 节。本模块只用标准库
（smtplib / email / html / re），不依赖 PyQt6、不依赖 core，也不联网直到真正
调用 ``send_email``；单元测试用 ``unittest.mock`` 模拟 ``smtplib.SMTP_SSL``。

对外接口：

- ``send_email(config, subject, html, text="") -> True``
  配置键：``mail_host``（默认 smtp.gmail.com）、``mail_port``（默认 465）、
  ``mail_user``、``mail_password``、``mail_to``（默认同 mail_user）。
- ``build_weekly_email(report_data, ai_comment="") -> (subject, html, text)``
  ``report_data`` 由 ``core.weekly_report_data`` 生成，本函数只负责渲染，字段缺失时
  会降级为空/0，保证周报不会因为某一块数据缺失而发不出去。
- ``format_week_range(week_start) -> "YYYY-MM-DD ~ YYYY-MM-DD"``

失败统一抛 ``MailError``，桌面端只需提示托盘消息，不阻塞主 UI。
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import math
import re
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_MAIL_HOST",
    "DEFAULT_MAIL_PORT",
    "MailError",
    "build_weekly_email",
    "format_week_range",
    "send_email",
]

DEFAULT_MAIL_HOST = "smtp.gmail.com"
DEFAULT_MAIL_PORT = 465
DEFAULT_TIMEOUT = 30


class MailError(RuntimeError):
    """邮件发送失败（配置缺失 / SMTP 连接或认证失败）。"""


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_day(value: Any) -> Optional[_dt.date]:
    """date / datetime / ISO 字符串 -> date；无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = _as_text(value)
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _number(value: Any) -> float:
    """尽量把账单金额转成 float；非法值按 0 处理。"""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return amount if math.isfinite(amount) else 0.0


def _money(value: Any) -> str:
    """与 core.format_money 保持一致的金额格式：``¥12.50`` / ``-¥3.00``。"""
    amount = _number(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}¥{abs(amount):.2f}"


def _escape(value: Any) -> str:
    return _html.escape(_as_text(value), quote=True)


def _parse_recipients(value: Any) -> List[str]:
    """把 ``"a@x.com, b@y.com"`` / ``["a", "b"]`` 解析成去重后的列表。"""
    if isinstance(value, str):
        raw_items: Iterable[Any] = re.split(r"[,;，；\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        return []
    recipients: List[str] = []
    for item in raw_items:
        address = _as_text(item)
        if address and address not in recipients:
            recipients.append(address)
    return recipients


def _html_to_text(markup: str) -> str:
    """把简单 HTML 转成可读纯文本（用于只给了 html 时的文本兜底）。"""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_week_range(week_start: Any) -> str:
    """把周起始日格式化成 ``YYYY-MM-DD ~ YYYY-MM-DD``（含周一到周日共 7 天）。

    ``week_start`` 可以是 ``date`` / ``datetime`` / ``"YYYY-MM-DD"`` 字符串。
    """
    start = _coerce_day(week_start)
    if start is None:
        raise ValueError(f"week_start 不是合法日期：{week_start!r}")
    end = start + _dt.timedelta(days=6)
    return f"{start.isoformat()} ~ {end.isoformat()}"


# ---------------------------------------------------------------------------
# 发送邮件
# ---------------------------------------------------------------------------

def send_email(config: Mapping[str, Any], subject: Any, html: Any, text: Any = "") -> bool:
    """通过 SMTP_SSL 发送一封 HTML + 纯文本邮件，成功返回 True。

    配置键：``mail_host`` / ``mail_port`` / ``mail_user`` / ``mail_password`` /
    ``mail_to``。任何配置或发送失败都会抛 ``MailError``。
    """
    config = config if isinstance(config, Mapping) else {}
    user = _as_text(config.get("mail_user"))
    password = str(config.get("mail_password") or "")
    host = _as_text(config.get("mail_host")) or DEFAULT_MAIL_HOST
    recipients = _parse_recipients(config.get("mail_to") or user)

    if not user:
        raise MailError("尚未配置发件邮箱（mail_user）")
    if not password:
        raise MailError("尚未配置邮箱密码（mail_password，Gmail 需应用专用密码）")
    if not recipients:
        raise MailError("尚未配置收件人（mail_to）")
    try:
        port = int(config.get("mail_port") or DEFAULT_MAIL_PORT)
    except (TypeError, ValueError) as exc:
        raise MailError(f"mail_port 不是合法端口：{config.get('mail_port')!r}") from exc
    if not 0 < port < 65536:
        raise MailError(f"mail_port 超出范围：{port}")

    subject_text = _as_text(subject) or "Win11 任务栏助手"
    html_text = str(html or "")
    plain_text = _as_text(text) or _html_to_text(html_text)
    if not html_text and not plain_text:
        raise MailError("邮件内容为空")

    message = MIMEMultipart("alternative")
    message["Subject"] = Header(subject_text, "utf-8")
    message["From"] = formataddr(("Win11 任务栏助手", user))
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="taskbar.local")
    message.attach(MIMEText(plain_text, "plain", "utf-8"))
    if html_text:
        message.attach(MIMEText(html_text, "html", "utf-8"))
    serialized = message.as_string()

    server: Optional[smtplib.SMTP_SSL] = None
    try:
        server = smtplib.SMTP_SSL(host, port, timeout=DEFAULT_TIMEOUT)
        server.login(user, password)
        server.sendmail(user, recipients, serialized)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"发送邮件失败：{exc}") from exc
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - 收尾失败不影响发送结果
                pass
    return True


# ---------------------------------------------------------------------------
# 周报渲染
# ---------------------------------------------------------------------------

def _todo_items(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _todo_line(item: Any) -> str:
    """把一条周报待办渲染成一行纯文本（用户输入不信任，HTML 侧统一转义）。"""
    if isinstance(item, Mapping):
        text = _as_text(item.get("text")) or _as_text(item.get("title")) or "（无内容）"
        extras: List[str] = []
        due_date = _as_text(item.get("due_date"))
        if due_date:
            extras.append(f"目标 {due_date}")
        estimate = item.get("estimate_minutes")
        try:
            minutes = int(estimate or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            extras.append(f"预计 {minutes} 分钟")
        done_at = _as_text(item.get("done_at"))
        if done_at:
            extras.append(f"完成于 {done_at[:10]}")
        category = _as_text(item.get("category"))
        if category:
            extras.append(f"分类 {category}")
        if extras:
            return f"{text}（{'，'.join(extras)}）"
        return text
    return _as_text(item) or "（无内容）"


def _subtask_lines(value: Any) -> List[str]:
    """兼容 core.weekly_report_data 的多种 subtask_progress 形态。

    支持 ``{parent: (done, total)}``、``{parent: {"done":..,"total":..}}``、
    ``[{"text":..,"done":..,"total":..}]`` 等写法；无法识别的条目直接跳过。
    """
    lines: List[str] = []
    if isinstance(value, Mapping):
        items: Iterable[Any] = value.items()
    elif isinstance(value, (list, tuple)):
        items = enumerate(value)
    else:
        return lines
    for key, item in items:
        label = ""
        done: Any = None
        total: Any = None
        if isinstance(item, Mapping):
            label = _as_text(item.get("text")) or _as_text(item.get("title")) or _as_text(
                item.get("name")
            )
            done = item.get("done")
            total = item.get("total")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            done, total = item[0], item[1]
        else:
            continue
        try:
            done_count = int(done or 0)
            total_count = int(total or 0)
        except (TypeError, ValueError):
            continue
        if total_count <= 0:
            continue
        if not label:
            label = _as_text(key)
        if not label or re.fullmatch(r"(td|t)[-_]\w+", label):
            label = "子任务"
        lines.append(f"{label}：{done_count}/{total_count}")
    return lines


def _category_lines(by_category: Any) -> List[str]:
    if not isinstance(by_category, Mapping):
        return []
    lines: List[str] = []
    for name in sorted(by_category, key=lambda item: str(item)):
        info = by_category.get(name)
        if not isinstance(info, Mapping):
            continue
        income = _number(info.get("income"))
        expense = _number(info.get("expense"))
        bits: List[str] = []
        if income:
            bits.append(f"收入 {_money(income)}")
        if expense:
            bits.append(f"支出 {_money(expense)}")
        if bits:
            lines.append(f"{_as_text(name) or '未分类'}：{' / '.join(bits)}")
    return lines


def _ledger_entry_line(entry: Any) -> str:
    if not isinstance(entry, Mapping):
        return _as_text(entry)
    day = _as_text(entry.get("date"))[:10]
    kind = "收入" if _as_text(entry.get("type")).lower() == "income" else "支出"
    amount = _money(entry.get("amount"))
    category = _as_text(entry.get("category")) or "未分类"
    note = _as_text(entry.get("note"))
    prefix = f"{day} " if day else ""
    line = f"{prefix}{kind} · {category} {amount}"
    if note:
        line += f"（{note}）"
    return line


def _ledger_lines(ledger: Any) -> Tuple[List[str], Dict[str, Any]]:
    """返回（账单概览行列表，原始汇总字段）。"""
    data = ledger if isinstance(ledger, Mapping) else {}
    income = _number(data.get("income"))
    expense = _number(data.get("expense"))
    net = data.get("net")
    net_value = _number(net) if net is not None else income - expense
    summary_lines = [
        f"收入 {_money(income)} · 支出 {_money(expense)} · 结余 {_money(net_value)}"
    ]
    summary_lines.extend(_category_lines(data.get("by_category")))
    return summary_lines, data


def _html_list(lines: Sequence[str], empty: str = "暂无") -> str:
    if not lines:
        return f'<p class="muted">{_escape(empty)}</p>'
    items = "".join(f"<li>{_escape(line)}</li>" for line in lines)
    return f"<ul>{items}</ul>"


def build_weekly_email(
    report_data: Any, ai_comment: Any = ""
) -> Tuple[str, str, str]:
    """渲染周报邮件，返回 ``(subject, html, text)``。

    ``report_data`` 来自 ``core.weekly_report_data(doc, ledger, week_start)``，
    使用字段：``week_start`` / ``week_end`` / ``completed`` / ``pending`` /
    ``overdue`` / ``created`` / ``subtask_progress`` / ``improvements`` / ``ledger``。
    """
    data: Dict[str, Any] = report_data if isinstance(report_data, Mapping) else {}

    week_start = data.get("week_start") or data.get("week_end")
    range_text = ""
    if week_start is not None:
        try:
            range_text = format_week_range(week_start)
        except ValueError:
            range_text = _as_text(week_start)

    completed = [_todo_line(item) for item in _todo_items(data.get("completed"))]
    pending = [_todo_line(item) for item in _todo_items(data.get("pending"))]
    overdue = [_todo_line(item) for item in _todo_items(data.get("overdue"))]
    created = [_todo_line(item) for item in _todo_items(data.get("created"))]
    improvements = [
        _as_text(item)
        for item in _todo_items(data.get("improvements"))
        if _as_text(item)
    ]
    subtask_progress = _subtask_lines(data.get("subtask_progress"))
    ledger_summary, ledger_data = _ledger_lines(data.get("ledger"))
    ledger_entries = [
        _ledger_entry_line(item) for item in _todo_items(ledger_data.get("entries"))
    ]
    comment = _as_text(ai_comment) or "（暂无 AI 评价）"
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    subject = "【周报】" + (f"{range_text} " if range_text else "") + "待办与账单"
    title = f"{range_text} 周报" if range_text else "本周周报"

    overview = [
        f"完成 {len(completed)} 项",
        f"未完成 {len(pending)} 项",
        f"逾期 {len(overdue)} 项",
        f"本周新增 {len(created)} 项",
    ]

    # -- HTML -------------------------------------------------------------
    comment_html = _escape(comment).replace("\n", "<br>")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{_escape(subject)}</title>
<style>
body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#222;line-height:1.7;max-width:720px;margin:0 auto;padding:24px;}}
h1{{font-size:20px;margin:0 0 4px;}}
h2{{font-size:16px;margin:24px 0 8px;border-left:4px solid #4a90d9;padding-left:8px;}}
.muted{{color:#888;font-size:13px;}}
ul{{padding-left:22px;margin:6px 0;}}
li{{margin:2px 0;}}
.box{{background:#f7f9fc;border:1px solid #e3e9f2;border-radius:8px;padding:10px 14px;margin:8px 0;}}
</style>
</head>
<body>
<h1>📊 {_escape(title)}</h1>
<p class="muted">生成时间：{_escape(generated_at)}</p>
<h2>📈 本周概览</h2>
<div class="box">{_escape(' · '.join(overview))}</div>
<h2>✅ 本周完成</h2>
{_html_list(completed, '本周还没有完成任何待办')}
<h2>⏳ 未完成</h2>
{_html_list(pending, '没有未完成的待办')}
<h2>⚠️ 逾期</h2>
{_html_list(overdue, '没有逾期待办，继续保持')}
<h2>🆕 本周新增</h2>
{_html_list(created, '本周没有新增待办')}
<h2>🧩 子任务进度</h2>
{_html_list(subtask_progress, '没有进行中的父任务')}
<h2>💡 改进建议</h2>
{_html_list(improvements, '暂无，下周继续保持')}
<h2>💰 本周账单</h2>
{_html_list(ledger_summary, '本周还没有记账')}
<h3 style="font-size:14px;margin:14px 0 6px;">明细</h3>
{_html_list(ledger_entries, '本周没有账单明细')}
<h2>🤖 AI 评价</h2>
<p>{comment_html}</p>
</body>
</html>"""

    # -- 纯文本 -----------------------------------------------------------
    def block(name: str, lines: Sequence[str], empty: str) -> List[str]:
        body = list(lines) if lines else [empty]
        return [f"【{name}】", *[f"- {line}" for line in body], ""]

    text_lines: List[str] = [f"== {title} ==", f"生成时间：{generated_at}", ""]
    text_lines.append("【本周概览】")
    text_lines.append(f"- {' · '.join(overview)}")
    text_lines.append("")
    text_lines.extend(block("本周完成", completed, "本周还没有完成任何待办"))
    text_lines.extend(block("未完成", pending, "没有未完成的待办"))
    text_lines.extend(block("逾期", overdue, "没有逾期待办，继续保持"))
    text_lines.extend(block("本周新增", created, "本周没有新增待办"))
    text_lines.extend(block("子任务进度", subtask_progress, "没有进行中的父任务"))
    text_lines.extend(block("改进建议", improvements, "暂无，下周继续保持"))
    text_lines.append("【本周账单】")
    text_lines.extend(f"- {line}" for line in ledger_summary)
    text_lines.append("- 明细：")
    if ledger_entries:
        text_lines.extend(f"  - {line}" for line in ledger_entries)
    else:
        text_lines.append("  - 本周没有账单明细")
    text_lines.append("")
    text_lines.append("【AI 评价】")
    text_lines.append(comment)
    text_lines.append("")
    text = "\n".join(text_lines).strip() + "\n"

    return subject, html, text
