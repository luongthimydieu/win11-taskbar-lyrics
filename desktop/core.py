#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — Win11 任务栏歌词 / 网易云热评 / 跨端待办 的“无界面核心层”。

这里只放**与 Qt 无关**的逻辑，方便被桌面端 UI、单元测试复用：

1. 同步文档模型 + 三端一致的合并协议（version / tombstones / 按条目 last-write-wins）
2. 提醒调度（提醒日期 + 时间 → 到期判定、24h 补提醒窗口、稍后提醒）
3. LRC 歌词解析与当前行定位
4. GitHub Gist REST 客户端（ETag 条件请求、读写、建库、错误分类）
5. 网易云搜索 / 歌词 / 高赞热评客户端（只缓存成功结果，失败可重试）

本模块只依赖标准库 + requests，不导入 PyQt6，因此可以在没有图形环境的地方
（例如 CI、纯 Python 解释器）直接跑测试：

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

LOGGER = logging.getLogger("taskbar.core")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2
GIST_FILENAME = "taskbar_todos.json"
API_BASE = "https://api.github.com"

PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"

#: 提醒“过期”后仍补提醒的时间窗口（秒）。24 小时意味着电脑关机一晚，
#: 第二天开机仍会补一次提醒；超过这个窗口则不再打扰。
REMIND_GRACE_SECONDS = 24 * 3600

#: 删除墓碑（tombstone）保留天数与数量上限，避免 Gist 文件无限膨胀。
TOMBSTONE_TTL_DAYS = 180
MAX_TOMBSTONES = 200

#: 网易云结果缓存时间（正面结果）与失败后的重试冷却。
NETEASE_CACHE_TTL = 30 * 60
NETEASE_RETRY_COOLDOWN = 60

_EPOCH_ISO = "1970-01-01T00:00:00+00:00"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# 时间工具（所有落盘时间统一 ISO8601 + 时区偏移，秒级精度）
# ---------------------------------------------------------------------------

def now_local() -> _dt.datetime:
    """当前本地时间（带时区）。"""
    return _dt.datetime.now().astimezone()


def to_iso(value: Optional[_dt.datetime] = None) -> str:
    """datetime -> ISO8601 字符串；None 表示当前时间。"""
    return (value or now_local()).isoformat(timespec="seconds")


def parse_iso(text: Any) -> Optional[_dt.datetime]:
    """ISO8601 / 常见时间字符串 -> 带时区 datetime；解析失败返回 None。"""
    if not text or not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None
    try:
        value = _dt.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                value = _dt.datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if value.tzinfo is None:
        # 旧数据没有时区：按本机时区理解
        value = value.astimezone()
    return value


def iso_rank(text: Any) -> float:
    """把时间字符串转成可比较的秒级浮点；无法解析视为 0（最旧）。"""
    value = parse_iso(text)
    return value.timestamp() if value else 0.0


def _normalize_iso(text: Any, fallback: str = _EPOCH_ISO) -> str:
    value = parse_iso(text)
    return to_iso(value) if value else fallback


# ---------------------------------------------------------------------------
# 文档模型
# ---------------------------------------------------------------------------

def new_id(prefix: str = "td") -> str:
    """生成全局唯一 id（时间戳 + 随机串，手机与电脑端生成的 id 不会冲突）。"""
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def _normalize_remind_at(text: Any) -> str:
    value = str(text or "").strip()
    return value if _TIME_RE.match(value) else ""


def _normalize_remind_date(text: Any) -> str:
    value = str(text or "").strip()
    if not _DATE_RE.match(value):
        return ""
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def normalize_todo(raw: Any) -> Optional[Dict[str, Any]]:
    """把任意来源的单条待办归一化为 schema v2 结构；无法修复时返回 None。

    兼容 v1 字段：``remind_time`` / ``createdAt`` / ``updatedAt`` 等。
    """
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or raw.get("title") or raw.get("content") or "").strip()
    if not text:
        return None

    created = _normalize_iso(raw.get("created_at") or raw.get("createdAt"), _EPOCH_ISO)
    updated_raw = raw.get("updated_at") or raw.get("updatedAt")
    updated = _normalize_iso(updated_raw, created)

    priority = str(raw.get("priority") or PRIORITY_NORMAL).strip().lower()
    if priority not in (PRIORITY_HIGH, PRIORITY_NORMAL):
        priority = PRIORITY_NORMAL

    done = bool(raw.get("done"))
    done_at = _normalize_iso(raw.get("done_at"), "") if done else ""
    if done and not done_at:
        done_at = updated
    notified_at = _normalize_iso(raw.get("notified_at"), "") if raw.get("notified_at") else ""

    return {
        "id": str(raw.get("id") or new_id()),
        "text": text,
        "priority": priority,
        "done": done,
        "created_at": created,
        "updated_at": updated,
        "remind_at": _normalize_remind_at(raw.get("remind_at") or raw.get("remind_time")),
        "remind_date": _normalize_remind_date(raw.get("remind_date") or raw.get("remindDate")),
        "notified_at": notified_at,
        "done_at": done_at,
    }


