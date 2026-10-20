#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task_archive.py — 完成记录本地归档。

为什么需要它：
- 待办完成后可能被删除（手机/电脑「清除已完成」），云端 Gist 里就查不到了；
- 每日记忆推送必须包含「当天完成、但后来被删掉」的条目；
- 同时保存点击完成时填写的「完成情况」「心得」。

归档只存本机 ``archive.json``，每日记忆推送时会一并写入 GitHub 仓库的
``memories/YYYY-MM-DD.md`` 与 ``archive/YYYY-MM-DD.json``。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import core

LOGGER = logging.getLogger("taskbar.archive")

ARCHIVE_VERSION = 1
MAX_EVENTS = 5000
KEEP_DAYS = 730


def _norm_iso(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text if core.parse_iso(text) else fallback


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def empty_archive() -> Dict[str, Any]:
    return {"version": ARCHIVE_VERSION, "updated_at": core.to_iso(), "events": []}


def normalize_event(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    text = _norm_text(raw.get("text"))
    if not text:
        return None
    completed_at = _norm_iso(raw.get("completed_at"))
    if not completed_at:
        completed_at = _norm_iso(raw.get("done_at")) or core.to_iso()
    todo_id = _norm_text(raw.get("todo_id") or raw.get("id"))
    return {
        "id": _norm_text(raw.get("id")) or core.new_id("evt"),
        "todo_id": todo_id,
        "text": text,
        "priority": _norm_text(raw.get("priority")) or "normal",
        "parent_id": _norm_text(raw.get("parent_id")),
        "due_date": _norm_text(raw.get("due_date")),
        "created_at": _norm_iso(raw.get("created_at")),
        "completed_at": completed_at,
        "done_note": _norm_text(raw.get("done_note")),
        "done_reflection": _norm_text(raw.get("done_reflection")),
        "source": _norm_text(raw.get("source")) or "desktop",
    }


def normalize_archive(raw: Any) -> Dict[str, Any]:
    archive = empty_archive()
    if not isinstance(raw, Mapping):
        return archive
    events: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw.get("events") or []:
        event = normalize_event(item)
        if event is None:
            continue
        key = (event["todo_id"], event["completed_at"])
        if event["todo_id"] and key in seen:
            continue
        if event["todo_id"]:
            seen.add(key)
        events.append(event)
    events.sort(key=lambda item: (item["completed_at"], item["id"]))
    archive["events"] = events[-MAX_EVENTS:]
    archive["updated_at"] = _norm_iso(raw.get("updated_at")) or core.to_iso()
    return archive


def save_archive(path: Path, archive: Dict[str, Any]) -> bool:
    normalized = normalize_archive(archive)
    normalized["updated_at"] = core.to_iso()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        LOGGER.warning("保存完成归档失败：%s", exc)
        return False


def load_archive(path: Path) -> Dict[str, Any]:
    try:
        if path.is_file():
            return normalize_archive(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取完成归档失败：%s", exc)
    return empty_archive()


def add_event(
    archive: Dict[str, Any],
    todo: Mapping[str, Any],
    completed_at: str = "",
    done_note: str = "",
    done_reflection: str = "",
    source: str = "desktop",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把一条已完成待办写入归档；同一 todo + 完成时间只记一次。"""
    updated = normalize_archive(archive)
    moment = _norm_iso(completed_at) or _norm_iso(todo.get("done_at")) or core.to_iso()
    todo_id = _norm_text(todo.get("id"))
    for existing in updated["events"]:
        if existing["todo_id"] == todo_id and existing["completed_at"] == moment:
            # 已有记录：补全后填的心得/完成情况
            if done_note and not existing["done_note"]:
                existing["done_note"] = _norm_text(done_note)
            if done_reflection and not existing["done_reflection"]:
                existing["done_reflection"] = _norm_text(done_reflection)
            return updated, existing
    event = normalize_event(
        {
            "id": core.new_id("evt"),
            "todo_id": todo_id,
            "text": todo.get("text"),
            "priority": todo.get("priority"),
            "parent_id": todo.get("parent_id"),
            "due_date": todo.get("due_date"),
            "created_at": todo.get("created_at"),
            "completed_at": moment,
            "done_note": done_note or todo.get("done_note"),
            "done_reflection": done_reflection or todo.get("done_reflection"),
            "source": source,
        }
    )
    if event is None:
        return updated, {}
    updated["events"].append(event)
    updated["events"].sort(key=lambda item: (item["completed_at"], item["id"]))
    updated["events"] = updated["events"][-MAX_EVENTS:]
    updated["updated_at"] = core.to_iso()
    return updated, event


def sync_completed_from_doc(
    archive: Dict[str, Any], doc: Mapping[str, Any]
) -> Tuple[Dict[str, Any], int]:
    """把文档里已完成、但归档中没有的待办补进归档（例如手机端完成的任务）。"""
    updated = normalize_archive(archive)
    added = 0
    for todo in doc.get("todos") or []:
        if not isinstance(todo, Mapping) or not todo.get("done"):
            continue
        completed_at = _norm_iso(todo.get("done_at"))
        if not completed_at:
            continue
        before = len(updated["events"])
        updated, _event = add_event(
            updated,
            todo,
            completed_at=completed_at,
            done_note=_norm_text(todo.get("done_note")),
            done_reflection=_norm_text(todo.get("done_reflection")),
            source="sync",
        )
        if len(updated["events"]) > before:
            added += 1
    return updated, added


def events_for_day(archive: Mapping[str, Any], day: Any) -> List[Dict[str, Any]]:
    """某一天（按完成时间 completed_at 的日期）完成的归档事件。"""
    target = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
    result = []
    for event in normalize_archive(archive)["events"]:
        completed = core.parse_iso(event.get("completed_at"))
        if completed and completed.date().isoformat() == target:
            result.append(event)
    result.sort(key=lambda item: item["completed_at"])
    return result


def prune(archive: Dict[str, Any], now: Optional[Any] = None, keep_days: int = KEEP_DAYS) -> Dict[str, Any]:
    """删除过老的归档事件，避免文件无限增长。"""
    updated = normalize_archive(archive)
    moment = now or core.now_local()
    cutoff = moment.timestamp() - max(1, int(keep_days)) * 86400
    kept = []
    for event in updated["events"]:
        completed = core.parse_iso(event.get("completed_at"))
        if completed and completed.timestamp() >= cutoff:
            kept.append(event)
    updated["events"] = kept
    return updated


def archive_payload(
    archive: Mapping[str, Any], day: Any, doc: Mapping[str, Any]
) -> Dict[str, Any]:
    """写出到 GitHub 的 archive/YYYY-MM-DD.json 内容：完成 + 未完成/逾期快照。"""
    target = day if hasattr(day, "isoformat") else None
    day_text = target.isoformat() if target else str(day)[:10]
    completed = events_for_day(archive, day_text)
    unfinished: List[Dict[str, Any]] = []
    overdue: List[Dict[str, Any]] = []
    for todo in doc.get("todos") or []:
        if not isinstance(todo, Mapping) or todo.get("done"):
            continue
        due = core.parse_iso(todo.get("due_date"))
        due_day = due.date() if due else None
        row = {
            "id": todo.get("id"),
            "text": todo.get("text"),
            "due_date": todo.get("due_date"),
            "priority": todo.get("priority"),
        }
        if target and due_day and due_day < target:
            overdue.append(row)
        elif target and (due_day == target or core.todo_day(todo) == target):
            unfinished.append(row)
    return {
        "date": day_text,
        "completed": [
            {
                "todo_id": event["todo_id"],
                "text": event["text"],
                "due_date": event["due_date"],
                "completed_at": event["completed_at"],
                "done_note": event["done_note"],
                "done_reflection": event["done_reflection"],
            }
            for event in completed
        ],
        "unfinished": unfinished,
        "overdue": overdue,
    }
