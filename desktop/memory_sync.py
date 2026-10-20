#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_sync.py — 把「每日记忆」推送到 GitHub 私有仓库。

契约见 ``docs/08-待办模块升级设计.md`` 第 4 节。本模块只依赖标准库 + requests，
不依赖 PyQt6，也不依赖 core（避免桌面端/测试之间的循环依赖）：

    repositories/luongthimydieu/taskbar-memory
    ├── memories/2026-10-20.md      # 每日记忆（Markdown）
    └── memories/index.json         # 索引数组，按日期倒序，最多 400 条

对外接口：

- ``MemoryRepo(token, repo="luongthimydieu/taskbar-memory", branch="main", timeout=30)``
  - ``ensure_repo()``：GET 仓库；404 时 POST /user/repos 创建私有仓库（auto_init）；
  - ``put_file(path, content, message) -> html_url``：GET sha → PUT contents（新建/更新）；
  - ``get_file(path) -> str | None``：读取并 base64 解码文件内容，不存在返回 None。
- ``build_daily_memory(doc, ledger, moment, ai_comment="") -> markdown``
- ``push_daily_memory(config, doc, ledger, moment, ai_comment="") -> dict | None``
  配置键：``memory_repo`` / ``memory_token`` / ``memory_enabled`` /
  ``memory_time``（由桌面端调度）/ ``memory_branch``。

失败统一抛 ``MemorySyncError``（带 ``status`` 属性），桌面端只做托盘提示。
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import logging
import math
import urllib.parse
from typing import Any, Dict, List, Mapping, Optional

import requests

__all__ = [
    "DEFAULT_BRANCH",
    "DEFAULT_REPO",
    "INDEX_PATH",
    "MAX_INDEX_ENTRIES",
    "MemoryRepo",
    "MemorySyncError",
    "build_daily_memory",
    "push_daily_memory",
]

LOGGER = logging.getLogger("taskbar.memory")

API_BASE = "https://api.github.com"
DEFAULT_REPO = "luongthimydieu/taskbar-memory"
DEFAULT_BRANCH = "main"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "Win11TaskbarLyrics/1.3"

MEMORY_DIR = "memories"
INDEX_PATH = f"{MEMORY_DIR}/index.json"
#: index.json 最多保留多少条（设计上限，避免仓库文件无限膨胀）。
MAX_INDEX_ENTRIES = 400
#: 索引条目的展示标题上限，避免单条 markdown 过长。
MAX_MEMORY_ITEMS = 30

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class MemorySyncError(RuntimeError):
    """GitHub 记忆仓库操作失败（配置缺失 / 网络 / API 错误）。"""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        #: 有 HTTP 响应时带上状态码，UI / 测试可以据此判断（无响应为 None）。
        self.status = status


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _one_line(value: Any) -> str:
    """把用户输入压成一行，避免破坏 Markdown 结构。"""
    return " ".join(_as_text(value).split())


def _coerce_day(value: Any) -> Optional[_dt.date]:
    """date / datetime / ISO 日期字符串 -> date；无法识别返回 None。"""
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


def _moment_day(moment: Any) -> _dt.date:
    day = _coerce_day(moment)
    return day or _dt.datetime.now().astimezone().date()


def _number(value: Any) -> float:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return amount if math.isfinite(amount) else 0.0


def _money(value: Any) -> str:
    """与 core.format_money 一致的金额格式：``¥12.50`` / ``-¥3.00``。"""
    amount = _number(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}¥{abs(amount):.2f}"