def normalize_doc(raw: Any) -> Dict[str, Any]:
    """把云端 / 本地缓存 / 旧版 JSON 归一化为 schema v2 文档。"""
    doc: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "version": 0,
        "updated_at": _EPOCH_ISO,
        "todos": [],
        "tombstones": [],
    }
    if not isinstance(raw, dict):
        return doc

    todos = raw.get("todos")
    if isinstance(todos, list):
        seen: set = set()
        for entry in todos:
            todo = normalize_todo(entry)
            if todo and todo["id"] not in seen:
                seen.add(todo["id"])
                doc["todos"].append(todo)

    tombstones = raw.get("tombstones")
    if isinstance(tombstones, list):
        seen_t: set = set()
        for entry in tombstones:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            key = str(entry["id"])
            if key in seen_t:
                continue
            deleted_at = _normalize_iso(entry.get("deleted_at"), "")
            if not deleted_at:
                continue
            seen_t.add(key)
            doc["tombstones"].append({"id": key, "deleted_at": deleted_at})

    try:
        version = int(raw.get("version") or raw.get("rev") or 0)
    except (TypeError, ValueError):
        version = 0
    doc["version"] = max(0, version)
    doc["updated_at"] = _normalize_iso(raw.get("updated_at"), _EPOCH_ISO)
    doc["todos"].sort(key=lambda item: item["id"])
    doc["tombstones"].sort(key=lambda item: item["id"])
    return doc


