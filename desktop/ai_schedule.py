#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_schedule.py — AI 调用时段与「复杂任务排队」的纯逻辑。

规则（用户定义）：
- 北京时间（UTC+8）周一至周五（不含中国法定节假日）：
  **09:00–12:00、14:00–18:00 为高峰时段**（不调用 AI）；
- 其余时间（12:00–14:00、18:00–次日 09:00、周末、法定节假日全天）均为空闲时段。

模块只依赖标准库 + requests，方便单元测试；节假日优先读取随包的
``holidays_cn.json``，并可用 GitHub 上的 holiday-cn 数据刷新缓存。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Set

import requests

LOGGER = logging.getLogger("taskbar.ai_schedule")

BEIJING_TZ = _dt.timezone(_dt.timedelta(hours=8), name="Asia/Shanghai")

# 高峰时段（左闭右开，单位：分钟）
PEAK_WINDOWS = ((9 * 60, 12 * 60), (14 * 60, 18 * 60))

HOLIDAY_REPO = "NateScarlet/holiday-cn"
HOLIDAY_URL = "https://api.github.com/repos/{repo}/contents/{year}.json"
DEFAULT_CACHE_NAME = "holidays_cache.json"


def parse_holiday_payload(data: Any) -> Set[_dt.date]:
    """从 holiday-cn 的 JSON 中提取放假日（isOffDay=true）。"""
    dates: Set[_dt.date] = set()
    days: Iterable[Any] = []
    if isinstance(data, dict):
        days = data.get("days") or []
    elif isinstance(data, list):
        days = data
    for item in days:
        if not isinstance(item, dict):
            continue
        if not item.get("isOffDay"):
            continue
        text = str(item.get("date") or "").strip()
        try:
            dates.add(_dt.date.fromisoformat(text))
        except ValueError:
            continue
    return dates


def load_bundled_dates() -> Set[_dt.date]:
    """读取随包的 holidays_cn.json。"""
    candidates: List[Path] = []
    meipass = getattr(os.sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "holidays_cn.json")
    candidates.append(Path(__file__).with_name("holidays_cn.json"))
    for path in candidates:
        try:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                dates = set()
                for text in payload.get("dates") or []:
                    try:
                        dates.add(_dt.date.fromisoformat(str(text)))
                    except ValueError:
                        continue
                LOGGER.info("已加载节假日数据 %d 条：%s", len(dates), path)
                return dates
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.warning("读取节假日数据失败 %s：%s", path, exc)
    return set()


def load_cache_dates(cache_path: Optional[Path]) -> Set[_dt.date]:
    if not cache_path:
        return set()
    try:
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return {
                _dt.date.fromisoformat(str(text))
                for text in payload.get("dates") or []
                if str(text).strip()
            }
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("读取节假日缓存失败：%s", exc)
    return set()


def save_cache_dates(cache_path: Optional[Path], dates: Iterable[_dt.date], source: str) -> None:
    if not cache_path:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": source,
            "updated_at": _dt.datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
            "dates": sorted(day.isoformat() for day in set(dates)),
        }
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError as exc:
        LOGGER.warning("写入节假日缓存失败：%s", exc)


def fetch_holiday_dates(year: int, timeout: float = 10.0, session: Any = None) -> Set[_dt.date]:
    """从 holiday-cn GitHub 仓库获取某年的放假日（无需 Token）。"""
    url = HOLIDAY_URL.format(repo=HOLIDAY_REPO, year=year)
    http = session or requests
    response = http.get(
        url,
        headers={"Accept": "application/vnd.github.raw", "User-Agent": "Win11TaskbarLyrics"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return parse_holiday_payload(data)


def refresh_holidays(
    cache_path: Optional[Path],
    years: Optional[Sequence[int]] = None,
    timeout: float = 10.0,
    session: Any = None,
) -> Set[_dt.date]:
    """尝试刷新若干年份的节假日；失败时回退到缓存/随包数据。"""
    today = _dt.datetime.now(BEIJING_TZ).date()
    target_years = list(years or (today.year, today.year + 1))
    merged = load_bundled_dates() | load_cache_dates(cache_path)
    fetched: Set[_dt.date] = set()
    for year in target_years:
        try:
            fetched |= fetch_holiday_dates(year, timeout=timeout, session=session)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("刷新 %d 年节假日失败：%s", year, exc)
    if fetched:
        merged |= fetched
        save_cache_dates(cache_path, merged, f"{HOLIDAY_REPO} + 随包数据")
    return merged


class HolidayCalendar:
    """节假日 + 高峰/空闲判断。"""

    def __init__(self, dates: Optional[Iterable[_dt.date]] = None) -> None:
        self.dates: Set[_dt.date] = set(dates or ())

    def is_holiday(self, day: _dt.date) -> bool:
        return day in self.dates

    def is_peak(self, moment: _dt.datetime) -> bool:
        """是否处于高峰时段（不允许调用 AI）。"""
        local = (moment if moment.tzinfo else moment.replace(tzinfo=BEIJING_TZ)).astimezone(BEIJING_TZ)
        if local.isoweekday() >= 6:
            return False
        if local.date() in self.dates:
            return False
        minutes = local.hour * 60 + local.minute
        return any(start <= minutes < end for start, end in PEAK_WINDOWS)

    def is_idle(self, moment: _dt.datetime) -> bool:
        return not self.is_peak(moment)

    def next_idle_start(self, moment: Optional[_dt.datetime] = None) -> _dt.datetime:
        """返回下一个空闲时段的开始时间（当前已空闲则返回当前分钟）。"""
        local = (moment or _dt.datetime.now(BEIJING_TZ))
        if not local.tzinfo:
            local = local.replace(tzinfo=BEIJING_TZ)
        local = local.astimezone(BEIJING_TZ)
        if self.is_idle(local):
            return local
        for start, end in PEAK_WINDOWS:
            if start <= local.hour * 60 + local.minute < end:
                hour, minute = divmod(end, 60)
                return local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return local


def load_calendar(cache_path: Optional[Path] = None) -> HolidayCalendar:
    """同步加载（随包 + 本地缓存），用于程序启动。"""
    return HolidayCalendar(load_bundled_dates() | load_cache_dates(cache_path))


def default_cache_path(app_dir: Optional[Path] = None) -> Path:
    if app_dir is not None:
        return app_dir / DEFAULT_CACHE_NAME
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Win11TaskbarLyrics" / DEFAULT_CACHE_NAME