def _todo_meta(todo: Mapping[str, Any]) -> List[str]:
    meta: List[str] = []
    due_date = _as_text(todo.get("due_date"))
    if due_date:
        meta.append(f"目标 {due_date}")
    try:
        minutes = int(todo.get("estimate_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes > 0:
        meta.append(f"预计 {minutes} 分钟")
    return meta


def _todo_line(todo: Mapping[str, Any], prefix: str = "") -> str:
    text = _one_line(todo.get("text")) or "（无内容）"
    meta = _todo_meta(todo)
    suffix = f"（{'，'.join(meta)}）" if meta else ""
    return f"{prefix}{text}{suffix}"


def _entry_kind(entry: Mapping[str, Any]) -> str:
    return "收入" if _as_text(entry.get("type")).lower() == "income" else "支出"


# ---------------------------------------------------------------------------
# GitHub Contents API 客户端
# ---------------------------------------------------------------------------

class MemoryRepo:
    """GitHub 仓库 Contents API 的最小客户端，只做「读写单个文件」。

    同一个 Token 下重复调用 ``ensure_repo`` 是安全的：仓库存在时只发一个 GET；
    仓库不存在时自动创建私有仓库（``auto_init=True``，这样 main 分支立即可写）。
    """

    def __init__(
        self,
        token: str,
        repo: str = DEFAULT_REPO,
        branch: str = DEFAULT_BRANCH,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.token = _as_text(token)
        self.repo = _as_text(repo) or DEFAULT_REPO
        self.branch = _as_text(branch) or DEFAULT_BRANCH
        self.timeout = timeout
        #: 测试时可注入假 session，避免真实网络请求
        self.session = session or requests.Session()
        self.user_agent = user_agent

    # -- 基础 -------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.token and self._split_repo(silent=True))

    def _split_repo(self, silent: bool = False) -> Optional[List[str]]:
        parts = [part.strip() for part in self.repo.split("/")]
        if len(parts) == 2 and parts[0] and parts[1] and "/" not in parts[1]:
            return parts
        if silent:
            return None
        raise MemorySyncError(f"memory_repo 需要 owner/repo 格式：{self.repo!r}")

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

    @property
    def _repo_url(self) -> str:
        return f"{API_BASE}/repos/{self.repo}"

    def _contents_url(self, path: str) -> str:
        clean = _as_text(path).lstrip("/")
        if not clean:
            raise MemorySyncError("文件路径不能为空")
        quoted = urllib.parse.quote(clean, safe="/")
        return f"{API_BASE}/repos/{self.repo}/contents/{quoted}"

    def _require_token(self) -> None:
        if not self.token:
            raise MemorySyncError("尚未配置 memory_token，无法访问记忆仓库")

    def _raise_for(self, response: Any, action: str) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 400:
            return
        if status == 401:
            message = "GitHub Token 无效或已过期（401），请重新配置 memory_token"
        elif status == 403:
            message = "GitHub 拒绝访问（403，Token 可能缺少 repo 写权限）"
        elif status == 404:
            message = f"GitHub 仓库或文件不存在（404）：{self.repo}"
        elif status in (409, 422):
            message = f"GitHub 写入冲突（HTTP {status}），请稍后重试"
        else:
            message = f"GitHub {action}失败（HTTP {status}）"
        raise MemorySyncError(message, status)

    @staticmethod
    def _json(response: Any, action: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise MemorySyncError(f"GitHub {action}响应不是合法 JSON") from exc

    # -- 仓库 -------------------------------------------------------------
    def ensure_repo(self) -> Dict[str, Any]:
        """确保仓库存在并返回仓库信息；不存在时自动创建私有仓库。"""
        self._require_token()
        owner, name = self._split_repo()  # type: ignore[misc]
        response = self.session.get(
            self._repo_url, headers=self._headers(), timeout=self.timeout
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 404:
            self._raise_for(response, "读取记忆仓库")
            payload = self._json(response, "读取记忆仓库")
            return payload if isinstance(payload, dict) else {}

        body = {
            "name": name,
            "private": True,
            "auto_init": True,
            "description": "Win11 任务栏助手 · 待办 / 记账 / 每日记忆",
        }
        created = self.session.post(
            f"{API_BASE}/user/repos",
            headers=self._headers({"Content-Type": "application/json"}),
            json=body,
            timeout=self.timeout,
        )
        self._raise_for(created, "创建记忆仓库")
        payload = self._json(created, "创建记忆仓库")
        repo_info = payload if isinstance(payload, dict) else {}
        full_name = _as_text(repo_info.get("full_name"))
        if full_name and full_name.lower() != self.repo.lower():
            # 仓库属于别的账号时，POST /user/repos 会建到 Token 自己名下 —— 必须提示，
            # 否则记忆会悄悄写到错误的地方。
            raise MemorySyncError(
                f"仓库 {self.repo} 不存在，已按 Token 账号创建 {full_name}；"
                "请检查 memory_repo 或更换 Token"
            )
        LOGGER.info("已创建私有记忆仓库：%s", full_name or f"{owner}/{name}")
        return repo_info

    # -- 文件 -------------------------------------------------------------
    def _get_entry(self, path: str) -> Optional[Dict[str, Any]]:
        """GET Contents API，返回文件条目；文件不存在（404）返回 None。"""
        self._require_token()
        response = self.session.get(
            self._contents_url(path),
            headers=self._headers(),
            params={"ref": self.branch},
            timeout=self.timeout,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 404:
            return None
        self._raise_for(response, f"读取 {path}")
        payload = self._json(response, f"读取 {path}")
        if isinstance(payload, list):
            raise MemorySyncError(f"{path} 是目录而不是文件")
        if not isinstance(payload, dict):
            raise MemorySyncError(f"{path} 的 GitHub 响应结构异常")
        return payload

    def get_file(self, path: str) -> Optional[str]:
        """读取文件内容（UTF-8）；文件不存在返回 None。"""
        entry = self._get_entry(path)
        if entry is None:
            return None
        content = entry.get("content")
        encoding = _as_text(entry.get("encoding")) or "base64"
        if encoding != "base64" or not isinstance(content, str):
            raise MemorySyncError(f"{path} 的内容不是可解码的 base64（encoding={encoding!r}）")
        try:
            raw = base64.b64decode(content)
        except (binascii.Error, ValueError) as exc:
            raise MemorySyncError(f"{path} 的 base64 内容损坏") from exc
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MemorySyncError(f"{path} 不是 UTF-8 文本") from exc

    def put_file(self, path: str, content: str, message: str = "") -> str:
        """新建或更新文件：先 GET 现有 sha，再 PUT contents。返回 html_url。

        Contents API 更新文件必须带 ``sha``，否则会 422；这里每次写入前都查一次，
        每日只写 2 个文件，这点额外请求可以接受。
        """
        self._require_token()
        clean_path = _as_text(path).lstrip("/")
        if not clean_path:
            raise MemorySyncError("文件路径不能为空")
        entry = self._get_entry(clean_path)
        body: Dict[str, Any] = {
            "message": _as_text(message) or f"update {clean_path}",
            "content": base64.b64encode((content or "").encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if entry is not None:
            sha = _as_text(entry.get("sha"))
            if sha:
                body["sha"] = sha
        response = self.session.put(
            self._contents_url(clean_path),
            headers=self._headers({"Content-Type": "application/json"}),
            json=body,
            timeout=self.timeout,
        )
        self._raise_for(response, f"写入 {clean_path}")
        payload = self._json(response, f"写入 {clean_path}")
        if isinstance(payload, Mapping):
            file_info = payload.get("content")
            if isinstance(file_info, Mapping):
                return _as_text(file_info.get("html_url"))
        return ""


# ---------------------------------------------------------------------------
# 每日记忆 Markdown
# ---------------------------------------------------------------------------

def _todo_day(todo: Mapping[str, Any]) -> Optional[_dt.date]:
    """创建日期（用于「没有 due_date 的待办算不算今天」）。"""
    return _coerce_day(todo.get("created_at"))


def build_daily_memory(
    doc: Any, ledger: Any, moment: Any, ai_comment: Any = ""
) -> str:
    """把当天的完成 / 未完成 / 账单 / AI 评价渲染成 Markdown 记忆。

    ``doc`` / ``ledger`` 直接使用 ``core`` 的文档字典（也容忍缺失字段与 None）。
    """
    day = _moment_day(moment)
    if isinstance(moment, _dt.datetime):
        generated_at = moment.strftime("%Y-%m-%d %H:%M")
    else:
        generated_at = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    doc_data = doc if isinstance(doc, Mapping) else {}
    ledger_data = ledger if isinstance(ledger, Mapping) else {}
    todos: List[Mapping[str, Any]] = [
        item for item in doc_data.get("todos") or [] if isinstance(item, Mapping)
    ]
    entries: List[Mapping[str, Any]] = [
        item for item in ledger_data.get("entries") or [] if isinstance(item, Mapping)
    ]

    completed: List[Mapping[str, Any]] = []
    pending_today: List[Mapping[str, Any]] = []
    overdue: List[Any] = []
    for todo in todos:
        due_date = _coerce_day(todo.get("due_date"))
        if todo.get("done"):
            done_day = _coerce_day(todo.get("done_at"))
            if done_day == day or (done_day is None and due_date == day):
                completed.append(todo)
            continue
        if due_date is None:
            if _todo_day(todo) == day:
                pending_today.append(todo)
        elif due_date == day:
            pending_today.append(todo)
        elif due_date < day:
            overdue.append((todo, (day - due_date).days))

    completed = completed[:MAX_MEMORY_ITEMS]
    pending_today = pending_today[:MAX_MEMORY_ITEMS]
    overdue = overdue[:MAX_MEMORY_ITEMS]

    today_entries = [
        entry for entry in entries if _as_text(entry.get("date"))[:10] == day.isoformat()
    ]
    income = sum(
        _number(entry.get("amount"))
        for entry in today_entries
        if _as_text(entry.get("type")).lower() == "income"
    )
    expense = sum(
        _number(entry.get("amount"))
        for entry in today_entries
        if _as_text(entry.get("type")).lower() != "income"
    )

    lines: List[str] = [
        f"# 每日记忆 · {day.isoformat()}（{_WEEKDAYS[day.weekday()]}）",
        "",
        f"> 生成时间：{generated_at}",
        "",
        f"## 今日完成（{len(completed)}）",
    ]
    if completed:
        lines.extend(_todo_line(todo, "✅ ") for todo in completed)
    else:
        lines.append("- 今天没有完成记录，明天继续 💪")
    lines.append("")

    remaining = len(pending_today) + len(overdue)
    lines.append(f"## 未完成 / 逾期（{remaining}）")
    if pending_today:
        lines.extend(
            f"- ⏳ {_todo_line(todo)}" for todo in pending_today
        )
    for todo, overdue_days in overdue:
        lines.append(f"- ⚠️ {_todo_line(todo)}，逾期 {overdue_days} 天")
    if remaining == 0:
        lines.append("- 今天的待办都清空了 🎉")
    lines.append("")

    lines.append(f"## 今日账单（{len(today_entries)} 笔）")
    if today_entries:
        lines.append(
            f"- 收入 {_money(income)} · 支出 {_money(expense)} · 结余 {_money(income - expense)}"
        )
        for entry in today_entries[:MAX_MEMORY_ITEMS]:
            note = _one_line(entry.get("note"))
            category = _one_line(entry.get("category")) or "未分类"
            time_text = _one_line(entry.get("time"))
            detail = (
                f"- {time_text + ' ' if time_text else ''}"
                f"{_entry_kind(entry)} · {category} {_money(entry.get('amount'))}"
            )
            if note:
                detail += f"（{note}）"
            lines.append(detail)
    else:
        lines.append("- 今天没有记账")
    lines.append("")

    lines.append("## AI 评价")
    comment = _as_text(ai_comment)
    lines.append(comment if comment else "（今日暂无 AI 评价）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 推送
# ---------------------------------------------------------------------------

def _normalize_index_entry(item: Any) -> Optional[Dict[str, str]]:
    """把 index.json 里的旧条目统一成 ``{"date","file","url"}``；非法条目返回 None。"""
    if isinstance(item, Mapping):
        date_text = _as_text(item.get("date"))[:10]
        file_text = _as_text(item.get("file"))
        url_text = _as_text(item.get("url"))
    elif isinstance(item, str):
        text = item.strip()
        if text.startswith(MEMORY_DIR + "/"):
            text = text[len(MEMORY_DIR) + 1 :]
        if text.endswith(".md"):
            text = text[: -len(".md")]
        date_text = text[:10]
        file_text = ""
        url_text = ""
    else:
        return None
    if _coerce_day(date_text) is None:
        return None
    return {
        "date": date_text,
        "file": file_text or f"{MEMORY_DIR}/{date_text}.md",
        "url": url_text,
    }


def _merge_index(raw: Optional[str], new_entry: Mapping[str, Any]) -> List[Dict[str, str]]:
    """合并 index.json：同日去重、按日期倒序、最多 ``MAX_INDEX_ENTRIES`` 条。"""
    items: List[Dict[str, str]] = []
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            LOGGER.warning("memories/index.json 解析失败，将按空索引重建")
            parsed = []
        if isinstance(parsed, list):
            for item in parsed:
                normalized = _normalize_index_entry(item)
                if normalized:
                    items.append(normalized)
        else:
            LOGGER.warning("memories/index.json 不是数组，将按空索引重建")

    date_text = _as_text(new_entry.get("date"))[:10]
    if _coerce_day(date_text) is None:
        raise MemorySyncError(f"索引条目日期非法：{new_entry.get('date')!r}")
    file_text = _as_text(new_entry.get("file")) or f"{MEMORY_DIR}/{date_text}.md"
    url_text = _as_text(new_entry.get("url"))

    merged = [item for item in items if item.get("date") != date_text]
    merged.append({"date": date_text, "file": file_text, "url": url_text})
    merged.sort(key=lambda item: item.get("date") or "", reverse=True)
    return merged[:MAX_INDEX_ENTRIES]


def push_daily_memory(
    config: Mapping[str, Any],
    doc: Any,
    ledger: Any,
    moment: Any,
    ai_comment: Any = "",
) -> Optional[Dict[str, Any]]:
    """推送今日记忆：``memories/YYYY-MM-DD.md`` + 更新 ``memories/index.json``。

    返回 ``{"date", "path", "markdown_url", "index_path", "index_url",
    "index_count"}``；``memory_enabled=False`` 时直接返回 None。Token 缺失 /
    网络 / API 失败抛 ``MemorySyncError``。
    """
    config = config if isinstance(config, Mapping) else {}
    if not bool(config.get("memory_enabled", True)):
        return None
    token = _as_text(config.get("memory_token"))
    if not token:
        raise MemorySyncError("尚未配置记忆仓库 Token（memory_token）")
    repo_name = _as_text(config.get("memory_repo")) or DEFAULT_REPO
    branch = _as_text(config.get("memory_branch")) or DEFAULT_BRANCH

    day = _moment_day(moment)
    date_text = day.isoformat()
    repo = MemoryRepo(token=token, repo=repo_name, branch=branch)
    repo.ensure_repo()

    markdown = build_daily_memory(doc, ledger, moment, ai_comment)
    memory_path = f"{MEMORY_DIR}/{date_text}.md"
    markdown_url = repo.put_file(memory_path, markdown, f"memory: {date_text}")

    existing = repo.get_file(INDEX_PATH)
    entries = _merge_index(
        existing,
        {"date": date_text, "file": memory_path, "url": markdown_url},
    )
    index_content = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    index_url = repo.put_file(INDEX_PATH, index_content, f"index: add {date_text}")

    return {
        "date": date_text,
        "path": memory_path,
        "markdown_url": markdown_url,
        "index_path": INDEX_PATH,
        "index_url": index_url,
        "index_count": len(entries),
    }