def canonical(doc: Any) -> str:
    """内容指纹：忽略 version / updated_at 的顺序无关 JSON，用于判断“有没有变化”。"""
    normalized = normalize_doc(doc)
    payload = {
        "todos": normalized["todos"],
        "tombstones": sorted(normalized["tombstones"], key=lambda item: item["id"]),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def docs_equal(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def merge_docs(local: Any, remote: Any) -> Dict[str, Any]:
    """三端一致的合并：按 id 取 updated_at 较新的条目，墓碑按 deleted_at 生效。

    - 同 id 条目：``updated_at`` 较新者胜（相等时远端胜，保证幂等）
    - 墓碑：``deleted_at >= todo.updated_at`` 时删除生效；否则删除被“复活”
    - 结果永远包含一个新的 ``version``（单调递增），便于排查同步轮次
    """
    left = normalize_doc(local)
    right = normalize_doc(remote)

    todos: Dict[str, Dict[str, Any]] = {item["id"]: item for item in left["todos"]}
    for item in right["todos"]:
        current = todos.get(item["id"])
        if current is None or iso_rank(item["updated_at"]) >= iso_rank(current["updated_at"]):
            todos[item["id"]] = item

    tombstones: Dict[str, Dict[str, Any]] = {
        item["id"]: item for item in left["tombstones"]
    }
    for item in right["tombstones"]:
        current = tombstones.get(item["id"])
        if current is None or iso_rank(item["deleted_at"]) > iso_rank(current["deleted_at"]):
            tombstones[item["id"]] = item

    # 墓碑与条目决斗
    for todo_id, tombstone in list(tombstones.items()):
        todo = todos.get(todo_id)
        if todo is None:
            continue
        if iso_rank(tombstone["deleted_at"]) >= iso_rank(todo["updated_at"]):
            todos.pop(todo_id)
        else:
            tombstones.pop(todo_id)

    # 墓碑裁剪：只保留 TTL 内的，且最多 MAX_TOMBSTONES 条
    prune_before = (now_local() - _dt.timedelta(days=TOMBSTONE_TTL_DAYS)).timestamp()
    kept = [
        tomb for tomb in tombstones.values()
        if iso_rank(tomb["deleted_at"]) >= prune_before
    ]
    kept.sort(key=lambda item: iso_rank(item["deleted_at"]), reverse=True)
    kept = kept[:MAX_TOMBSTONES]

    merged = {
        "schema": SCHEMA_VERSION,
        "version": max(left["version"], right["version"]) + 1,
        "updated_at": to_iso(),
        "todos": sorted(todos.values(), key=lambda item: item["id"]),
        "tombstones": sorted(kept, key=lambda item: item["id"]),
    }
    return merged


def needs_push(local: Any, remote: Any) -> bool:
    """本地是否有远端缺少的内容（决定要不要 PATCH 上云）。"""
    merged = merge_docs(local, remote)
    return canonical(merged) != canonical(remote)


# ---------------------------------------------------------------------------
# 文档变更操作（全部返回新文档，不修改入参）
# ---------------------------------------------------------------------------

def empty_doc() -> Dict[str, Any]:
    return normalize_doc({})


def add_todo(
    doc: Any,
    text: str,
    priority: str = PRIORITY_NORMAL,
    remind_at: str = "",
    remind_date: str = "",
    todo_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """新增一条待办，返回 (新文档, 新条目)。"""
    normalized = normalize_doc(doc)
    now = to_iso()
    todo: Dict[str, Any] = {
        "id": todo_id or new_id(),
        "text": str(text).strip(),
        "priority": priority if priority in (PRIORITY_HIGH, PRIORITY_NORMAL) else PRIORITY_NORMAL,
        "done": False,
        "created_at": now,
        "updated_at": now,
        "remind_at": _normalize_remind_at(remind_at),
        "remind_date": _normalize_remind_date(remind_date),
        "notified_at": "",
        "done_at": "",
    }
    normalized["todos"] = [item for item in normalized["todos"] if item["id"] != todo["id"]]
    normalized["todos"].append(todo)
    normalized["todos"].sort(key=lambda item: item["id"])
    normalized["version"] += 1
    normalized["updated_at"] = now
    return normalized, todo


def update_todo(doc: Any, todo_id: str, **fields: Any) -> Dict[str, Any]:
    """更新一条待办（白名单字段），自动刷新 updated_at。"""
    normalized = normalize_doc(doc)
    allowed = {
        "text", "priority", "done", "remind_at", "remind_date", "notified_at", "done_at",
    }
    for item in normalized["todos"]:
        if item["id"] != todo_id:
            continue
        now = to_iso()
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "priority":
                item[key] = value if value in (PRIORITY_HIGH, PRIORITY_NORMAL) else PRIORITY_NORMAL
            elif key == "text":
                cleaned = str(value or "").strip()
                if cleaned:
                    item[key] = cleaned
            elif key in ("done",):
                item[key] = bool(value)
                item["done_at"] = now if value else ""
                if not value:
                    item["notified_at"] = ""
            elif key in ("remind_at",):
                item[key] = _normalize_remind_at(value)
            elif key in ("remind_date",):
                item[key] = _normalize_remind_date(value)
            elif key in ("notified_at", "done_at"):
                item[key] = _normalize_iso(value, "") if value else ""
        item["updated_at"] = now
        normalized["version"] += 1
        normalized["updated_at"] = now
        break
    return normalized


def set_done(doc: Any, todo_id: str, done: bool = True) -> Dict[str, Any]:
    return update_todo(doc, todo_id, done=done)


def mark_notified(doc: Any, todo_id: str, when: Optional[str] = None) -> Dict[str, Any]:
    return update_todo(doc, todo_id, notified_at=when or to_iso())


def snooze_todo(doc: Any, todo_id: str, minutes: int = 10) -> Dict[str, Any]:
    """稍后提醒：把提醒时间改到 now + minutes，并清空 notified_at。"""
    target = now_local() + _dt.timedelta(minutes=max(1, int(minutes)))
    normalized = normalize_doc(doc)
    for item in normalized["todos"]:
        if item["id"] != todo_id:
            continue
        item["remind_at"] = target.strftime("%H:%M")
        item["remind_date"] = target.strftime("%Y-%m-%d")
        item["notified_at"] = ""
        item["updated_at"] = to_iso()
        normalized["version"] += 1
        normalized["updated_at"] = to_iso()
        break
    return normalized


def delete_todo(doc: Any, todo_id: str) -> Dict[str, Any]:
    """删除待办：移除条目并写入墓碑（同步到其他端后不会被旧数据复活）。"""
    normalized = normalize_doc(doc)
    now = to_iso()
    normalized["todos"] = [item for item in normalized["todos"] if item["id"] != todo_id]
    normalized["tombstones"] = [
        item for item in normalized["tombstones"] if item["id"] != todo_id
    ]
    normalized["tombstones"].append({"id": todo_id, "deleted_at": now})
    normalized["version"] += 1
    normalized["updated_at"] = now
    return normalized


def pending_todos(doc: Any) -> List[Dict[str, Any]]:
    """未完成待办：紧急优先 -> 更早的提醒时间 -> 更早创建。"""
    items = [item for item in normalize_doc(doc)["todos"] if not item["done"]]

    def sort_key(item: Dict[str, Any]) -> Tuple[int, float, float]:
        target = reminder_datetime(item)
        return (
            0 if item["priority"] == PRIORITY_HIGH else 1,
            target.timestamp() if target else float("inf"),
            iso_rank(item["created_at"]),
        )

    return sorted(items, key=sort_key)


def next_todo(doc: Any) -> Optional[Dict[str, Any]]:
    """“下一项待办”：按手机端上传顺序（创建时间）取最老的一条未完成。

    手机端添加待办时是按顺序上传的，所以任务栏应该稳定显示“接下来该做的那一项”，
    而不是在待办之间随机轮播；完成当前项后自动顺延到下一项。
    """
    items = [item for item in normalize_doc(doc)["todos"] if not item["done"]]
    if not items:
        return None
    return min(
        items,
        key=lambda item: (
            iso_rank(item["created_at"]),
            0 if item["priority"] == PRIORITY_HIGH else 1,
            item["id"],
        ),
    )


# ---------------------------------------------------------------------------
# 提醒调度
# ---------------------------------------------------------------------------

def reminder_datetime(
    todo: Dict[str, Any], now: Optional[_dt.datetime] = None
) -> Optional[_dt.datetime]:
    """计算一条待办的提醒时刻（本地时区）。

    旧数据没有 ``remind_date`` 时，按“创建当天”理解（与 v1 行为一致）。
    """
    remind_at = _normalize_remind_at(todo.get("remind_at"))
    if not remind_at:
        return None
    hour, minute = (int(part) for part in remind_at.split(":"))

    remind_date = _normalize_remind_date(todo.get("remind_date"))
    if remind_date:
        try:
            day = _dt.date.fromisoformat(remind_date)
        except ValueError:
            day = (now or now_local()).date()
    else:
        anchor = parse_iso(todo.get("created_at")) or (now or now_local())
        day = anchor.date()

    naive = _dt.datetime.combine(day, _dt.time(hour, minute))
    return naive.astimezone()


def reminder_delta_seconds(
    todo: Dict[str, Any], now: Optional[_dt.datetime] = None
) -> Optional[float]:
    """距提醒时刻的秒数（负数=已过点）；没有提醒返回 None。"""
    target = reminder_datetime(todo, now)
    if target is None:
        return None
    return (( now or now_local()) - target).total_seconds()


def due_reminder(todo: Dict[str, Any], now: Optional[_dt.datetime] = None) -> bool:
    """是否需要在“现在”弹出提醒。

    条件：未完成、未提醒过、有提醒时间、已到点、且离到点不超过 24h（补提醒窗口）。
    """
    if todo.get("done") or todo.get("notified_at"):
        return False
    delta = reminder_delta_seconds(todo, now)
    if delta is None:
        return False
    return 0 <= delta <= REMIND_GRACE_SECONDS


def upcoming_reminders(
    doc: Any, now: Optional[_dt.datetime] = None
) -> List[Dict[str, Any]]:
    """当前到期需要提醒的待办（按到期时间先后）。"""
    moment = now or now_local()
    due = [item for item in pending_todos(doc) if due_reminder(item, moment)]
    due.sort(key=lambda item: iso_rank(reminder_datetime(item).isoformat()))
    return due


def format_reminder(todo: Dict[str, Any], now: Optional[_dt.datetime] = None) -> str:
    """给 UI 用的提醒标签：今天 15:00 / 明天 09:00 / 10-20 09:00。"""
    target = reminder_datetime(todo, now)
    if target is None:
        return ""
    today = (now or now_local()).date()
    if target.date() == today:
        prefix = "今天"
    elif target.date() == today + _dt.timedelta(days=1):
        prefix = "明天"
    else:
        prefix = target.strftime("%m-%d")
    return f"{prefix} {target.strftime('%H:%M')}"


def format_todo_line(
    todo: Dict[str, Any], pending_count: Optional[int] = None, lead: str = "待办"
) -> str:
    """任务栏副行展示用的单行待办文案。"""
    prefix = "🔥" if todo.get("priority") == PRIORITY_HIGH else "📋"
    count = f"({pending_count})" if pending_count is not None else ""
    text = str(todo.get("text") or "").strip()
    line = f"{prefix} {lead}{count}：{text}"
    label = format_reminder(todo)
    if label:
        line += f"  ⏰{label}"
    if todo.get("notified_at"):
        line += "  ✅已提醒"
    return line


def format_next_todo_line(
    todo: Dict[str, Any], pending_count: Optional[int] = None
) -> str:
    """任务栏上“下一项待办”的文案。"""
    return format_todo_line(todo, pending_count, lead="下一项")


# ---------------------------------------------------------------------------
# LRC 歌词
# ---------------------------------------------------------------------------

_LRC_TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)


def parse_lrc(lrc_text: Any) -> List[Tuple[float, str]]:
    """LRC -> [(秒, 歌词行)]，已排序；支持一行多个时间戳与 [offset:] 标签。"""
    if not lrc_text or not isinstance(lrc_text, str):
        return []

    offset_seconds = 0.0
    lines: List[Tuple[float, str]] = []
    seen: set = set()

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        offset_match = _LRC_OFFSET_RE.search(line)
        if offset_match:
            offset_seconds = int(offset_match.group(1)) / 1000.0
            line = _LRC_OFFSET_RE.sub("", line).strip()
            if not line:
                continue

        stamps = list(_LRC_TS_RE.finditer(line))
        if not stamps:
            continue
        content = line[stamps[-1].end():].strip()
        if not content:
            continue
        for match in stamps:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            frac = match.group(3) or "0"
            millis = int(frac.ljust(3, "0")[:3])
            timestamp = round(minutes * 60 + seconds + millis / 1000.0 + offset_seconds, 3)
            key = (timestamp, content)
            if key in seen:
                continue
            seen.add(key)
            lines.append((timestamp, content))

    lines.sort(key=lambda item: item[0])
    return lines


def lyric_index(lyrics: Sequence[Tuple[float, str]], position: float) -> int:
    """二分查找当前时间对应的歌词行下标；早于第一行返回 -1。"""
    if not lyrics:
        return -1
    times = [item[0] for item in lyrics]
    return bisect.bisect_right(times, max(0.0, float(position))) - 1


def current_lyric(lyrics: Sequence[Tuple[float, str]], position: float) -> str:
    index = lyric_index(lyrics, position)
    return lyrics[index][1] if index >= 0 else ""


# ---------------------------------------------------------------------------
# 网易云客户端
# ---------------------------------------------------------------------------

#: 纯“水评”/无信息量的评论，整条匹配到就丢弃
_BORING_COMMENT_RE = re.compile(
    r"^(?:好听|太好听了?|好听到哭|打卡|签到|前排|沙发|板凳|地板|留名|火钳刘明|"
    r"666+|爱了爱了|支持|收藏|经典|循环|单曲循环|网易云|yyds|YYDS|泪目|破防|"
    r"路过|挤挤|第一|第二|来了|晚安|早安|回忆杀|爷青回|开听|播放|评论|"
    r"[!！。.~～?？·、，,\s0-9]+)+$",
    re.IGNORECASE,
)
#: 广告/引流/联系方式类评论
_COMMENT_AD_RE = re.compile(
    r"(https?://|www\.|\.com\b|\.cn\b|公众号|加微信|加v|加V|私信|QQ群|qq群|群号|"
    r"代打|代练|刷单|刷赞|招聘|兼职|优惠|折扣|领券|广告|互粉|回赞|关注我|"
    r"看我主页|进群|福利|链接|下载地址)",
    re.IGNORECASE,
)
_EMOJI_ONLY_RE = re.compile(
    r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B00-\u2BFF\s]+$"
)
#: 越像“讲了一句话/一个故事”的词，权重越高
_STORY_HINTS = (
    "那年", "当时", "后来", "想起", "记得", "突然", "终于", "如果", "希望",
    "毕业", "高考", "大学", "高中", "初中", "暗恋", "初恋", "分手", "异地",
    "妈妈", "爸爸", "奶奶", "爷爷", "外婆", "外公", "朋友", "兄弟", "姐妹",
    "深夜", "凌晨", "半夜", "耳机", "车站", "火车", "同桌", "老师", "故事",
    "笑着", "哭了", "眼泪", "青春", "遗憾", "错过", "重逢", "等待", "想念",
    "孤单", "难过", "开心", "幸福", "人生", "生活", "长大", "小时候", "夏天",
)
INTERESTING_MIN_LEN = 8
INTERESTING_MAX_LEN = 90


def score_interesting_comment(content: str, liked: int = 0) -> float:
    """给一条评论打“有意思”分；返回负数表示不值得展示（水评/广告/纯表情）。"""
    text = " ".join(str(content or "").split())
    length = len(text)
    if length < INTERESTING_MIN_LEN or length > 160:
        return -1.0
    if _EMOJI_ONLY_RE.match(text) or _BORING_COMMENT_RE.match(text):
        return -1.0
    if _COMMENT_AD_RE.search(text):
        return -1.0
    if len(set(text)) <= 3:
        return -1.0
    score = 0.0
    if length <= INTERESTING_MAX_LEN:
        score += 2.0
    else:
        score += 0.5
    score += min(4, sum(1 for hint in _STORY_HINTS if hint in text)) * 1.5
    if any(mark in text for mark in "，。！？…"):
        score += 0.8
    if text.count("。") + text.count("，") >= 2:
        score += 0.5
    score += min(4.0, (max(0, int(liked)) + 1) ** 0.35)
    return score


def select_interesting_comment_items(
    items: Any, limit: int = 8
) -> List[Dict[str, Any]]:
    """从 hotComments 里挑“有故事/有情绪”的评论，过滤水评、广告与纯表情。"""
    scored = []
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split())
        if not content:
            continue
        try:
            liked = int(item.get("likedCount") or 0)
        except (TypeError, ValueError):
            liked = 0
        score = score_interesting_comment(content, liked)
        if score < 0:
            continue
        scored.append((score, -liked, index, item))
    scored.sort(key=lambda row: (row[0], row[1], -row[2]), reverse=True)

    selected: List[Dict[str, Any]] = []
    seen: set = set()
    for _score, _neg_liked, _index, item in scored:
        key = " ".join(str(item.get("content") or "").split())[:12]
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


class NetEaseClient:
    """网易云非官方接口客户端：搜歌、LRC 歌词、高赞热评。

    设计要点：
    - 只缓存**成功**结果（30 分钟），失败不缓存，避免一次网络抖动导致整首歌都没歌词；
    - 所有网络异常都被捕获并记录，由 UI 决定是否显示“歌词获取失败”；
    - 非官方接口可能随时变更，因此热评/歌词都是“可选增强”，失败不能影响核心待办功能。
    """

    SEARCH_URL = "https://music.163.com/api/search/get/web"
    LYRIC_URL = "https://music.163.com/api/song/lyric"
    COMMENT_URL = "https://music.163.com/api/v1/resource/comments/R_SO_4_{song_id}"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: float = 5.0,
        comment_limit: int = 40,
        comment_show: int = 10,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self.timeout = timeout
        self.comment_limit = comment_limit
        self.comment_show = comment_show
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- 对外 -------------------------------------------------------------
    def fetch_song_bundle(self, title: str, artist: str = "") -> Dict[str, Any]:
        """根据“歌名 + 歌手”获取歌词与热评；返回带 ``ok`` 标记的字典。"""
        query = f"{title} {artist}".strip()
        result: Dict[str, Any] = {
            "ok": False,
            "query": query,
            "song_id": None,
            "lyrics": [],
            "hot_comments": [],
            "error": "",
        }
        if not query:
            result["error"] = "空查询"
            return result

        cached = self._get_cache(query)
        if cached is not None:
            return cached

        try:
            song_id = self._search_song_id(query)
            if not song_id:
                result["error"] = "未在网易云找到匹配歌曲"
                return result
            result["song_id"] = song_id
            result["lyrics"] = self._fetch_lyrics(song_id)
            result["hot_comments"] = self._fetch_hot_comments(song_id)
            result["ok"] = True
            self._set_cache(query, result)
        except Exception as exc:  # noqa: BLE001 - 网络/接口问题统一降级
            result["error"] = f"{type(exc).__name__}: {exc}"
            LOGGER.warning("网易云数据获取失败（%s）：%s", query, exc)
        return result

    def probe_song(self, query: str) -> Optional[int]:
        """只做搜歌（设置界面的“测试网易云连通性”用）。"""
        return self._search_song_id(query)

    # -- 内部 -------------------------------------------------------------
    def _get_cache(self, query: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._cache.get(query)
            if not entry:
                return None
            if time.monotonic() - entry["ts"] > NETEASE_CACHE_TTL:
                self._cache.pop(query, None)
                return None
            return entry["data"]

    def _set_cache(self, query: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._cache[query] = {"ts": time.monotonic(), "data": data}

    def _search_song_id(self, query: str) -> Optional[int]:
        response = self.session.post(
            self.SEARCH_URL,
            data={"s": query, "type": 1, "offset": 0, "total": "true", "limit": 3},
            timeout=self.timeout,
        )
        payload = response.json()
        songs = (payload.get("result") or {}).get("songs") or []
        if not songs:
            return None
        try:
            return int(songs[0]["id"])
        except (KeyError, TypeError, ValueError):
            return None

    def _fetch_lyrics(self, song_id: int) -> List[Tuple[float, str]]:
        response = self.session.get(
            self.LYRIC_URL,
            params={"os": "pc", "id": song_id, "lv": -1, "tv": -1, "kv": -1},
            timeout=self.timeout,
        )
        payload = response.json()
        raw_lrc = ((payload.get("lrc") or {}).get("lyric") or "")
        return parse_lrc(raw_lrc)

    def _fetch_hot_comments(self, song_id: int) -> List[str]:
        response = self.session.get(
            self.COMMENT_URL.format(song_id=song_id),
            params={"limit": self.comment_limit, "offset": 0},
            timeout=self.timeout,
        )
        payload = response.json()
        pool = payload.get("hotComments") or []
        if not pool:
            comments = payload.get("comments") or []
            pool = sorted(
                comments,
                key=lambda item: int(item.get("likedCount") or 0),
                reverse=True,
            )

        # 只挑“有意思”的：过滤水评、广告、纯表情，偏好有故事/有情绪的短评
        selected = select_interesting_comment_items(pool, self.comment_show)
        lines = [line for line in (self.format_comment(item) for item in selected) if line]

        # 好评论太少时，退回到“点赞最高”的评论，至少保证有内容可轮播
        if len(lines) < 3:
            for item in pool:
                line = self.format_comment(item, max_len=58)
                if line and line not in lines:
                    lines.append(line)
                if len(lines) >= self.comment_show:
                    break
        return lines[: self.comment_show]

    @staticmethod
    def _format_likes(liked: int) -> str:
        if liked >= 10000:
            return f"{liked / 10000:.1f}万".replace(".0万", "万")
        return str(liked)

    @staticmethod
    def format_comment(item: Any, max_len: int = 52) -> Optional[str]:
        """把一条评论格式化成任务栏可展示的单行文本（短、带点赞数）。"""
        if not isinstance(item, dict):
            return None
        content = " ".join(str(item.get("content") or "").split())
        if not content:
            return None
        if len(content) > max_len:
            content = content[: max_len - 1] + "…"
        try:
            liked = int(item.get("likedCount") or 0)
        except (TypeError, ValueError):
            liked = 0
        nickname = str(((item.get("user") or {}).get("nickname")) or "云村村民").strip()
        if len(nickname) > 12:
            nickname = nickname[:11] + "…"
        likes = f" 👍{NetEaseClient._format_likes(liked)}" if liked else ""
        return f"「{content}」 @{nickname}{likes}"


# ---------------------------------------------------------------------------
# GitHub Gist 客户端
# ---------------------------------------------------------------------------

class GitHubApiError(RuntimeError):
    """带 HTTP 状态码的 GitHub API 错误，UI 可据此给出准确提示。"""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class GistFetch:
    """一次 GET 的结果：ok=拿到文档；not_modified=304 未变化。"""

    status: str
    doc: Dict[str, Any]
    etag: Optional[str]


@dataclass
class SyncReport:
    """一次同步的完整结果，供 UI 记录状态并渲染。"""

    status: str
    doc: Dict[str, Any]
    remote: Dict[str, Any]
    pushed: bool = False
    etag: Optional[str] = None
    error: str = ""


class GitHubGistStore:
    """GitHub Gist 作为免费云端 JSON 数据库的最小客户端。

    同步协议：``GET（ETag 条件请求） → merge(local, remote) → 有本地新内容才 PATCH``。
    Gist API 没有 If-Match 之类的原子写，所以两端并发时靠“按条目合并”收敛：
    即使一次 PATCH 覆盖了别人的改动，对方下一轮同步也会把本地内容合并回去。
    """

    def __init__(
        self,
        token: str,
        gist_id: str = "",
        timeout: float = 8.0,
        session: Optional[requests.Session] = None,
        user_agent: str = "Win11TaskbarLyrics/1.1",
    ) -> None:
        self.token = (token or "").strip()
        self.gist_id = (gist_id or "").strip()
        self.timeout = timeout
        self.session = session or requests.Session()
        self.user_agent = user_agent
        self._last_remote: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # -- 基础 -------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.token and self.gist_id)

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.user_agent,
        }
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 401:
            raise GitHubApiError("GitHub Token 无效或已过期（401），请重新配置", status)
        if status == 403:
            raise GitHubApiError("GitHub 拒绝访问（403，通常是限流或无 gist 权限）", status)
        if status == 404:
            raise GitHubApiError("Gist 不存在（404），请检查 Gist ID", status)
        raise GitHubApiError(f"GitHub API 请求失败（HTTP {status}）", status)

    # -- 读写 -------------------------------------------------------------
    def get_doc(self, etag: Optional[str] = None) -> GistFetch:
        """读取云端文档；带 etag 时若未变化返回 304（不计入限流额度）。"""
        if not self.configured:
            raise GitHubApiError("尚未配置 GitHub Token / Gist ID")
        extra = {"If-None-Match": etag} if etag else None
        response = self.session.get(
            f"{API_BASE}/gists/{self.gist_id}",
            headers=self._headers(extra),
            timeout=self.timeout,
        )
        if response.status_code == 304:
            return GistFetch("not_modified", empty_doc(), etag)
        self._raise_for_status(response)
        payload = response.json()
        files = payload.get("files") or {}
        entry = files.get(GIST_FILENAME)
        if not entry:
            raise GitHubApiError(f"Gist 中缺少 {GIST_FILENAME} 文件")
        if entry.get("truncated"):
            raise GitHubApiError("Gist 文件内容被截断（超过 1MB），请清理待办数据")
        raw_content = entry.get("content") or "{}"
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise GitHubApiError(f"Gist 中的 JSON 解析失败：{exc}") from exc
        doc = normalize_doc(parsed)
        with self._lock:
            self._last_remote = doc
        return GistFetch("ok", doc, response.headers.get("ETag"))

    def put_doc(self, doc: Any) -> Optional[str]:
        """覆盖写入云端文档（调用方应先 GET + merge），返回新的 ETag。"""
        if not self.configured:
            raise GitHubApiError("尚未配置 GitHub Token / Gist ID")
        normalized = normalize_doc(doc)
        body = {
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(normalized, ensure_ascii=False, indent=2)
                }
            }
        }
        response = self.session.patch(
            f"{API_BASE}/gists/{self.gist_id}",
            headers=self._headers({"Content-Type": "application/json"}),
            json=body,
            timeout=self.timeout,
        )
        self._raise_for_status(response)
        with self._lock:
            self._last_remote = normalized
        return response.headers.get("ETag")

    def create_gist(self, doc: Any, description: str = "Win11 Taskbar Lyrics & Todo Sync Data") -> str:
        """创建私密（不公开列表）Gist 并写入初始文档，返回 Gist ID。"""
        if not self.token:
            raise GitHubApiError("尚未配置 GitHub Token")
        body = {
            "description": description,
            "public": False,
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(normalize_doc(doc), ensure_ascii=False, indent=2)
                }
            },
        }
        response = self.session.post(
            f"{API_BASE}/gists",
            headers=self._headers({"Content-Type": "application/json"}),
            json=body,
            timeout=self.timeout,
        )
        self._raise_for_status(response)
        payload = response.json()
        gist_id = str(payload.get("id") or "")
        if not gist_id:
            raise GitHubApiError("创建 Gist 成功但响应中没有 id")
        self.gist_id = gist_id
        return gist_id

    # -- 同步 -------------------------------------------------------------
    def sync(self, local_doc: Any, etag: Optional[str] = None) -> SyncReport:
        """执行一轮“拉取 → 合并 → 按需推送”。网络错误会抛 GitHubApiError / requests 异常。"""
        local = normalize_doc(local_doc)
        fetch = self.get_doc(etag=etag)

        if fetch.status == "not_modified":
            with self._lock:
                remote = self._last_remote
            if remote is None:
                # 手里没有远端缓存（例如冷启动就 304），强制全量拉一次
                fetch = self.get_doc(etag=None)
                remote = fetch.doc
            if needs_push(local, remote):
                merged = merge_docs(local, remote)
                new_etag = self.put_doc(merged)
                return SyncReport("ok", merged, remote, pushed=True, etag=new_etag)
            return SyncReport("not_modified", local, remote, pushed=False, etag=fetch.etag)

        remote = fetch.doc
        merged = merge_docs(local, remote)
        if needs_push(merged, remote):
            new_etag = self.put_doc(merged)
            return SyncReport("ok", merged, remote, pushed=True, etag=new_etag)
        return SyncReport("ok", merged, remote, pushed=False, etag=fetch.etag)


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def setup_logging(log_path: Optional[str] = None, level: int = logging.INFO) -> None:
    """控制台 + 滚动文件日志。重复调用是安全的。"""
    root = logging.getLogger()
    if any(getattr(handler, "_taskbar_managed", False) for handler in root.handlers):
        return
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console._taskbar_managed = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if log_path:
        try:
            from logging.handlers import RotatingFileHandler

            handler = RotatingFileHandler(
                log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            handler._taskbar_managed = True  # type: ignore[attr-defined]
            root.addHandler(handler)
        except OSError:
            LOGGER.warning("无法写入日志文件：%s", log_path)


__all__ = [
    "SCHEMA_VERSION",
    "GIST_FILENAME",
    "PRIORITY_HIGH",
    "PRIORITY_NORMAL",
    "REMIND_GRACE_SECONDS",
    "NetEaseClient",
    "GitHubGistStore",
    "GitHubApiError",
    "GistFetch",
    "SyncReport",
    "now_local",
    "to_iso",
    "parse_iso",
    "iso_rank",
    "new_id",
    "normalize_todo",
    "normalize_doc",
    "canonical",
    "docs_equal",
    "merge_docs",
    "needs_push",
    "empty_doc",
    "add_todo",
    "update_todo",
    "set_done",
    "mark_notified",
    "snooze_todo",
    "delete_todo",
    "pending_todos",
    "next_todo",
    "reminder_datetime",
    "reminder_delta_seconds",
    "due_reminder",
    "upcoming_reminders",
    "format_reminder",
    "format_todo_line",
    "format_next_todo_line",
    "score_interesting_comment",
    "select_interesting_comment_items",
    "INTERESTING_MIN_LEN",
    "INTERESTING_MAX_LEN",
    "parse_lrc",
    "lyric_index",
    "current_lyric",
    "setup_logging",
]
