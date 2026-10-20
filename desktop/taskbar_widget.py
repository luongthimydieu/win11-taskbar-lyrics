#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""taskbar_widget.py — Win11 任务栏悬浮胶囊（逐行歌词 + 网易云高赞热评 + 跨端待办提醒）v1.1

功能概览
--------
- 悬浮在 Win11 任务栏空白处（不是 deskband，不修改系统），可拖拽并记住位置；
- 主行显示当前播放歌曲的逐行歌词；副行在“高赞热评”和“未完成待办”间轮播；
- 手机端在 GitHub Pages 上增删待办、写入私密 Gist；本程序每 30 秒用 ETag 拉取同步；
- 到点提醒：红底呼吸闪烁，左键一键完成并回写云端；
- 右键菜单：切换模式、完成待办、配置同步、稍后提醒、重新对齐任务栏、退出；
- 附加：系统托盘、单实例锁、滚动日志、网易云接口失败自动降级。

依赖（任选一条环境路线）
------------------------
Python 3.10–3.11（推荐，winsdk 有官方轮子）::

    pip install PyQt6 requests winsdk

Python 3.12+（winsdk 没有对应轮子，改用 pywinrt 拆分包）::

    pip install PyQt6 requests winrt-Windows.Media.Control

运行::

    python taskbar_widget.py            # 正常启动
    python taskbar_widget.py --selftest # 无界面自检（CI / 打包前验证）

注意：没有 winsdk / pywinrt 时会自动降级为“读取网易云窗口标题”，歌词按
墙钟估算推进（暂停/拖动进度条无法精确跟随），功能仍可用。
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import datetime
import json
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 允许“同一个目录下直接运行脚本”和“被 PyInstaller 打包”两种场景
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import requests
from PyQt6.QtCore import QDate, QTime, QObject, QRectF, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

import core
import ai_schedule
import task_archive

LOGGER = logging.getLogger("taskbar.widget")

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

APP_NAME = "Win11TaskbarLyrics"
APP_TITLE = "Win11 任务栏歌词 · 跨端待办"
APP_VERSION = "1.3.8"

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
LEDGER_STATE_PATH = APP_DIR / "ledger_state.json"
AI_QUEUE_PATH = APP_DIR / "ai_queue.json"
HOLIDAY_CACHE_PATH = APP_DIR / "holidays_cache.json"
ARCHIVE_PATH = APP_DIR / "archive.json"
LOG_PATH = APP_DIR / "app.log"
#: 调试用：每次界面文字变化时把“当前实际要显示的文字”落盘，便于区分“状态没更新”还是“画面没重绘”
DEBUG_TEXT_PATH = APP_DIR / "last_text.json"

MUTEX_NAME = "Local\\Win11TaskbarLyrics_SingleInstance"

WIDGET_HEIGHT = 48
DEFAULT_WIDTH = 560
ROTATE_MIN_SECONDS = 5.0
ERROR_ALREADY_EXISTS = 183

DEFAULT_CONFIG: Dict[str, Any] = {
    "config_version": 4,
    "gh_token": "",
    "gist_id": "",
    "github_direct": True,     # api.github.com 直连（绕过系统代理），避免代理抖动导致同步失败
    "lyric_offset_ms": 0,      # 歌词同步微调（正数=歌词更早；用于修正播放器/SMTC 的 0.x 秒延迟）
    # ==== v1.3.0 待办/记账/周报/记忆 ====
    "ai_enabled": True,
    "ai_base_url": "https://api.deepseek.com",
    "ai_api_key": "",
    "ai_model": "deepseek-flash",
    "mail_host": "smtp.gmail.com",
    "mail_port": 465,
    "mail_user": "",
    "mail_password": "",
    "mail_to": "",
    "ledger_gist_id": "",
    "memory_enabled": True,
    "memory_repo": "luongthimydieu/taskbar-memory",
    "memory_token": "",
    "memory_branch": "main",
    "memory_time": "04:00",
    "weekly_report_enabled": True,
    "weekly_report_weekday": 7,   # ISO：7=周日
    "weekly_report_time": "21:00",
    "last_memory_date": "",
    "last_report_week": "",
    # AI 只在空闲时调用（系统无键鼠输入 ≥ N 分钟，或处于安静时段）
    "offset_ratio": 0.0,       # 0=自动；拖拽后保存为相对任务栏宽度的比例
    "auto_anchor": True,       # True=自动贴到托盘区左侧的空位
    "width": DEFAULT_WIDTH,
    "mode": "auto",            # auto | lyrics | comments | todos
    # overlay=置顶覆盖（默认，Qt 绘制稳定）；embed=嵌入任务栏（外观更原生，但部分系统上
    # 跨进程子窗口的合成不稳定，可能出现文字不刷新/不显示）
    "taskbar_mode": "overlay",
    "rotate_sec": 8,           # 副行在「热评/下一项待办」之间切换的间隔（秒）
    "comment_rotate_sec": 20,  # 有意思的热评之间的轮播间隔（秒）
    "sync_sec": 30,            # 云端同步间隔（秒）
    "show_tray": True,
    "hide_on_fullscreen": True,    # B站/网页视频全屏、全屏游戏时自动隐藏
    "daily_fill_reminder": True,   # 每天 22:00 提醒填明天的待办
    "last_fill_reminded": "",      # 最近一次已提醒的“展示日”，避免重复弹
}

# ---------------------------------------------------------------------------
# 可选媒体后端：winsdk（Python ≤3.11）或 pywinrt 拆分包（Python 3.12+）
# ---------------------------------------------------------------------------

MEDIA_BACKEND = ""
_SessionManager = None
try:  # pragma: no cover - 取决于运行环境
    from winsdk.windows.media.control import (  # type: ignore
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
    )
    MEDIA_BACKEND = "winsdk"
except Exception:  # noqa: BLE001 - 缺依赖时静默降级
    try:  # pragma: no cover - 取决于运行环境
        from winrt.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
        )
        MEDIA_BACKEND = "pywinrt"
    except Exception:  # noqa: BLE001
        MEDIA_BACKEND = ""

#: 网易云 PC 端窗口类名（用于没有 SMTC 时的兜底识别）
NETEASE_WINDOW_CLASSES = ("OrpheusBrowserHost", "Win32Window")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def ensure_app_dir() -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        LOGGER.warning("无法创建配置目录：%s", APP_DIR)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def find_logo_file() -> Optional[Path]:
    """找项目自带的 logo.png（源码目录 / PyInstaller 解包目录 / 用户数据目录）。"""
    candidates: List[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "logo.png")
    candidates.append(_HERE / "logo.png")
    candidates.append(APP_DIR / "logo.png")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _add_no_proxy_hosts(hosts: Tuple[str, ...]) -> None:
    """把指定域名加入 NO_PROXY，让 requests 对这些域名绕过系统代理。"""
    for key in ("NO_PROXY", "no_proxy"):
        current = [part.strip() for part in os.environ.get(key, "").split(",") if part.strip()]
        for host in hosts:
            if host not in current:
                current.append(host)
        os.environ[key] = ",".join(current)


def _remove_no_proxy_hosts(hosts: Tuple[str, ...]) -> None:
    for key in ("NO_PROXY", "no_proxy"):
        current = [
            part.strip()
            for part in os.environ.get(key, "").split(",")
            if part.strip() and part.strip() not in hosts
        ]
        if current:
            os.environ[key] = ",".join(current)
        else:
            os.environ.pop(key, None)


def apply_github_direct_env(config: Dict[str, Any]) -> None:
    """按配置设置 GitHub API 直连（默认只对 api.github.com 绕过代理）。

    Clash/代理软件偶尔重启或线路抖动时，requests 会收到 ProxyError；
    api.github.com 在国内通常可以直连，固定走直连能显著减少同步失败。
    """
    if bool(config.get("github_direct", True)):
        _add_no_proxy_hosts(("api.github.com",))
        LOGGER.info("GitHub API 直连已启用（api.github.com 已加入 NO_PROXY，绕过系统代理）")


def load_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    migrated = False
    try:
        if CONFIG_PATH.exists():
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update({key: loaded[key] for key in loaded if key in DEFAULT_CONFIG})
                if "config_version" not in loaded:
                    # v1.2 布局迁移：旧版只是“悬浮卡片”，升级后默认任务栏等高、自动贴空位
                    migrated = True
                    config["config_version"] = 4
                    config["width"] = DEFAULT_WIDTH
                    config["auto_anchor"] = True
                    config["offset_ratio"] = 0.0
                    config["taskbar_mode"] = "overlay"
                elif int(loaded.get("config_version") or 0) < 4:
                    # v1.2.1：默认改为置顶覆盖；旧版拖拽/嵌入留下的位置与宽度已不适用，重置布局
                    migrated = True
                    config["config_version"] = 4
                    config["taskbar_mode"] = "overlay"
                    config["auto_anchor"] = True
                    config["offset_ratio"] = 0.0
                    config["width"] = DEFAULT_WIDTH
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取配置失败，使用默认配置：%s", exc)
    if migrated:
        LOGGER.info("检测到旧版配置，已迁移为 v1.2 布局（Token / Gist ID 保留）")
        save_config(config)
    apply_github_direct_env(config)
    return config


def save_config(config: Dict[str, Any]) -> None:
    try:
        ensure_app_dir()
        _atomic_write_json(CONFIG_PATH, config)
    except OSError as exc:
        LOGGER.warning("保存配置失败：%s", exc)


def load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取本地状态失败：%s", exc)
    return {"doc": core.empty_doc(), "etag": None, "last_sync": "", "last_error": ""}


def save_state(doc: Dict[str, Any], etag: Optional[str], last_sync: str, last_error: str) -> None:
    try:
        ensure_app_dir()
        _atomic_write_json(
            STATE_PATH,
            {
                "doc": core.normalize_doc(doc),
                "etag": etag,
                "last_sync": last_sync,
                "last_error": last_error,
            },
        )
    except OSError as exc:
        LOGGER.warning("保存本地状态失败：%s", exc)


def _empty_ledger() -> Dict[str, Any]:
    factory = getattr(core, "empty_ledger", None)
    if factory is not None:
        try:
            return factory()
        except Exception:  # noqa: BLE001
            pass
    return {"schema": 1, "version": 0, "updated_at": "", "entries": [], "tombstones": []}


def load_ledger_state() -> Dict[str, Any]:
    payload: Any = None
    try:
        if LEDGER_STATE_PATH.exists():
            payload = json.loads(LEDGER_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取本地账本失败：%s", exc)
    normalize = getattr(core, "normalize_ledger", None)
    doc = payload.get("doc") if isinstance(payload, dict) else None
    if normalize is not None:
        try:
            doc = normalize(doc)
        except Exception:  # noqa: BLE001
            doc = _empty_ledger()
    elif not isinstance(doc, dict):
        doc = _empty_ledger()
    etag = payload.get("etag") if isinstance(payload, dict) else None
    return {"doc": doc, "etag": etag}


def save_ledger_state(doc: Dict[str, Any], etag: Optional[str]) -> None:
    try:
        ensure_app_dir()
        normalize = getattr(core, "normalize_ledger", None)
        normalized = normalize(doc) if normalize is not None else doc
        _atomic_write_json(LEDGER_STATE_PATH, {"doc": normalized, "etag": etag})
    except OSError as exc:
        LOGGER.warning("保存本地账本失败：%s", exc)


def _hhmm_reached(now: "datetime.datetime", value: str) -> bool:
    """判断 HH:MM 是否已到（格式错误时返回 False）。"""
    try:
        hour, minute = [int(part) for part in str(value).strip().split(":", 1)]
    except (TypeError, ValueError):
        return False
    return (now.hour, now.minute) >= (hour, minute)


def _week_start(moment: "datetime.date") -> "datetime.date":
    return moment - datetime.timedelta(days=moment.weekday())


def acquire_single_instance() -> bool:
    """Windows 命名互斥量实现单实例；已在运行返回 False。"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return False
        # 故意不释放：进程存活期间保持互斥量
        globals()["_INSTANCE_MUTEX"] = handle
        return True
    except Exception:  # noqa: BLE001 - 非 Windows 或受限环境
        return True


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class Win32Taskbar:
    """任务栏 Win32 操作：定位、托盘区、明暗主题、嵌入/还原窗口。

    Windows 11 的任务栏内容由 XAML（``Windows.UI.Composition.DesktopWindowContentBridge``）
    绘制，本程序用 ``SetParent`` 把自绘窗口挂成 ``Shell_TrayWnd`` 的子窗口，并把它顶到
    任务栏子窗口 z 序最前面，从而实现“长在任务栏上”的效果；嵌入失败时自动退回置顶覆盖。
    """

    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_CHILD = 0x40000000
    WS_POPUP = 0x80000000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    HWND_TOP = 0
    HWND_TOPMOST = -1
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_FRAMECHANGED = 0x0020
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:  # 64 位下获取完整 LONG_PTR 样式
        _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    except AttributeError:  # pragma: no cover - 32 位 Python
        pass
    # 屏幕取色相关：HDC 是 64 位句柄，必须显式声明，否则会被截断
    # （GetPixel 属于 gdi32，不是 user32）
    try:
        _user32.GetDC.argtypes = [wintypes.HWND]
        _user32.GetDC.restype = wintypes.HDC
        _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        _user32.ReleaseDC.restype = ctypes.c_int
        _gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        _gdi32.GetPixel.restype = wintypes.DWORD
        # 全屏检测
        _user32.GetForegroundWindow.restype = wintypes.HWND
        _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        _user32.MonitorFromWindow.restype = ctypes.c_void_p
        _user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
        _user32.GetMonitorInfoW.restype = wintypes.BOOL
        _user32.IsIconic.argtypes = [wintypes.HWND]
        _user32.IsIconic.restype = wintypes.BOOL
        _user32.IsWindowVisible.argtypes = [wintypes.HWND]
        _user32.IsWindowVisible.restype = wintypes.BOOL
        _user32.WindowFromPoint.argtypes = [wintypes.POINT]
        _user32.WindowFromPoint.restype = wintypes.HWND
        _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        _user32.GetAncestor.restype = wintypes.HWND
        _user32.EnumWindows.restype = wintypes.BOOL
        _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _user32.EnumDisplayMonitors.restype = wintypes.BOOL
        _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        _user32.MonitorFromPoint.restype = ctypes.c_void_p
        _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    except AttributeError:  # pragma: no cover - 极端精简系统
        pass
    # GDI 截图：一次 BitBlt 抓取整条任务栏，避免逐像素 GetPixel 太慢卡住 UI
    try:
        _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        _gdi32.CreateCompatibleDC.restype = wintypes.HDC
        _gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        _gdi32.SelectObject.restype = wintypes.HGDIOBJ
        _gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        _gdi32.BitBlt.restype = wintypes.BOOL
        _gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
        ]
        _gdi32.GetDIBits.restype = ctypes.c_int
        _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        _gdi32.DeleteObject.restype = wintypes.BOOL
        _gdi32.DeleteDC.argtypes = [wintypes.HDC]
        _gdi32.DeleteDC.restype = wintypes.BOOL
    except AttributeError:  # pragma: no cover - 极端精简系统
        pass

    # -- 查询 -------------------------------------------------------------
    @classmethod
    def find(cls) -> int:
        try:
            return int(cls._user32.FindWindowW("Shell_TrayWnd", None) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def rect(cls, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        if not hwnd:
            return None
        try:
            value = wintypes.RECT()
            if not cls._user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(value)):
                return None
            return value.left, value.top, value.right, value.bottom
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def client_origin(cls, hwnd: int) -> Tuple[int, int]:
        point = wintypes.POINT(0, 0)
        try:
            if not hwnd or not cls._user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
                return 0, 0
            return int(point.x), int(point.y)
        except Exception:  # noqa: BLE001
            return 0, 0

    # -- 全屏检测 ---------------------------------------------------------
    @classmethod
    def class_name(cls, hwnd: int) -> str:
        try:
            buffer = ctypes.create_unicode_buffer(256)
            cls._user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
            return buffer.value
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    def foreground_window(cls) -> int:
        try:
            return int(cls._user32.GetForegroundWindow() or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def monitor_rect(cls, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        try:
            monitor = cls._user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # NEAREST
            if not monitor:
                return None
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if not cls._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None
            rect = info.rcMonitor
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def shell_d3d_fullscreen(cls) -> bool:
        """Windows 自带的“当前是否全屏 D3D 应用”检测（游戏常见）。"""
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            state = ctypes.c_int(0)
            result = shell32.SHQueryUserNotificationState(ctypes.byref(state))
            return result == 0 and state.value == 3  # QUNS_RUNNING_D3D_FULL_SCREEN
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def is_foreground_fullscreen(cls, exclude_hwnd: int = 0) -> bool:
        """前台窗口是否全屏：浏览器 F11、网页视频全屏、全屏游戏都会覆盖整个显示器。"""
        hwnd = cls.foreground_window()
        if not hwnd or hwnd == exclude_hwnd:
            return False
        if cls.class_name(hwnd) in (
            "Shell_TrayWnd",
            "Progman",
            "WorkerW",
        ):
            return False
        try:
            if cls._user32.IsIconic(wintypes.HWND(hwnd)) or not cls._user32.IsWindowVisible(
                wintypes.HWND(hwnd)
            ):
                return False
        except Exception:  # noqa: BLE001
            pass
        window_rect = cls.rect(hwnd)
        monitor = cls.monitor_rect(hwnd)
        if window_rect is None or monitor is None:
            return False
        primary = cls.primary_monitor_rect()
        if primary is not None and monitor != primary:
            return False  # 只关心主显示器
        return core.rect_covers_monitor(window_rect, primary or monitor, tolerance=2) or cls.shell_d3d_fullscreen()

    @classmethod
    def window_at(cls, x: int, y: int) -> int:
        """屏幕坐标处最上层的窗口句柄。"""
        try:
            return int(cls._user32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def window_root(cls, hwnd: int) -> int:
        """取窗口的顶层祖先（GW_ANCESTOR_ROOT）。"""
        try:
            root = cls._user32.GetAncestor(wintypes.HWND(hwnd), 2)  # GA_ROOT
            return int(root or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def is_point_on_taskbar(cls, x: int, y: int) -> bool:
        """某个屏幕坐标处是否真的是任务栏（而不是被别的窗口盖住）。"""
        hwnd = cls.window_at(x, y)
        taskbar = cls.find()
        if not hwnd or not taskbar:
            return False
        return cls.window_root(hwnd) == taskbar

    @classmethod
    def primary_monitor_rect(cls) -> Optional[Tuple[int, int, int, int]]:
        """主显示器的物理像素矩形。"""
        try:
            monitor = cls._user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # PRIMARY
            if not monitor:
                return None
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if not cls._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None
            rect = info.rcMonitor
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def has_primary_fullscreen_window(cls, exclude_hwnd: int = 0) -> bool:
        """主显示器上是否有窗口铺满（不要求它是前台窗口）。

        只关心主屏：副屏全屏不影响主屏任务栏组件的显示。
        从 z 序最上层往下找，碰到桌面（Progman/WorkerW）就停止，
        避免把桌面壁纸/装饰层当成全屏窗口。
        """
        primary = cls.primary_monitor_rect()
        if primary is None:
            return False
        found = [False]
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, lparam):  # noqa: ANN001
            if found[0]:
                return False
            try:
                if not hwnd or hwnd == exclude_hwnd:
                    return True
                name = cls.class_name(hwnd)
                if name in ("Progman", "WorkerW"):
                    return False  # 到桌面为止，再往下都是桌面级别的窗口
                if name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                    return True
                if not cls._user32.IsWindowVisible(hwnd) or cls._user32.IsIconic(hwnd):
                    return True
                pid = wintypes.DWORD()
                cls._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == os.getpid():
                    return True
                if cls._is_cloaked(hwnd):
                    return True
                window_rect = cls.rect(hwnd)
                monitor = cls.monitor_rect(hwnd)
                if window_rect is None or monitor is None:
                    return True
                if monitor != primary:
                    return True
                if core.rect_covers_monitor(window_rect, primary, tolerance=2):
                    found[0] = True
                    return False
                return True
            except Exception:  # noqa: BLE001
                return True

        cls._user32.EnumWindows(callback_type(callback), 0)
        return found[0]

    @classmethod
    def _is_cloaked(cls, hwnd: int) -> bool:
        """UWP 隐藏窗口（cloaked）不算可见窗口。"""
        try:
            value = wintypes.DWORD(0)
            result = cls._dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value)  # DWMWA_CLOAKED
            )
            return result == 0 and bool(value.value & 1)
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def _child_by_class(cls, parent: int, class_name: str) -> int:
        try:
            return int(cls._user32.FindWindowExW(wintypes.HWND(parent), None, class_name, None) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def tray_rect(cls) -> Optional[Tuple[int, int, int, int]]:
        """托盘区（TrayNotifyWnd）的物理像素矩形；用于把窗口贴到托盘左侧空位。"""
        taskbar = cls.find()
        if not taskbar:
            return None
        child = cls._child_by_class(taskbar, "TrayNotifyWnd")
        return cls.rect(child) if child else None

    @classmethod
    def child_rect(cls, parent: int, class_name: str) -> Optional[Tuple[int, int, int, int]]:
        child = cls._child_by_class(parent, class_name)
        return cls.rect(child) if child else None

    @classmethod
    def layout_gaps(cls, taskbar_rect: Tuple[int, int, int, int]) -> List[Tuple[int, int]]:
        """用任务栏已知子窗口（开始按钮/任务按钮区/托盘区）算出“安全空档”的物理像素区间。

        像素扫描可能偶尔因为任务栏正在重绘而误判，这里用窗口矩形兜底，保证不会盖住
        开始按钮、任务按钮和托盘。
        """
        left, top, right, bottom = taskbar_rect
        taskbar = cls.find()
        if not taskbar:
            return [(left, right)]
        obstacles: List[Tuple[int, int]] = []
        for class_name in ("Start", "ReBarWindow32", "TrayDummySearchControl"):
            rect = cls.child_rect(taskbar, class_name)
            if rect and rect[2] > rect[0]:
                obstacles.append((int(rect[0]), int(rect[2])))
        tray = cls.tray_rect()
        limit = int(tray[0]) if tray and tray[0] > left + 40 else right
        gaps: List[Tuple[int, int]] = []
        cursor = int(left)
        for start, end in sorted(obstacles):
            start = max(int(left), int(start))
            end = min(limit, int(end))
            if start > cursor + 8:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if limit > cursor + 8:
            gaps.append((cursor, limit))
        return gaps

    @classmethod
    def capture_pixels(
        cls, rect: Tuple[int, int, int, int]
    ) -> Optional[Tuple[int, int, bytes]]:
        """用 GDI BitBlt 一次性抓取屏幕矩形，返回 (width, height, BGRA bytes)。"""
        left, top, right, bottom = rect
        width = int(right - left)
        height = int(bottom - top)
        if width <= 0 or height <= 0:
            return None
        screen = mem = bitmap = None
        try:
            screen = cls._user32.GetDC(None)
            if not screen:
                return None
            mem = cls._gdi32.CreateCompatibleDC(screen)
            bitmap = cls._gdi32.CreateCompatibleBitmap(screen, width, height)
            if not mem or not bitmap:
                return None
            cls._gdi32.SelectObject(mem, bitmap)
            if not cls._gdi32.BitBlt(mem, 0, 0, width, height, screen, int(left), int(top), 0x00CC0020):
                return None
            buffer = ctypes.create_string_buffer(width * height * 4)
            header = _BitmapInfoHeader()
            header.biSize = ctypes.sizeof(_BitmapInfoHeader)
            header.biWidth = width
            header.biHeight = -height  # 负数=自上而下
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = 0  # BI_RGB
            info = _BitmapInfo()
            info.bmiHeader = header
            if not cls._gdi32.GetDIBits(
                mem, bitmap, 0, height, buffer, ctypes.byref(info), 0
            ):
                return None
            return width, height, buffer.raw
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                if mem:
                    cls._gdi32.DeleteDC(mem)
                if bitmap:
                    cls._gdi32.DeleteObject(bitmap)
                if screen:
                    cls._user32.ReleaseDC(None, screen)
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def empty_column_runs(
        cls,
        taskbar_rect: Tuple[int, int, int, int],
        exclude: Optional[Tuple[int, int, int, int]] = None,
        step: int = 2,
    ) -> List[Tuple[int, int]]:
        """扫描任务栏，找出“没有图标”的连续空位（物理像素，返回 (start, end)）。

        Win11 的任务栏图标可能由 XAML 绘制，读 ReBarWindow32 的矩形并不可靠，
        所以直接对任务栏截一条像素带：图标/圆点/文字所在的列纵向颜色差异很大，
        空位（纯色/亚克力背景）纵向颜色基本一致。
        """
        left, top, right, bottom = taskbar_rect
        tray = cls.tray_rect()
        limit_right = int(tray[0]) if tray and tray[0] > left + 40 else right
        captured = cls.capture_pixels((left, top, min(right, limit_right), bottom))
        if captured is None:
            return []
        width, height, data = captured
        if width <= 4 or height <= 4:
            return []

        fractions = (0.18, 0.36, 0.54, 0.72, 0.88)

        def pixel(x: int, y: int) -> Tuple[int, int, int]:
            index = (y * width + x) * 4
            return data[index + 2], data[index + 1], data[index]

        busy_flags: List[bool] = []
        for x in range(2, width - 2, step):
            absolute_x = left + x
            if exclude and exclude[0] <= absolute_x < exclude[2]:
                # 这段是我们自己当前占用的位置，移动之后会空出来，按“空位”处理
                busy_flags.append(False)
                continue
            samples = [pixel(x, int(height * fraction)) for fraction in fractions]
            score = 0
            for i in range(len(samples)):
                for j in range(i + 1, len(samples)):
                    score = max(score, max(abs(a - b) for a, b in zip(samples[i], samples[j])))
            busy_flags.append(score > 30)

        runs: List[Tuple[int, int]] = []
        run_start: Optional[int] = None
        for index, busy in enumerate(busy_flags):
            if not busy and run_start is None:
                run_start = index
            elif busy and run_start is not None:
                runs.append((run_start, index))
                run_start = None
        if run_start is not None:
            runs.append((run_start, len(busy_flags)))
        return [(left + 2 + start * step, left + 2 + end * step) for start, end in runs]

    @classmethod
    def parent_of(cls, hwnd: int) -> int:
        try:
            return int(cls._user32.GetParent(wintypes.HWND(hwnd)) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    def dark_mode(cls, hwnd: int) -> bool:
        """任务栏是否深色：优先问 DWM，失败时读系统主题注册表。"""
        value = ctypes.c_int(-1)
        try:
            result = cls._dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                cls.DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                return bool(value.value)
        except Exception:  # noqa: BLE001
            pass
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 0
        except OSError:
            return False

    @classmethod
    def _get_pixel(cls, hdc, x: int, y: int) -> Optional[Tuple[int, int, int]]:
        try:
            color = int(cls._gdi32.GetPixel(hdc, int(x), int(y)))
        except Exception:  # noqa: BLE001
            return None
        if color == -1 or color == 0xFFFFFFFF:
            return None
        return color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF

    @classmethod
    def background_luminance(
        cls, taskbar_rect: Tuple[int, int, int, int], exclude: Optional[Tuple[int, int, int, int]] = None
    ) -> Optional[float]:
        """采样任务栏背景亮度（用于判断该用深色还是浅色文字）。

        任务栏在 Win11 是“亚克力/透明”的，颜色会随壁纸变化，不能只看系统主题；
        这里在任务栏左侧空位采样几个点，排除本程序窗口自身占据的区域。
        """
        try:
            hdc = cls._user32.GetDC(None)
        except Exception:  # noqa: BLE001
            return None
        if not hdc:
            return None
        try:
            left, top, right, bottom = taskbar_rect
            width = max(1, right - left)
            height = max(1, bottom - top)
            samples: List[float] = []
            for fx in (0.02, 0.05, 0.09, 0.13):
                x = left + int(width * fx)
                if x >= right:
                    continue
                for fy in (0.35, 0.6):
                    y = top + int(height * fy)
                    if exclude and exclude[0] <= x < exclude[2] and exclude[1] <= y < exclude[3]:
                        continue
                    rgb = cls._get_pixel(hdc, x, y)
                    if rgb is None:
                        continue
                    samples.append(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2])
            if not samples:
                return None
            samples.sort()
            return samples[len(samples) // 2]
        finally:
            try:
                cls._user32.ReleaseDC(None, hdc)
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def is_dark_background(
        cls, taskbar_rect: Tuple[int, int, int, int], exclude: Optional[Tuple[int, int, int, int]] = None
    ) -> Optional[bool]:
        luminance = cls.background_luminance(taskbar_rect, exclude)
        if luminance is None:
            return None
        return luminance < 125

    @classmethod
    def _get_long(cls, hwnd: int, index: int) -> int:
        try:
            return int(cls._user32.GetWindowLongPtrW(wintypes.HWND(hwnd), index))
        except (AttributeError, TypeError):
            return int(cls._user32.GetWindowLongW(wintypes.HWND(hwnd), index))

    @classmethod
    def _set_long(cls, hwnd: int, index: int, value: int) -> None:
        try:
            cls._user32.SetWindowLongPtrW(wintypes.HWND(hwnd), index, ctypes.c_ssize_t(value))
        except (AttributeError, TypeError):
            cls._user32.SetWindowLongW(wintypes.HWND(hwnd), index, ctypes.c_long(value & 0xFFFFFFFF))

    # -- 嵌入 / 还原 ------------------------------------------------------
    @classmethod
    def embed(cls, child: int, parent: int) -> bool:
        """把 child 变成任务栏的子窗口；成功返回 True。

        注意：SetParent 后立刻用 GetParent 校验并不可靠（实测 Windows 11 上会短暂
        返回 0，要等 SetWindowPos 之后才稳定），所以这里只以 SetParent 的错误码为准，
        真正的父窗口校验放到移动窗口之后做。
        """
        if not child or not parent:
            return False
        try:
            ctypes.set_last_error(0)
            cls._user32.SetParent(wintypes.HWND(child), wintypes.HWND(parent))
            error = ctypes.get_last_error()
            if error:
                LOGGER.warning("SetParent 失败：%s", ctypes.FormatError(error))
                return False
            style = cls._get_long(child, cls.GWL_STYLE)
            style = (style & ~(cls.WS_POPUP | cls.WS_CAPTION | cls.WS_THICKFRAME)) | cls.WS_CHILD
            cls._set_long(child, cls.GWL_STYLE, style)
            ex_style = cls._get_long(child, cls.GWL_EXSTYLE)
            ex_style = (ex_style | cls.WS_EX_NOACTIVATE) & ~cls.WS_EX_APPWINDOW
            cls._set_long(child, cls.GWL_EXSTYLE, ex_style)
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("嵌入任务栏异常")
            return False

    @classmethod
    def detach(cls, child: int) -> None:
        """把 child 从任务栏摘下来，恢复成普通弹出窗口。"""
        if not child:
            return
        try:
            cls._user32.SetParent(wintypes.HWND(child), None)
            style = cls._get_long(child, cls.GWL_STYLE)
            style = (style & ~cls.WS_CHILD) | cls.WS_POPUP
            cls._set_long(child, cls.GWL_STYLE, style)
            ex_style = cls._get_long(child, cls.GWL_EXSTYLE)
            ex_style = (ex_style | cls.WS_EX_TOOLWINDOW | cls.WS_EX_NOACTIVATE) & ~cls.WS_EX_APPWINDOW
            cls._set_long(child, cls.GWL_EXSTYLE, ex_style)
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def _set_window_pos(cls, child: int, insert_after: int, x: int, y: int, width: int, height: int) -> bool:
        try:
            flags = cls.SWP_NOACTIVATE | cls.SWP_SHOWWINDOW | cls.SWP_FRAMECHANGED
            return bool(
                cls._user32.SetWindowPos(
                    wintypes.HWND(child),
                    wintypes.HWND(insert_after),
                    int(x),
                    int(y),
                    int(width),
                    int(height),
                    flags,
                )
            )
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def move_child(cls, child: int, x: int, y: int, width: int, height: int) -> bool:
        """在任务栏客户区坐标系里移动子窗口，并顶到兄弟窗口最前面。"""
        return cls._set_window_pos(child, cls.HWND_TOP, x, y, width, height)

    @classmethod
    def move_topmost(cls, child: int, x: int, y: int, width: int, height: int) -> bool:
        """覆盖模式：放到屏幕坐标并保持置顶。"""
        return cls._set_window_pos(child, cls.HWND_TOPMOST, x, y, width, height)

    @classmethod
    def show_window(cls, hwnd: int, visible: bool) -> None:
        """临时显示/隐藏窗口（用于扫描空位时把自身从截屏里去掉）。"""
        try:
            cls._user32.ShowWindow(wintypes.HWND(hwnd), 4 if visible else 0)  # SW_SHOWNOACTIVATE / SW_HIDE
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def force_redraw(cls, hwnd: int) -> None:
        """强制任务栏里的子窗口立刻重绘。

        嵌入到别的进程窗口后，Qt 的 update() 有时不会马上反映到 DWM 合成结果上，
        表现为“状态更新了但任务栏上还是旧文字”。这里用 RedrawWindow 硬刷一次。
        """
        try:
            cls._user32.RedrawWindow(
                wintypes.HWND(hwnd),
                None,
                None,
                0x0001 | 0x0100 | 0x0080,  # RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN
            )
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def window_rect(cls, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        return cls.rect(hwnd)


# ---------------------------------------------------------------------------
# Qt 信号桥（工作线程 -> 主线程）
# ---------------------------------------------------------------------------

class Bridge(QObject):
    media_updated = pyqtSignal(dict)
    lyrics_ready = pyqtSignal(str, dict)
    sync_finished = pyqtSignal(dict)
    ai_plan_ready = pyqtSignal(object)
    ledger_sync_finished = pyqtSignal(dict)
    report_ready = pyqtSignal(object)
    mail_finished = pyqtSignal(dict)
    memory_finished = pyqtSignal(dict)
    calendar_ready = pyqtSignal(object)
    ai_queue_result = pyqtSignal(object)


# ---------------------------------------------------------------------------
# 媒体监听线程（常驻事件循环，避免在 Qt 主线程反复 asyncio.run）
# ---------------------------------------------------------------------------

class MediaWatcher(threading.Thread):
    """每 0.5 秒读取一次当前播放状态：优先 SMTC，失败时退回网易云窗口标题。"""

    def __init__(self, bridge: Bridge, interval: float = 0.5) -> None:
        super().__init__(daemon=True, name="MediaWatcher")
        self.bridge = bridge
        self.interval = max(0.2, float(interval))
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._manager: Any = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - 线程逻辑
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        LOGGER.info("媒体监听线程启动，后端=%s", MEDIA_BACKEND or "window-title")
        try:
            while not self._stop_event.is_set():
                payload = self._poll_once()
                if payload:
                    self.bridge.media_updated.emit(payload)
                self._stop_event.wait(self.interval)
        except Exception:  # noqa: BLE001
            LOGGER.exception("媒体监听线程异常退出")
        finally:
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    def _poll_once(self) -> Optional[Dict[str, Any]]:
        if _SessionManager is not None and self._loop is not None:
            try:
                info = self._loop.run_until_complete(self._read_smtc())
                if info:
                    return info
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("SMTC 读取失败，使用窗口标题兜底：%s", exc)
        return self._read_window_title()

    async def _read_smtc(self) -> Optional[Dict[str, Any]]:
        if self._manager is None:
            self._manager = await _SessionManager.request_async()
        sessions = list(self._manager.get_sessions())
        target = None
        app_id = ""
        for session in sessions:
            candidate = str(getattr(session, "source_app_user_model_id", "") or "").lower()
            if any(key in candidate for key in ("cloudmusic", "netease", "163")):
                target, app_id = session, candidate
                break
        if target is None:
            target = self._manager.get_current_session()
            app_id = str(getattr(target, "source_app_user_model_id", "") or "").lower() if target else ""
        if target is None:
            return None

        media = None
        try:
            media = await target.try_get_media_properties_async()
        except Exception:  # noqa: BLE001
            media = None
        title = str(getattr(media, "title", "") or "").strip()
        if not title:
            return None
        artist = str(getattr(media, "artist", "") or "").strip()

        position = -1.0
        position_at = 0.0
        try:
            timeline = target.get_timeline_properties()
            position = float(timeline.position.total_seconds())
            # LastUpdatedTime 是“这个 position 采样于什么时候”（UTC），比轮询时刻更准，
            # 可以让歌词估算少 0.x 秒误差；部分播放器会给 1601 哨兵值，由上层校验丢弃。
            updated = getattr(timeline, "last_updated_time", None)
            if updated is not None and hasattr(updated, "timestamp"):
                try:
                    position_at = float(updated.timestamp())
                except (OSError, OverflowError, ValueError):
                    position_at = 0.0
        except Exception:  # noqa: BLE001
            position = -1.0
            position_at = 0.0

        is_playing = True
        try:
            playback = target.get_playback_info()
            is_playing = int(playback.playback_status) == 4  # PLAYING
        except Exception:  # noqa: BLE001
            is_playing = True

        return {
            "title": title,
            "artist": artist,
            "position": position,
            "position_at": position_at,
            "is_playing": is_playing,
            "source": "smtc",
            "app_id": app_id,
        }

    @staticmethod
    def _read_window_title() -> Optional[Dict[str, Any]]:
        """兜底：枚举网易云主窗口标题“歌名 - 歌手”。

        不要求窗口可见（最小化到托盘后仍能拿到标题），这是 v1.0 最大的坑之一。
        """
        try:
            user32 = ctypes.windll.user32
        except Exception:  # noqa: BLE001
            return None
        found: List[str] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):  # type: ignore[no-untyped-def]
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            if class_buffer.value in NETEASE_WINDOW_CLASSES:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    text_buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, text_buffer, length + 1)
                    text = text_buffer.value.strip()
                    if " - " in text:
                        found.append(text)
                        return False
            return True

        try:
            user32.EnumWindows(_enum, 0)
        except Exception:  # noqa: BLE001
            return None
        if not found:
            return None
        title, artist = found[0].split(" - ", 1)
        return {
            "title": title.strip(),
            "artist": artist.strip(),
            "position": -1.0,
            "is_playing": True,  # 兜底模式无法判断暂停，按“正在播放”估算
            "source": "window",
            "app_id": "OrpheusBrowserHost",
        }


# ---------------------------------------------------------------------------
# 设置对话框
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """配置 GitHub Gist 云同步参数。"""

    def __init__(self, parent: Optional[QWidget], config: Dict[str, Any], doc: Dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("GitHub Gist 云同步配置")
        self.setModal(True)
        self.resize(560, 260)
        self._doc = core.normalize_doc(doc)
        self._config = dict(config)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.token_edit = QLineEdit(str(config.get("gh_token", "")))
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("ghp_xxx（classic token 勾选 gist 权限即可）")
        self.gist_edit = QLineEdit(str(config.get("gist_id", "")))
        self.gist_edit.setPlaceholderText("留空可点“创建新 Gist”自动生成")
        form.addRow("GitHub Token", self.token_edit)
        form.addRow("Gist ID", self.gist_edit)
        layout.addLayout(form)

        hint = QLabel(
            "提示：手机端与电脑端要填同一组 Token + Gist ID。\n"
            "“创建新 Gist”会把当前电脑端的待办写进去，并把 Gist ID 填回这里。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b;font-size:12px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox()
        self.test_button = QPushButton("测试连接")
        self.create_button = QPushButton("创建新 Gist")
        buttons.addButton(self.test_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.create_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)

        self.test_button.clicked.connect(self._on_test)
        self.create_button.clicked.connect(self._on_create)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    # -- 辅助 -------------------------------------------------------------
    def _token(self) -> str:
        return self.token_edit.text().strip()

    def _gist_id(self) -> str:
        return self.gist_edit.text().strip()

    def _on_test(self) -> None:
        token, gist_id = self._token(), self._gist_id()
        if not token or not gist_id:
            QMessageBox.warning(self, "缺少参数", "请先填写 Token 和 Gist ID。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            store = core.GitHubGistStore(token=token, gist_id=gist_id, timeout=8)
            fetch = store.get_doc()
            count = len(fetch.doc["todos"])
            QMessageBox.information(
                self,
                "连接成功",
                f"已连上 Gist，当前云端有 {count} 条待办。\nETag: {str(fetch.etag)[:16]}…",
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "连接失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _on_create(self) -> None:
        token = self._token()
        if not token:
            QMessageBox.warning(self, "缺少参数", "创建新 Gist 需要先填写 Token。")
            return
        if self._gist_id():
            answer = QMessageBox.question(
                self,
                "已有 Gist ID",
                "当前已填写 Gist ID，仍要再创建一个新的私密 Gist 吗？\n"
                "创建后手机端也要改成新的 Gist ID。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            store = core.GitHubGistStore(token=token, timeout=10)
            gist_id = store.create_gist(self._doc)
            self.gist_edit.setText(gist_id)
            QMessageBox.information(
                self,
                "创建成功",
                "已创建私密 Gist，并写入当前待办。\nGist ID 已填入输入框，保存后生效。",
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def result_config(self) -> Dict[str, Any]:
        updated = dict(self._config)
        updated["gh_token"] = self._token()
        updated["gist_id"] = self._gist_id()
        return updated


class AdvancedSettingsDialog(QDialog):
    """AI 规划 / 周报邮件 / 记忆仓库 / 账本的配置。"""

    CATEGORIES = ["餐饮", "交通", "购物", "学习", "娱乐", "工资", "人情", "其他"]
    WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def __init__(self, parent: Optional[QWidget], config: Dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("高级设置：AI / 邮件 / 记忆 / 账本")
        self.setModal(True)
        self.resize(640, 560)
        self._config = dict(config)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # --- AI ---
        ai_page = QWidget()
        ai_form = QFormLayout(ai_page)
        self.ai_enabled = QCheckBox("启用 AI（复杂任务拆解 / 规划 / 每日评价）")
        self.ai_enabled.setChecked(bool(config.get("ai_enabled", True)))
        self.ai_base = QLineEdit(str(config.get("ai_base_url", "")))
        self.ai_base.setPlaceholderText("https://api.deepseek.com")
        self.ai_model = QLineEdit(str(config.get("ai_model", "deepseek-chat")))
        self.ai_model.setPlaceholderText("deepseek-chat")
        self.ai_key = QLineEdit(str(config.get("ai_api_key", "")))
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key.setPlaceholderText("sk-...（只保存在本机 config）")
        ai_form.addRow("", self.ai_enabled)
        ai_form.addRow("API Base URL", self.ai_base)
        ai_form.addRow("Model", self.ai_model)
        ai_form.addRow("API Key", self.ai_key)
        model_hint = QLabel(
            "本机 API 可用模型：deepseek-flash（快速/低成本，适合定时任务）、deepseek-v4-pro（更强）。"
            "定时记忆/周报的 AI 评价会优先使用 flash；若使用 Gemini Flash 等其它服务，请改 Base URL 和 Model。"
        )
        model_hint.setWordWrap(True)
        model_hint.setStyleSheet("color:#64748b;font-size:12px;")
        ai_form.addRow("", model_hint)
        policy_hint = QLabel(
            "AI 调用时段：北京时间**周一至周五 09:00–12:00、14:00–18:00**（不含中国法定节假日）为高峰，"
            "期间不调用 AI；其余时间（12:00–14:00、18:00–次日 09:00、周末、法定节假日全天）为空闲时段。"
            "高峰时点「AI 拆解」会先排队，进入空闲后自动用 deepseek-flash 拆解并加入待办。"
            "节假日数据随包内置，并会自动从 holiday-cn 刷新缓存。"
        )
        policy_hint.setWordWrap(True)
        policy_hint.setStyleSheet("color:#94a3b8;font-size:12px;line-height:1.55;")
        ai_form.addRow("调用时段", policy_hint)
        tabs.addTab(ai_page, "🤖 AI")

        # --- 周报邮件 ---
        mail_page = QWidget()
        mail_form = QFormLayout(mail_page)
        self.mail_host = QLineEdit(str(config.get("mail_host", "smtp.gmail.com")))
        self.mail_port = QSpinBox()
        self.mail_port.setRange(1, 65535)
        self.mail_port.setValue(int(config.get("mail_port", 465)))
        self.mail_user = QLineEdit(str(config.get("mail_user", "")))
        self.mail_user.setPlaceholderText("xxx@gmail.com")
        self.mail_password = QLineEdit(str(config.get("mail_password", "")))
        self.mail_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_password.setPlaceholderText("Gmail 应用专用密码（不是登录密码）")
        self.mail_to = QLineEdit(str(config.get("mail_to", "")))
        self.mail_to.setPlaceholderText("收件邮箱，默认同发件邮箱")
        self.report_enabled = QCheckBox("每周自动发送")
        self.report_enabled.setChecked(bool(config.get("weekly_report_enabled", True)))
        self.report_weekday = QComboBox()
        self.report_weekday.addItems(self.WEEKDAYS)
        self.report_weekday.setCurrentIndex(max(0, min(6, int(config.get("weekly_report_weekday", 7)) - 1)))
        self.report_time = QLineEdit(str(config.get("weekly_report_time", "21:00")))
        self.report_time.setPlaceholderText("21:00")
        mail_form.addRow("SMTP 服务器", self.mail_host)
        mail_form.addRow("端口（SSL）", self.mail_port)
        mail_form.addRow("发件邮箱", self.mail_user)
        mail_form.addRow("应用密码", self.mail_password)
        mail_form.addRow("收件邮箱", self.mail_to)
        mail_form.addRow("", self.report_enabled)
        mail_form.addRow("发送日", self.report_weekday)
        mail_form.addRow("发送时间", self.report_time)
        tabs.addTab(mail_page, "📧 周报邮件")

        # --- 记忆仓库 ---
        memory_page = QWidget()
        memory_form = QFormLayout(memory_page)
        self.memory_enabled = QCheckBox("每天自动推送记忆到 GitHub 仓库")
        self.memory_enabled.setChecked(bool(config.get("memory_enabled", True)))
        self.memory_repo = QLineEdit(str(config.get("memory_repo", "luongthimydieu/taskbar-memory")))
        self.memory_repo.setPlaceholderText("owner/repo")
        self.memory_token = QLineEdit(str(config.get("memory_token", "")))
        self.memory_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.memory_token.setPlaceholderText("需要 repo 写权限的 Token（可与同步 Token 相同）")
        self.memory_branch = QLineEdit(str(config.get("memory_branch", "main")))
        self.memory_time = QLineEdit(str(config.get("memory_time", "23:30")))
        self.memory_time.setPlaceholderText("23:30")
        memory_form.addRow("", self.memory_enabled)
        memory_form.addRow("仓库", self.memory_repo)
        memory_form.addRow("Token", self.memory_token)
        memory_form.addRow("分支", self.memory_branch)
        memory_form.addRow("每日推送时间", self.memory_time)
        tabs.addTab(memory_page, "🧠 记忆仓库")

        # --- 账本 ---
        ledger_page = QWidget()
        ledger_form = QFormLayout(ledger_page)
        self.ledger_gist = QLineEdit(str(config.get("ledger_gist_id", "")))
        self.ledger_gist.setPlaceholderText("独立 Gist ID（留空点“创建账本 Gist”）")
        self.ledger_create_btn = QPushButton("创建账本 Gist")
        self.ledger_create_btn.clicked.connect(self._create_ledger_gist)
        ledger_form.addRow("账本 Gist ID", self.ledger_gist)
        ledger_form.addRow("", self.ledger_create_btn)
        ledger_hint = QLabel("账本与待办分开存：待办用 taskbar_todos.json，账本用 taskbar_ledger.json。")
        ledger_hint.setWordWrap(True)
        ledger_hint.setStyleSheet("color:#64748b;font-size:12px;")
        ledger_form.addRow("", ledger_hint)
        tabs.addTab(ledger_page, "💰 账本")

        test_row = QHBoxLayout()
        self.test_ai_btn = QPushButton("测试 AI")
        self.test_mail_btn = QPushButton("发送测试邮件")
        self.test_memory_btn = QPushButton("测试记忆仓库")
        test_row.addWidget(self.test_ai_btn)
        test_row.addWidget(self.test_mail_btn)
        test_row.addWidget(self.test_memory_btn)
        test_row.addStretch(1)
        layout.addLayout(test_row)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.test_ai_btn.clicked.connect(self._test_ai)
        self.test_mail_btn.clicked.connect(self._test_mail)
        self.test_memory_btn.clicked.connect(self._test_memory)

    # -- helpers ----------------------------------------------------------
    def _current_config(self) -> Dict[str, Any]:
        cfg = dict(self._config)
        cfg.update(
            {
                "ai_enabled": self.ai_enabled.isChecked(),
                "ai_base_url": self.ai_base.text().strip(),
                "ai_model": self.ai_model.text().strip(),
                "ai_api_key": self.ai_key.text().strip(),
                "mail_host": self.mail_host.text().strip(),
                "mail_port": self.mail_port.value(),
                "mail_user": self.mail_user.text().strip(),
                "mail_password": self.mail_password.text().strip(),
                "mail_to": self.mail_to.text().strip() or self.mail_user.text().strip(),
                "weekly_report_enabled": self.report_enabled.isChecked(),
                "weekly_report_weekday": self.report_weekday.currentIndex() + 1,
                "weekly_report_time": self.report_time.text().strip() or "21:00",
                "memory_enabled": self.memory_enabled.isChecked(),
                "memory_repo": self.memory_repo.text().strip(),
                "memory_token": self.memory_token.text().strip(),
                "memory_branch": self.memory_branch.text().strip() or "main",
                "memory_time": self.memory_time.text().strip() or "23:30",
                "ledger_gist_id": self.ledger_gist.text().strip(),
            }
        )
        return cfg

    def result_config(self) -> Dict[str, Any]:
        return self._current_config()

    def _test_ai(self) -> None:
        cfg = self._current_config()
        if not cfg.get("ai_api_key"):
            QMessageBox.warning(self, "缺少 API Key", "请先填写 AI API Key。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import ai_client  # 延迟导入，集成模块缺失时给出明确提示

            client = ai_client.OpenAICompatibleClient(
                base_url=cfg["ai_base_url"],
                api_key=cfg["ai_api_key"],
                model=cfg["ai_model"],
                timeout=30,
            )
            reply = client.chat([{"role": "user", "content": "请只回复两个字：正常"}], temperature=0)
            QMessageBox.information(self, "AI 连接成功", str(reply)[:300])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "AI 连接失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _test_mail(self) -> None:
        cfg = self._current_config()
        if not cfg.get("mail_user") or not cfg.get("mail_password"):
            QMessageBox.warning(self, "缺少参数", "请先填写发件邮箱和应用密码。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import reporting

            reporting.send_email(cfg, "任务栏助手：测试邮件", "<p>这是一封测试邮件，说明 SMTP 配置可用。</p>")
            QMessageBox.information(self, "发送成功", "测试邮件已发送，请查收。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "发送失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _test_memory(self) -> None:
        cfg = self._current_config()
        token = cfg.get("memory_token") or cfg.get("gh_token")
        if not token or not cfg.get("memory_repo"):
            QMessageBox.warning(self, "缺少参数", "请填写记忆仓库和带 repo 权限的 Token。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import memory_sync

            repo = memory_sync.MemoryRepo(
                token=token,
                repo=cfg["memory_repo"],
                branch=cfg.get("memory_branch") or "main",
            )
            repo.ensure_repo()
            QMessageBox.information(self, "仓库连接成功", f"可以写入 {cfg['memory_repo']}。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "仓库连接失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _create_ledger_gist(self) -> None:
        token = self._config.get("gh_token", "").strip()
        if not token:
            QMessageBox.warning(self, "缺少参数", "请先在“同步”设置里填写 GitHub Token。")
            return
        ledger_store = getattr(core, "LedgerStore", None)
        if ledger_store is None:
            QMessageBox.warning(self, "模块未就绪", "当前版本还没有账本模块，请稍后重试。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            store = ledger_store(token=token, timeout=10)
            gist_id = store.create_gist(core.empty_ledger())
            self.ledger_gist.setText(gist_id)
            QMessageBox.information(self, "创建成功", "已创建账本 Gist，ID 已填入，保存后生效。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()


class AddTodoDialog(QDialog):
    """添加待办：可指定未来完成日期、优先级、预计耗时。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加待办")
        self.setModal(True)
        self.resize(480, 260)
        form = QFormLayout()
        self.text_edit = QLineEdit()
        self.text_edit.setPlaceholderText("要做什么？")
        form.addRow("内容", self.text_edit)

        date_row = QHBoxLayout()
        self.has_due = QCheckBox("指定目标完成日期")
        self.due_edit = QDateEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDate(QDate.currentDate())
        self.due_edit.setEnabled(False)
        self.has_due.toggled.connect(self.due_edit.setEnabled)
        date_row.addWidget(self.has_due)
        date_row.addWidget(self.due_edit, 1)
        form.addRow("目标日期", date_row)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["普通", "🔥 紧急"])
        form.addRow("优先级", self.priority_combo)
        self.estimate_spin = QSpinBox()
        self.estimate_spin.setRange(0, 6000)
        self.estimate_spin.setSingleStep(15)
        self.estimate_spin.setSuffix(" 分钟（0=未估）")
        form.addRow("预计耗时", self.estimate_spin)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        hint = QLabel("复杂任务可以在右键菜单用「🤖 AI 拆解复杂任务」自动拆成子任务。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b;font-size:12px;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

    def _on_accept(self) -> None:
        if not self.text_edit.text().strip():
            QMessageBox.warning(self, "内容为空", "请输入待办内容。")
            return
        self.accept()

    def values(self) -> Dict[str, Any]:
        return {
            "text": self.text_edit.text().strip(),
            "due_date": self.due_edit.date().toString("yyyy-MM-dd") if self.has_due.isChecked() else "",
            "priority": "high" if self.priority_combo.currentIndex() == 1 else "normal",
            "estimate_minutes": int(self.estimate_spin.value()),
        }


class CompletionDialog(QDialog):
    """点击完成时记录「完成情况」与「心得」。"""

    def __init__(self, parent: Optional[QWidget], task_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("✅ 完成任务")
        self.setModal(True)
        self.resize(520, 380)
        self._skip = False
        layout = QVBoxLayout(self)
        title = QLabel(f"「{task_text}」")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:600;font-size:14px;")
        layout.addWidget(title)
        hint = QLabel("简单记一下做了什么、有什么心得；留空也可以直接完成。")
        hint.setStyleSheet("color:#64748b;font-size:12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(QLabel("完成情况（做了什么 / 结果）"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("例如：写完了课程设计初稿，实验数据已整理")
        self.note_edit.setFixedHeight(80)
        layout.addWidget(self.note_edit)
        layout.addWidget(QLabel("心得 / 反思"))
        self.reflection_edit = QTextEdit()
        self.reflection_edit.setPlaceholderText("例如：下次先列大纲再写正文，效率会更高")
        self.reflection_edit.setFixedHeight(80)
        layout.addWidget(self.reflection_edit)
        buttons = QDialogButtonBox()
        self.skip_button = QPushButton("直接完成")
        self.save_button = QPushButton("保存完成")
        buttons.addButton(self.skip_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        self.skip_button.clicked.connect(self._on_skip)
        self.save_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _on_skip(self) -> None:
        self._skip = True
        self.accept()

    def values(self) -> Dict[str, str]:
        if self._skip:
            return {"done_note": "", "done_reflection": ""}
        return {
            "done_note": self.note_edit.toPlainText().strip(),
            "done_reflection": self.reflection_edit.toPlainText().strip(),
        }


class AiPlanDialog(QDialog):
    """输入一个复杂任务，调用 AI 拆成多个带日期的小任务。

    高峰时段以 ``queue_mode=True`` 打开：不调用 API，只把任务收进排队，等空闲时段
    由主程序自动拆解并加入待办。
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        config: Dict[str, Any],
        queue_mode: bool = False,
        idle_at: Optional["datetime.datetime"] = None,
        default_horizon: int = 14,
        default_granularity: str = "standard",
        replan_parent: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.replan_parent = dict(replan_parent or {})
        self.replan_mode = bool(self.replan_parent)
        long_term = int(default_horizon) >= 21
        if self.replan_mode:
            title = "🔄 更新任务规划（AI 重排）" if not queue_mode else "⏳ 更新规划排队（高峰时段）"
        else:
            title = (
                "⏳ AI 任务排队（高峰时段）"
                if queue_mode
                else ("📅 长期任务规划（AI 排期）" if long_term else "🤖 AI 拆解复杂任务")
            )
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(700, 640)
        self._config = dict(config)
        self._plan: Optional[Dict[str, Any]] = None
        self.queue_mode = bool(queue_mode)
        self.queued = False

        layout = QVBoxLayout(self)
        if self.replan_mode:
            done_titles = self.replan_parent.get("done_titles") or []
            pending_titles = self.replan_parent.get("incomplete_titles") or []
            intro_text = (
                f"🔄 更新「{self.replan_parent.get('parent_text') or '长期任务'}」的规划：\n"
                f"已完成 {len(done_titles)} 步、未完成 {len(pending_titles)} 步。"
                "AI 会保留已完成的子任务，替换未完成的子任务；已完成记录不会丢。"
            )
        else:
            intro_text = (
                "📅 长期任务：描述目标 + 选好期限/每天投入/粒度，AI 会拆成多天可执行的子任务；"
                "高峰时段只排队，空闲后自动生成并加入待办。"
            )
        intro = QLabel(intro_text)
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#94a3b8;font-size:12px;")
        layout.addWidget(intro)
        form = QFormLayout()
        self.task_edit = QLineEdit()
        if self.replan_mode:
            self.task_edit.setPlaceholderText(
                "更新要求（可空）：例如“进度落后了，剩余步骤压缩到 10 天内，每天只有 1 小时”"
            )
        else:
            self.task_edit.setPlaceholderText(
                "例如：3 周内完成数学建模竞赛（选题、数据、建模、论文、答辩），每天 2 小时"
            )
        form.addRow("更新要求" if self.replan_mode else "长期任务", self.task_edit)
        self.horizon_spin = QSpinBox()
        self.horizon_spin.setRange(3, 120)
        self.horizon_spin.setValue(max(3, min(120, int(default_horizon))))
        self.horizon_spin.setSuffix(" 天内完成")
        form.addRow("完成期限", self.horizon_spin)
        preset_row = QHBoxLayout()
        for days, label in ((7, "1 周"), (14, "2 周"), (30, "1 个月"), (60, "2 个月")):
            preset_btn = QPushButton(label)
            preset_btn.setMinimumHeight(34)
            preset_btn.clicked.connect(lambda _checked=False, d=days: self.horizon_spin.setValue(d))
            preset_row.addWidget(preset_btn)
        form.addRow("快速期限", preset_row)
        self.daily_combo = QComboBox()
        for label, minutes in (("不限", 0), ("30 分钟", 30), ("1 小时", 60), ("2 小时", 120), ("3 小时", 180)):
            self.daily_combo.addItem(label, minutes)
        form.addRow("每天可投入", self.daily_combo)
        self.granularity_combo = QComboBox()
        for label, code in (
            ("简洁（3–5 步）", "concise"),
            ("适中（5–8 步）", "standard"),
            ("详细（8–12 步）", "detailed"),
        ):
            self.granularity_combo.addItem(label, code)
        granularity_codes = ["concise", "standard", "detailed"]
        granularity_code = str(default_granularity or "standard").strip().lower()
        self.granularity_combo.setCurrentIndex(
            granularity_codes.index(granularity_code) if granularity_code in granularity_codes else 1
        )
        form.addRow("拆分粒度", self.granularity_combo)
        self.weekend_combo = QComboBox()
        self.weekend_combo.addItem("只在工作日安排", False)
        self.weekend_combo.addItem("周末也可以", True)
        form.addRow("周末安排", self.weekend_combo)
        layout.addLayout(form)

        self.preview = QTextBrowser()
        layout.addWidget(self.preview, 1)

        row = QHBoxLayout()
        self.generate_btn = QPushButton("加入排队" if self.queue_mode else "生成计划")
        self.add_btn = QPushButton("加入待办")
        self.add_btn.setEnabled(False)
        row.addWidget(self.generate_btn)
        row.addWidget(self.add_btn)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(self.reject)

        if self.queue_mode:
            self.add_btn.setVisible(False)
            when = ""
            if idle_at is not None:
                when = f"预计 **{idle_at.strftime('%m-%d %H:%M')}** 进入空闲时段后自动运行。"
            self.preview.setMarkdown(
                "⏳ **当前是高峰时段，暂不调用 AI。**\n\n"
                "按设置，北京时间周一至周五 09:00–12:00、14:00–18:00（不含法定节假日）为高峰；"
                "任务会先排队，不消耗 token。\n\n" + when +
                "\n\n运行时会自动拆解成带日期的小任务并加入待办，完成后托盘提示。"
            )
            self.generate_btn.clicked.connect(self._enqueue)
        else:
            self.generate_btn.clicked.connect(self._generate)
            self.add_btn.clicked.connect(self._on_add)

    def _enqueue(self) -> None:
        if not self.task_text().strip():
            QMessageBox.warning(self, "内容为空", "请输入任务或更新要求。")
            return
        self.queued = True
        self.accept()

    def _generate(self) -> None:
        cfg = self._config
        if not cfg.get("ai_enabled", True) or not cfg.get("ai_api_key"):
            QMessageBox.warning(self, "AI 未配置", "请先在「高级设置 → AI」里填写 API Key。")
            return
        text = self.task_text().strip()
        if not text:
            QMessageBox.warning(self, "内容为空", "请输入任务描述。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import ai_client

            client = ai_client.OpenAICompatibleClient(
                base_url=cfg["ai_base_url"],
                api_key=cfg["ai_api_key"],
                model=cfg["ai_model"],
                timeout=60,
            )
            plan = ai_client.plan_complex_task(
                client,
                text,
                core.current_day(),
                horizon_days=int(self.horizon_spin.value()),
                options=self.options(),
            )
            self._plan = plan
            lines = [f"**{plan.get('title') or text}**", ""]
            for index, step in enumerate(plan.get("steps", []), start=1):
                lines.append(
                    f"{index}. {step.get('text', '')}  \n"
                    f"    📅 {step.get('due_date', '')} · ⏱ {step.get('estimate_minutes', 0)} 分钟"
                )
            if plan.get("note"):
                lines.extend(["", f"> {plan['note']}"])
            self.preview.setMarkdown("\n".join(lines))
            self.add_btn.setEnabled(bool(plan.get("steps")))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _on_add(self) -> None:
        if self._plan:
            self.accept()

    def plan(self) -> Optional[Dict[str, Any]]:
        return self._plan

    def task_text(self) -> str:
        if self.replan_mode:
            parent_text = str(self.replan_parent.get("parent_text") or "长期任务")
            update = self.task_edit.text().strip()
            return f"{parent_text}（更新规划：{update}）" if update else f"{parent_text}（更新规划）"
        return self.task_edit.text().strip()

    def replan_context(self) -> str:
        if not self.replan_mode:
            return ""
        done_titles = self.replan_parent.get("done_titles") or []
        pending_titles = self.replan_parent.get("incomplete_titles") or []
        due = str(self.replan_parent.get("due_date") or "")
        return (
            f"任务：{self.replan_parent.get('parent_text') or '长期任务'}\n"
            f"原计划完成日期：{due or '未指定'}\n"
            f"已完成（保留）：{('、'.join(done_titles[:10]) or '无')}\n"
            f"未完成（可调整/替换）：{('、'.join(pending_titles[:10]) or '无')}\n"
            f"用户更新要求：{self.task_edit.text().strip() or '根据进度重新安排剩余步骤'}"
        )

    def is_replan(self) -> bool:
        return self.replan_mode

    def horizon_days(self) -> int:
        return int(self.horizon_spin.value())

    def options(self) -> Dict[str, Any]:
        options = {
            "daily_minutes": int(self.daily_combo.currentData() or 0),
            "granularity": str(self.granularity_combo.currentData() or "standard"),
            "allow_weekends": bool(self.weekend_combo.currentData()),
        }
        if self.replan_mode:
            options["context_text"] = self.replan_context()
        return options

    def is_queued(self) -> bool:
        return self.queued


class LedgerDialog(QDialog):
    """记一笔 + 本周账本概览。"""

    CATEGORIES = ["餐饮", "交通", "购物", "学习", "娱乐", "工资", "人情", "其他"]

    def __init__(self, parent: Optional[QWidget], ledger_doc: Dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("💰 记一笔 / 本周账本")
        self.setModal(True)
        self.resize(640, 520)
        self._ledger = ledger_doc

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime.currentTime())
        self.type_combo = QComboBox()
        self.type_combo.addItems(["支出", "收入"])
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setDecimals(2)
        self.amount_spin.setRange(0.0, 10_000_000.0)
        self.amount_spin.setPrefix("¥ ")
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(self.CATEGORIES)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("备注（可选）")
        form.addRow("日期", self.date_edit)
        form.addRow("时间", self.time_edit)
        form.addRow("类型", self.type_combo)
        form.addRow("金额", self.amount_spin)
        form.addRow("分类", self.category_combo)
        form.addRow("备注", self.note_edit)
        layout.addLayout(form)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-weight:600;padding:4px 0;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["日期", "时间", "类型", "金额", "分类", "备注"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._refresh()

    def _week_start(self) -> Any:
        today = core.current_day()
        return today - __import__("datetime").timedelta(days=today.weekday())

    def _refresh(self) -> None:
        try:
            summary = core.ledger_week_summary(self._ledger, self._week_start())
        except Exception:  # noqa: BLE001
            summary = {"income": 0, "expense": 0, "net": 0, "entries": []}
        self.summary_label.setText(
            f"本周：收入 {core.format_money(summary['income'])} · "
            f"支出 {core.format_money(summary['expense'])} · "
            f"结余 {core.format_money(summary['net'])}"
        )
        entries = summary.get("entries", [])
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                str(entry.get("date", "")),
                str(entry.get("time", "")),
                "收入" if entry.get("type") == "income" else "支出",
                core.format_money(float(entry.get("amount", 0))),
                str(entry.get("category", "")),
                str(entry.get("note", "")),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _on_accept(self) -> None:
        if self.amount_spin.value() <= 0:
            QMessageBox.warning(self, "金额无效", "请输入大于 0 的金额。")
            return
        self.accept()

    def values(self) -> Dict[str, Any]:
        return {
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "time": self.time_edit.time().toString("HH:mm"),
            "type": "income" if self.type_combo.currentIndex() == 1 else "expense",
            "amount": round(float(self.amount_spin.value()), 2),
            "category": self.category_combo.currentText().strip() or "其他",
            "note": self.note_edit.text().strip(),
        }


class WeeklyReportDialog(QDialog):
    """周报预览 + 手动发送。"""

    def __init__(self, parent: Optional[QWidget], markdown: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("📊 周报预览")
        self.setModal(True)
        self.resize(760, 620)
        self.send_requested = False
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setMarkdown(markdown)
        layout.addWidget(browser, 1)
        row = QHBoxLayout()
        preview_hint = QLabel("如果配置了 Gmail SMTP，可直接发送到邮箱。")
        preview_hint.setStyleSheet("color:#64748b;font-size:12px;")
        row.addWidget(preview_hint)
        row.addStretch(1)
        send_btn = QPushButton("📤 发送到邮箱")
        close_btn = QPushButton("关闭")
        row.addWidget(send_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)
        send_btn.clicked.connect(self._on_send)
        close_btn.clicked.connect(self.reject)

    def _on_send(self) -> None:
        self.send_requested = True
        self.accept()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class TaskbarWidget(QWidget):
    """任务栏上的半透明胶囊：自绘文字、提醒闪烁、点击完成、右键菜单。"""

    def __init__(self, selftest: bool = False) -> None:
        super().__init__()
        self.selftest = selftest
        ensure_app_dir()
        self.config = load_config()
        state = load_state()
        self.doc: Dict[str, Any] = core.normalize_doc(state.get("doc"))
        self.etag: Optional[str] = state.get("etag")
        self.last_sync: str = str(state.get("last_sync") or "")
        self.last_error: str = str(state.get("last_error") or "")

        self.bridge = Bridge()
        self.bridge.media_updated.connect(self.on_media_updated)
        self.bridge.lyrics_ready.connect(self.on_lyrics_ready)
        self.bridge.sync_finished.connect(self.on_sync_finished)
        self.bridge.ledger_sync_finished.connect(self.on_ledger_sync_finished)
        self.bridge.mail_finished.connect(self.on_mail_finished)
        self.bridge.memory_finished.connect(self.on_memory_finished)
        self.bridge.calendar_ready.connect(self.on_calendar_ready)
        self.bridge.ai_queue_result.connect(self.on_ai_queue_result)

        self.netease = core.NetEaseClient()
        self.store = self._build_store()

        # AI 空闲时段 / 排队（高峰只排队，空闲自动运行）
        self.ai_calendar = ai_schedule.load_calendar(APP_DIR)
        self.ai_queue: List[Dict[str, Any]] = self._load_ai_queue()
        self._ai_queue_running = False
        self._ai_queue_retry_at = 0.0

        # 记账（独立 Gist + 本地缓存）
        ledger_state = load_ledger_state()
        self.ledger_doc: Dict[str, Any] = ledger_state["doc"]
        self.ledger_etag: Optional[str] = ledger_state.get("etag")
        self.ledger_store = self._build_ledger_store()
        self._ledger_sync_running = False
        self._ledger_sync_thread: Optional[threading.Thread] = None
        self.archive = task_archive.load_archive(ARCHIVE_PATH)
        self._sync_archive_from_doc()
        self._memory_running = False
        self._mail_running = False
        self._memory_retry_at = 0.0
        self._mail_retry_at = 0.0

        # 媒体 / 歌词状态
        self._media: Optional[Dict[str, Any]] = None
        self._media_mono = 0.0
        self._song_key = ""
        # 本地歌词时间轴（SMTC 的时间轴经常给 0/空值，需要本地单调推进）
        self._est_song = ""
        self._est_base = 0.0
        self._est_mono = time.monotonic()
        self._est_playing = False
        self._est_anchor_seen = -1.0
        self._lyrics: List[Tuple[float, str]] = []
        self._hot_comments: List[str] = []
        self._lyrics_state = "idle"   # idle | loading | ok | failed
        self._lyrics_error = ""
        self._fallback_start_mono = time.monotonic()
        self._lyrics_request_key = ""
        self._lyrics_last_attempt = 0.0

        # 显示 / 轮播状态
        self._rotation_anchor = time.monotonic()
        self._rotation_offset = 0
        self._comment_anchor = time.monotonic()
        self._comment_offset = 0
        self._primary_text = "🎵 任务栏显示器已就绪"
        self._secondary_text = "📱 在手机端添加待办，30 秒内同步到这里"
        self._text_signature: Tuple[str, str, bool] = ("", "", False)
        self._alerting = False

        # 提醒状态
        self._alert_queue: List[str] = []
        self._current_alert_id: Optional[str] = None

        # 每日待办：凌晨 2:00 翻篇、22:00 提醒填明天
        self._last_daily_day = core.current_day()

        # 同步状态
        self._sync_thread: Optional[threading.Thread] = None
        self._sync_running = False

        # 任务栏嵌入状态
        self._taskbar_hwnd_cache: int = 0
        self._taskbar_rect_cache: Optional[Tuple[int, int, int, int]] = None
        self._embedded = False
        self._dark_taskbar = True
        self._layout_retries = 0
        self._paint_count = 0
        self._bg_image: Optional[QImage] = None
        self._force_clean_rescan = False
        self._hidden_for_space = False
        self._hidden_for_fullscreen = False
        self._user_hidden = False
        self._fullscreen_true_streak = 0
        self._fullscreen_false_streak = 0

        # 拖拽状态
        self._drag_offset = None
        self._dragging = False

        self._tray: Optional[QSystemTrayIcon] = None
        self._last_notified_error = ""

        self.init_ui()
        self._apply_logo()
        self.init_timers()
        self.start_media_watcher()
        if not selftest:
            self.start_tray()
        self.refresh_texts()
        self.check_reminders()

        if self.store is not None and self.store.configured:
            QTimer.singleShot(1200, self.request_sync)

    # ==================== 初始化 ====================
    def _apply_logo(self) -> None:
        """有 logo.png 就用作窗口/任务栏图标（exe 图标在打包时已带入）。"""
        logo = find_logo_file()
        if logo is None:
            return
        icon = QIcon(str(logo))
        if icon.isNull():
            return
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def _build_store(self) -> Optional[core.GitHubGistStore]:
        token = str(self.config.get("gh_token", "") or "").strip()
        gist_id = str(self.config.get("gist_id", "") or "").strip()
        if not token or not gist_id:
            return None
        return core.GitHubGistStore(token=token, gist_id=gist_id, timeout=8)

    def init_ui(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(APP_TITLE)
        self.resize(int(self.config.get("width", DEFAULT_WIDTH)), WIDGET_HEIGHT)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self.selftest:
            # 窗口真正创建后再嵌入任务栏（winId 此时才有效）；稍等 300ms 让任务栏先画好图标
            QTimer.singleShot(300, self.apply_taskbar_mode)

    def _screen_dpr(self) -> float:
        screen = QGuiApplication.primaryScreen()
        try:
            return float(screen.devicePixelRatio()) if screen else 1.0
        except Exception:  # noqa: BLE001
            return 1.0

    def _resolve_placement(
        self,
        taskbar_rect: Tuple[int, int, int, int],
        exclude: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[int, int]:
        """在任务栏里找一个不遮挡图标的位置，返回 (x 物理像素, 宽度 物理像素)。

        - 默认自动：扫描任务栏空位，优先选最宽的空位，窗口贴在这个空位靠右一侧；
        - 拖拽过以后：按保存的相对比例定位（用户自己决定位置）。
        """
        dpr = self._screen_dpr() or 1.0
        left, top, right, bottom = taskbar_rect
        taskbar_width = max(1, right - left)
        configured = int(max(240, int(self.config.get("width", DEFAULT_WIDTH))) * dpr)
        ratio = float(self.config.get("offset_ratio", 0.0) or 0.0)
        auto_anchor = bool(self.config.get("auto_anchor", True)) or ratio <= 0
        if not auto_anchor:
            x = left + int(taskbar_width * ratio)
            x = max(left + 4, min(x, left + taskbar_width - configured - 4))
            return x, configured

        runs = Win32Taskbar.empty_column_runs(taskbar_rect, exclude=exclude)
        gaps = Win32Taskbar.layout_gaps(taskbar_rect)
        if not gaps:
            gaps = [(left, right)]
        # 像素空位 ∩ 窗口坐标算出的安全带（两者都满足才不会盖住按钮）
        candidates: List[Tuple[int, int]] = []
        for run_start, run_end in runs:
            for gap_start, gap_end in gaps:
                start = max(run_start, gap_start)
                end = min(run_end, gap_end)
                if end - start >= 80:
                    candidates.append((start, end))
        if not candidates:
            candidates = [(s, e) for s, e in gaps if e - s >= 80] or gaps
        fitting = [run for run in candidates if run[1] - run[0] >= configured + 16]
        if fitting:
            # 优先用最靠左的空位（居中任务栏时就是开始按钮左侧那一大段）
            start, end = min(fitting, key=lambda run: run[0])
            LOGGER.debug(
                "placement: runs=%s gaps=%s candidates=%s -> (%s,%s) width=%s",
                runs, gaps, candidates, start, end, configured,
            )
            return end - configured - 8, configured
        # 没有足够宽的空位：优先选靠左且 >= 300px 的空位，否则选最宽的空位并缩窄
        decent = [run for run in candidates if run[1] - run[0] >= 300]
        start, end = min(decent, key=lambda run: run[0]) if decent else max(
            candidates, key=lambda run: run[1] - run[0]
        )
        width = int(max(240 * dpr, min(configured, (end - start) - 16)))
        return end - width - 8, width

    def _taskbar_target(self, ignore_self: bool = False) -> Optional[Dict[str, Any]]:
        """返回任务栏/托盘/目标窗口的位置信息（物理+逻辑像素）。

        说明：**不会移动/隐藏自身窗口**去扫描空位——实测在任务栏子窗口上反复移动自己
        会让 DWM 停止合成该窗口（文字消失）。启动时窗口还没贴上任务栏，扫描天然干净；
        贴上以后只要任务栏矩形没变就保持原位，不重复扫描。
        """
        taskbar = Win32Taskbar.find()
        rect = Win32Taskbar.rect(taskbar) if taskbar else None
        if not taskbar or rect is None:
            return None
        dpr = self._screen_dpr() or 1.0
        hwnd = int(self.winId())
        current = Win32Taskbar.rect(hwnd) if self.isVisible() else None
        height = max(32, int(round((rect[3] - rect[1]) / dpr)))

        # 如果任务栏图标已经长到我们窗口下面（或要求强制重排），先把自己挪出任务栏再扫描，
        # 否则既会遮住图标，背景截图里也会混进自己。
        if current is not None:
            gaps_now = Win32Taskbar.layout_gaps(rect)
            inside = any(
                gap_start - 2 <= current[0] and current[2] <= gap_end + 2
                for gap_start, gap_end in gaps_now
            )
            if not inside or self._force_clean_rescan:
                LOGGER.info(
                    "任务栏布局变化（图标可能已占用当前位置），先移出再重新扫描：current=%s gaps=%s",
                    current,
                    gaps_now,
                )
                try:
                    Win32Taskbar.move_topmost(
                        hwnd, -5000, current[1], current[2] - current[0], current[3] - current[1]
                    )
                    time.sleep(0.05)
                except Exception:  # noqa: BLE001
                    pass
                current = None
                self._force_clean_rescan = False

        # 已经贴合在同一个任务栏上：保持当前位置和宽度，不重新扫描（避免闪动与自身干扰）
        if (
            self._embedded
            and self._taskbar_rect_cache == tuple(rect)
            and current is not None
            and current[3] > rect[1]
            and current[1] < rect[3]
        ):
            width = max(240, int(round((current[2] - current[0]) / dpr)))
            return {
                "taskbar": taskbar,
                "rect": rect,
                "dpr": dpr,
                "left": int(round(rect[0] / dpr)),
                "top": int(round(rect[1] / dpr)),
                "width": int(round((rect[2] - rect[0]) / dpr)),
                "height": height,
                "widget_width": width,
                "x": int(round(current[0] / dpr)),
                "bg_image": self._bg_image,
            }

        exclude = None
        if current is not None and current[3] > rect[1] and current[1] < rect[3]:
            exclude = current
        x_phys, width_phys = self._resolve_placement(rect, exclude=exclude)
        target_left = int(x_phys)
        target_right = int(x_phys) + int(width_phys)
        covered = (
            current is not None
            and not (current[2] <= target_left or current[0] >= target_right)
        )
        if covered and self._bg_image is not None:
            bg_image = self._bg_image
        else:
            bg_image = self._sample_taskbar_background(rect, x_phys, width_phys)
        width = max(240, int(round(width_phys / dpr)))
        return {
            "taskbar": taskbar,
            "rect": rect,
            "dpr": dpr,
            "left": int(round(rect[0] / dpr)),
            "top": int(round(rect[1] / dpr)),
            "width": int(round((rect[2] - rect[0]) / dpr)),
            "height": height,
            "widget_width": width,
            "x": int(round(x_phys / dpr)),
            "bg_image": bg_image,
        }

    def _sample_taskbar_background(
        self, taskbar_rect: Tuple[int, int, int, int], x_phys: int, width_phys: int
    ) -> Optional[QImage]:
        """截取目标位置的任务栏背景，直接当作窗口底色绘制。

        这样窗口底色和任务栏（亚克力/渐变/壁纸）逐像素一致，看不出“贴了一块”。
        截取时机是窗口还没盖到该位置时，所以拿到的是真实任务栏背景。
        """
        captured = Win32Taskbar.capture_pixels(
            (int(x_phys), taskbar_rect[1], int(x_phys) + int(width_phys), taskbar_rect[3])
        )
        if captured is None:
            return None
        width, height, data = captured
        if width <= 4 or height <= 4:
            return None
        raw = bytearray(data)
        for index in range(3, len(raw), 4):
            raw[index] = 255  # GDI 截图 alpha 字节不可靠，统一置为不透明
        image = QImage(bytes(raw), width, height, width * 4, QImage.Format.Format_ARGB32)
        return image.copy()

    def apply_taskbar_mode(self) -> None:
        """按配置把窗口嵌入任务栏（embed）或置顶覆盖（overlay）。"""
        if self.selftest:
            return
        # 全屏隐藏/用户手动隐藏期间不要重排：move_* 里带 SWP_SHOWWINDOW，
        # 会把刚隐藏的窗口又显示出来（表现为“隐藏后又闪一下/一直闪”）
        if self._hidden_for_fullscreen or self._user_hidden:
            return
        target = self._taskbar_target()
        if target is None:
            self._fall_back_to_screen()
            return

        taskbar = int(target["taskbar"])
        dpr = float(target["dpr"])
        width = int(target["widget_width"])
        height = int(target["height"])
        self._bg_image = target.get("bg_image") or self._bg_image
        if self.width() != width or self.height() != height:
            self.resize(width, height)

        self._dark_taskbar = Win32Taskbar.dark_mode(taskbar)
        # 任务栏是半透明亚克力，颜色跟着壁纸走：优先采样实际背景亮度来决定文字颜色
        exclude = (
            int(round(int(target["x"]) * dpr)),
            int(round(int(target["top"]) * dpr)),
            int(round((int(target["x"]) + width) * dpr)),
            int(round((int(target["top"]) + height) * dpr)),
        )
        sampled_dark = Win32Taskbar.is_dark_background(tuple(target["rect"]), exclude)
        if sampled_dark is not None:
            self._dark_taskbar = sampled_dark
        hwnd = int(self.winId())
        mode = str(self.config.get("taskbar_mode", "embed"))
        embedded = False

        if mode != "overlay":
            already_embedded = self._embedded and Win32Taskbar.parent_of(hwnd) == taskbar
            if already_embedded or Win32Taskbar.embed(hwnd, taskbar):
                origin_x, origin_y = Win32Taskbar.client_origin(taskbar)
                rel_x = int(round(int(target["x"]) * dpr)) - origin_x
                rel_y = int(round(int(target["top"]) * dpr)) - origin_y
                Win32Taskbar.move_child(
                    hwnd, rel_x, rel_y, int(round(width * dpr)), int(round(height * dpr))
                )
                # 移动之后再校验父窗口（SetParent 后立刻查询不可靠）
                if Win32Taskbar.parent_of(hwnd) == taskbar:
                    embedded = True
                else:
                    LOGGER.warning(
                        "嵌入后父窗口校验失败（parent=%s taskbar=%s），退回覆盖模式",
                        Win32Taskbar.parent_of(hwnd),
                        taskbar,
                    )
                    Win32Taskbar.detach(hwnd)
            else:
                LOGGER.warning("嵌入任务栏失败，退回置顶覆盖模式（可右键切换）")

        if not embedded:
            if self._embedded:
                Win32Taskbar.detach(hwnd)
            Win32Taskbar.move_topmost(
                hwnd,
                int(round(int(target["x"]) * dpr)),
                int(round(int(target["top"]) * dpr)),
                int(round(width * dpr)),
                int(round(height * dpr)),
            )

        self._embedded = embedded
        self._taskbar_hwnd_cache = taskbar
        self._taskbar_rect_cache = tuple(target["rect"])  # type: ignore[assignment]
        LOGGER.info(
            "任务栏模式=%s，嵌入=%s，任务栏矩形=%s，窗口=%sx%s",
            mode,
            embedded,
            target["rect"],
            width,
            height,
        )
        if not self._dragging:
            # 位置由“启动时扫一次空位”决定；之后保持不动，避免反复挪动自己的窗口
            self._layout_retries = 0
        self.update()

    def _verify_placement(self) -> None:
        """保留接口但不再主动重排：反复移动嵌入窗口会让 DWM 停止合成它。"""
        return

    def _schedule_layout_verify(self) -> None:
        """兼容旧调用：不再延迟复查（原因见 _verify_placement）。"""
        return

    def _fall_back_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.geometry()
        width = max(240, int(self.config.get("width", DEFAULT_WIDTH)))
        height = max(32, self.height())
        Win32Taskbar.move_topmost(
            int(self.winId()), rect.left(), rect.bottom() - height - 4, width, height
        )
        self._embedded = False
        self.update()

    def snap_to_taskbar(self) -> None:
        """重新贴合任务栏（右键菜单/屏幕变化时调用）：强制重新截图背景并重新选空位。"""
        self._force_clean_rescan = True
        self._hidden_for_space = False
        self.apply_taskbar_mode()

    def reapply_taskbar_layout(self) -> None:
        """强制重新截图背景并重新选空位（右键菜单/主题变化时用）。"""
        self._force_clean_rescan = True
        self._hidden_for_space = False
        self.apply_taskbar_mode()

    def ensure_taskbar_position(self) -> None:
        """每 5 秒检查：任务栏句柄/矩形变化、图标占用、主题切换时自动重新贴合。"""
        self._refresh_taskbar_position()

    def _refresh_taskbar_position(self) -> None:
        if self.selftest:
            return
        # 全屏隐藏 / 用户手动隐藏期间什么都别做：
        # 下面的 move_topmost 会带 SWP_SHOWWINDOW，会把隐藏的窗口重新显示出来
        if self._hidden_for_fullscreen or self._user_hidden:
            return
        if not self.isVisible() and not self._hidden_for_space:
            return
        taskbar = Win32Taskbar.find()
        rect = Win32Taskbar.rect(taskbar) if taskbar else None
        if not taskbar or rect is None:
            return
        changed = (
            taskbar != self._taskbar_hwnd_cache
            or rect != self._taskbar_rect_cache
        )
        if changed and not self._dragging:
            LOGGER.info("检测到任务栏变化，重新贴合：hwnd=%s rect=%s", taskbar, rect)
            self._force_clean_rescan = True
            self.apply_taskbar_mode()
            return
        mode = str(self.config.get("taskbar_mode", "overlay"))
        if mode == "overlay":
            if self._dragging:
                return
            current = Win32Taskbar.rect(int(self.winId()))
            if current is None:
                return
            gaps = Win32Taskbar.layout_gaps(rect)
            has_space = any(gap_end - gap_start >= 220 for gap_start, gap_end in gaps)
            inside = any(
                gap_start - 2 <= current[0] and current[2] <= gap_end + 2
                for gap_start, gap_end in gaps
            )
            if not has_space:
                # 任务栏被窗口按钮占满：暂时隐藏，留给后面自动恢复
                if not self._hidden_for_space:
                    LOGGER.info("任务栏空位不足（图标占满），暂时隐藏组件")
                    self._hidden_for_space = True
                    self.hide()
                    if self._tray is not None:
                        self._tray.showMessage(
                            APP_TITLE,
                            "任务栏空位不足，歌词组件已暂时隐藏；关掉一些窗口后会自动恢复。",
                            QSystemTrayIcon.MessageIcon.Information,
                            4000,
                        )
                return
            if self._hidden_for_space:
                LOGGER.info("任务栏空位恢复，重新显示组件")
                self._hidden_for_space = False
                self.show()
                self._force_clean_rescan = True
                self.apply_taskbar_mode()
                return
            if not inside:
                LOGGER.info(
                    "任务栏图标已占用当前空位，自动重排：current=%s gaps=%s", current, gaps
                )
                self._force_clean_rescan = True
                self.apply_taskbar_mode()
                return
            # 空位变大时自动长回去（例如关掉一些窗口后）
            dpr = self._screen_dpr() or 1.0
            gap_size = 0
            for gap_start, gap_end in gaps:
                if gap_start - 2 <= current[0] and current[2] <= gap_end + 2:
                    gap_size = max(gap_size, gap_end - gap_start)
            configured = int(max(240, int(self.config.get("width", DEFAULT_WIDTH))) * dpr)
            want_width = int(max(240 * dpr, min(configured, gap_size - 16)))
            if want_width - (current[2] - current[0]) > 40:
                LOGGER.info(
                    "任务栏空位变大，自动扩展宽度：%s -> %s",
                    current[2] - current[0],
                    want_width,
                )
                self._force_clean_rescan = True
                self.apply_taskbar_mode()
                return
            if current[1] != rect[1] or current[3] != rect[3]:
                # 对齐到任务栏上下边缘（避免 1~2px 缝隙）
                Win32Taskbar.move_topmost(
                    int(self.winId()),
                    current[0],
                    rect[1],
                    current[2] - current[0],
                    rect[3] - rect[1],
                )
            dark = Win32Taskbar.dark_mode(taskbar)
            if dark != self._dark_taskbar:
                # 只更新记录，不要重新排版：以前这里会 reapply，导致每 5 秒把窗口
                # 挪出屏幕再放回来，表现为任务栏组件一直闪烁
                self._dark_taskbar = dark
                self.update()
            # 轻量重新置顶，防止被任务栏/其他置顶窗口压下去
            Win32Taskbar.move_topmost(
                int(self.winId()),
                current[0],
                current[1],
                current[2] - current[0],
                current[3] - current[1],
            )
            return
        if self._embedded and Win32Taskbar.parent_of(int(self.winId())) != taskbar:
            LOGGER.info("嵌入关系失效（explorer 可能重启过），重新嵌入")
            self._embedded = False
            self.apply_taskbar_mode()
            return
        dark = Win32Taskbar.dark_mode(taskbar)
        if dark != self._dark_taskbar:
            self._dark_taskbar = dark
            self.update()

    def init_timers(self) -> None:
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.tick_ui)
        self.ui_timer.start(250)

        self.remind_timer = QTimer(self)
        self.remind_timer.timeout.connect(self.check_reminders)
        self.remind_timer.start(1000)

        interval_ms = max(10, int(self.config.get("sync_sec", 30))) * 1000
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self._on_sync_timer)
        self.sync_timer.start(interval_ms)

        self.snap_timer = QTimer(self)
        self.snap_timer.timeout.connect(self.ensure_taskbar_position)
        self.snap_timer.start(5000)

        # 每日待办：每 30 秒检查一次“凌晨 2 点翻篇 / 22 点提醒填明天”
        self.daily_timer = QTimer(self)
        self.daily_timer.timeout.connect(self._check_daily_events)
        self.daily_timer.start(30000)

        # 全屏应用（B站/网页视频 F11、全屏游戏）时自动隐藏，退出后恢复
        self.fullscreen_timer = QTimer(self)
        self.fullscreen_timer.timeout.connect(self._check_fullscreen_state)
        self.fullscreen_timer.start(1000)

        # 账本同步（独立 Gist，30 秒一次）
        if not self.selftest:
            self.ledger_timer = QTimer(self)
            self.ledger_timer.timeout.connect(self.request_ledger_sync)
            self.ledger_timer.start(30000)

            # 每日记忆推送 / 每周周报邮件：每 30 秒检查一次时间点
            self.report_timer = QTimer(self)
            self.report_timer.timeout.connect(self._check_scheduled_jobs)
            self.report_timer.start(30000)

            QTimer.singleShot(2500, self._ensure_ledger_gist)
            QTimer.singleShot(4000, self.request_ledger_sync)
            QTimer.singleShot(5000, self._check_scheduled_jobs)
            QTimer.singleShot(7000, self._check_ai_queue)
            threading.Thread(
                target=self._refresh_holidays_worker, daemon=True, name="HolidayRefresh"
            ).start()

        try:
            app = QGuiApplication.instance()
            if app is not None:
                app.screenAdded.connect(lambda *_: self.snap_to_taskbar())
                app.screenRemoved.connect(lambda *_: self.snap_to_taskbar())
        except Exception:  # noqa: BLE001
            pass

    def start_media_watcher(self) -> None:
        # 0.5 秒一读，位置锚点更细，歌词切换更跟手
        self.media_watcher = MediaWatcher(self.bridge, interval=0.5)
        self.media_watcher.start()

    # ==================== 媒体与歌词 ====================
    def on_media_updated(self, info: Dict[str, Any]) -> None:
        self._media = info or None
        self._media_mono = time.monotonic()
        title = str((info or {}).get("title") or "").strip()
        artist = str((info or {}).get("artist") or "").strip()
        key = f"{title} - {artist}".strip(" -") if title else ""
        if key and key != self._song_key:
            self._song_key = key
            self._lyrics = []
            self._hot_comments = []
            self._lyrics_state = "loading"
            self._lyrics_error = ""
            self._fallback_start_mono = time.monotonic()
            self._rotation_anchor = time.monotonic()
            self._rotation_offset = 0
            self._comment_anchor = self._rotation_anchor
            self._comment_offset = 0
            self._request_lyrics(key, title, artist)
        elif not key and self._song_key:
            LOGGER.info("媒体状态清空（播放器可能已关闭）")
            self._song_key = ""
            self._lyrics = []
            self._hot_comments = []
            self._lyrics_state = "idle"
            self._media = None
        elif key and key == self._song_key and self._lyrics_state == "failed":
            # 网络抖动导致歌词拉取失败：同一首歌 60 秒后允许重试（不做永久负缓存）
            if time.monotonic() - self._lyrics_last_attempt > 60:
                self._lyrics_state = "loading"
                self._lyrics_error = ""
                self._request_lyrics(key, title, artist)
        self._update_estimated_timeline(info or {}, key)
        self.refresh_texts()

    def _update_estimated_timeline(self, info: Dict[str, Any], key: str) -> None:
        """维护“本地歌词时间轴”。

        网易云等播放器的 SMTC 时间轴经常一直返回 0 / 空值，如果每轮轮询都把锚点重置成 0，
        歌词就会卡住或跳回开头。这里只在“换歌 / 明显跳转 / 播放/暂停切换”时重新锚定，
        其余时间用单调时钟平滑推进。
        """
        now = time.monotonic()
        anchor = float(info.get("position", -1) or -1)
        playing = bool(info.get("is_playing"))

        if not key:
            self._est_song = ""
            self._est_base = 0.0
            self._est_mono = now
            self._est_playing = False
            self._est_anchor_seen = -1.0
            return

        if key != self._est_song:
            self._est_song = key
            self._est_base = max(0.0, anchor)
            self._est_mono = now
            self._est_playing = playing
            self._est_anchor_seen = anchor
            return

        if playing != self._est_playing:
            if not playing:
                # 暂停：把当前估算值冻结下来
                self._est_base = self._estimate_from_base(now)
            self._est_mono = now
            self._est_playing = playing
            self._est_anchor_seen = anchor
            return

        if not playing:
            return

        # 播放中：SMTC 位置有明显变化（或首次拿到有效进度）才重新锚定
        if anchor >= 0:
            estimate = self._estimate_from_base(now)
            if abs(anchor - self._est_anchor_seen) > 0.25 or (
                anchor > 0.5 and abs(anchor - estimate) > 0.5
            ):
                self._est_base = anchor
                self._est_mono = now
            self._est_anchor_seen = anchor

    def _request_lyrics(self, key: str, title: str, artist: str) -> None:
        if self._lyrics_request_key == key and self._lyrics_state == "loading":
            return
        self._lyrics_request_key = key
        self._lyrics_last_attempt = time.monotonic()
        LOGGER.info("拉取网易云数据：%s", key)
        threading.Thread(
            target=self._fetch_lyrics_worker,
            args=(key, title, artist),
            daemon=True,
            name="NeteaseFetch",
        ).start()

    def _fetch_lyrics_worker(self, key: str, title: str, artist: str) -> None:
        try:
            bundle = self.netease.fetch_song_bundle(title, artist)
        except Exception as exc:  # noqa: BLE001
            bundle = {"ok": False, "error": str(exc), "lyrics": [], "hot_comments": []}
        self.bridge.lyrics_ready.emit(key, bundle)

    def on_lyrics_ready(self, key: str, bundle: Dict[str, Any]) -> None:
        if key != self._song_key:
            return  # 已经切歌，丢弃过期结果
        self._lyrics = [(float(stamp), str(text)) for stamp, text in bundle.get("lyrics", [])]
        self._hot_comments = [str(item) for item in bundle.get("hot_comments", [])]
        if bundle.get("ok"):
            self._lyrics_state = "ok"
            self._lyrics_error = ""
        else:
            self._lyrics_state = "failed"
            self._lyrics_error = str(bundle.get("error") or "未知错误")
            LOGGER.warning("歌词/热评不可用：%s（%s）", key, self._lyrics_error)
        self.refresh_texts()

    def _estimate_from_base(self, mono: float) -> float:
        return max(0.0, self._est_base + max(0.0, mono - self._est_mono))

    def _estimated_position(self) -> float:
        offset = float(self.config.get("lyric_offset_ms", 0) or 0) / 1000.0
        if self._est_song:
            if self._est_playing:
                return self._estimate_from_base(time.monotonic()) + offset
            return max(0.0, self._est_base + offset)
        if self._media is None:
            return max(0.0, offset)
        anchor = float(self._media.get("position", -1) or -1)
        if anchor >= 0:
            return max(0.0, anchor + offset)
        return max(0.0, offset)

    # ==================== 轮播与文案 ====================
    def _rotation_step(self) -> int:
        rotate = max(ROTATE_MIN_SECONDS, float(self.config.get("rotate_sec", 8)))
        return self._rotation_offset + int((time.monotonic() - self._rotation_anchor) / rotate)

    def _comment_line(self) -> str:
        """当前要展示的“有意思的热评”。"""
        if not self._hot_comments:
            return "💬 暂无有意思的热评"
        rotate = max(10.0, float(self.config.get("comment_rotate_sec", 20)))
        index = int((time.monotonic() - self._comment_anchor) / rotate) + self._comment_offset
        return self._hot_comments[index % len(self._hot_comments)]

    def _next_todo_line(self) -> str:
        """稳定显示“下一项待办”：只算今天的，今天没有就退回最早的逾期项。"""
        today = core.current_day()
        todo = core.next_todo_for_day(self.doc, today)
        if todo is None:
            return "📋 今天没有待办 · 22:00 提醒你填明天的"
        if core.todo_day(todo) == today:
            return core.format_next_todo_line(
                todo, len(core.todos_for_day(self.doc, today)), doc=self.doc
            )
        return core.format_next_todo_line(
            todo, len(core.overdue_todos(self.doc, today)), lead="逾期", doc=self.doc
        )

    def _fill_hint_line(self) -> str:
        """22:00–01:59 且明天待办还没填时，副行显示的提醒文案。"""
        if not bool(self.config.get("daily_fill_reminder", True)):
            return ""
        moment = core.now_local()
        if not core.fill_reminder_needed(self.doc, moment):
            return ""
        target = core.target_day(moment)
        return f"📝 22:00 了，去手机填明天的待办（{target.strftime('%m-%d')}）"

    def _current_alert(self) -> Optional[Dict[str, Any]]:
        if not self._current_alert_id:
            return None
        for item in self.doc["todos"]:
            if item["id"] == self._current_alert_id:
                return item if not item["done"] else None
        return None

    def _compose_texts(self) -> Tuple[str, str, bool]:
        alert = self._current_alert()
        if alert is not None:
            remaining = max(0, len(core.pending_todos(self.doc)) - 1)
            return (
                f"⏰ 该做：{alert['text']}",
                f"左键完成 · 之后还有 {remaining} 项待办",
                True,
            )

        pending = core.pending_todos(self.doc)
        step = self._rotation_step()
        comment = self._comment_line()
        todo_line = self._next_todo_line()
        playing = bool(self._song_key) and bool(
            self._media and self._media.get("is_playing")
        )
        song_label = self._song_key or "未检测到正在播放的网易云音乐"

        if self._lyrics_state == "loading" and playing and not self._lyrics:
            lyric = "🎵 正在获取歌词与热评…"
        elif self._lyrics and playing:
            line = core.current_lyric(self._lyrics, self._estimated_position())
            lyric = f"🎶 {line}" if line else f"🎵 {song_label}"
        else:
            lyric = f"🎵 {song_label}"

        mode = str(self.config.get("mode", "auto"))
        fill_hint = self._fill_hint_line()
        if mode == "lyrics":
            return lyric, f"🎶 逐行歌词 · {song_label}", False
        if mode == "comments":
            return comment, f"💬 有意思的热评 · {song_label}", False
        if mode == "todos":
            if fill_hint:
                return todo_line, fill_hint, False
            return todo_line, f"📋 下一项待办 · 共 {len(pending)} 项未完成", False

        # auto：播放时主行歌词、副行在「有意思的热评」与「下一项待办」之间切换；
        # 没播放时主行直接显示下一项待办。22:00–2:00 且明天还没填时，副行优先显示填写提醒。
        if playing:
            if fill_hint:
                return lyric, fill_hint, False
            secondary = comment if step % 2 == 0 else todo_line
            return lyric, secondary, False
        if fill_hint:
            return todo_line, fill_hint, False
        return todo_line, comment, False

    def refresh_texts(self) -> None:
        primary, secondary, alerting = self._compose_texts()
        signature = (primary, secondary, alerting)
        changed = signature != self._text_signature
        self._primary_text = primary
        self._secondary_text = secondary
        self._alerting = alerting
        if changed:
            self._text_signature = signature
            self._update_tray_tooltip()
            try:
                _atomic_write_json(
                    DEBUG_TEXT_PATH,
                    {
                        "time": core.to_iso(),
                        "primary": primary,
                        "secondary": secondary,
                        "alerting": alerting,
                        "embedded": self._embedded,
                        "paint_count": self._paint_count,
                        "window_rect": Win32Taskbar.rect(int(self.winId())),
                    },
                )
            except OSError:
                pass
        if changed or alerting:
            self.update()
            # 说明：RedrawWindow(RDW_UPDATENOW) 会打乱 Qt 对分层窗口的合成，实测会导致任务栏里
            # 不再显示文字；默认关闭，只有在排查重绘问题时才通过环境变量打开。
            if self._embedded and os.environ.get("TASKBAR_FORCE_REDRAW") == "1":
                Win32Taskbar.force_redraw(int(self.winId()))

    def tick_ui(self) -> None:
        self._check_lyrics_timeout()
        self.refresh_texts()

    def _check_lyrics_timeout(self) -> None:
        """歌词拉取超过 25 秒还没结果就降级显示，避免一直停在“正在获取歌词…”。"""
        if self._lyrics_state != "loading":
            return
        if self._lyrics_request_key != self._song_key:
            return
        if time.monotonic() - self._lyrics_last_attempt < 25:
            return
        self._lyrics_state = "failed"
        self._lyrics_error = "拉取超时"
        LOGGER.warning("歌词/热评拉取超时（25 秒），先降级显示，稍后自动重试")

    # ==================== 每日待办节奏 ====================
    def _check_daily_events(self) -> None:
        """每 30 秒检查：凌晨 2:00 自动翻篇、22:00 提醒填写明天的待办。"""
        moment = core.now_local()
        today = core.current_day(moment)

        if self._last_daily_day != today:
            LOGGER.info(
                "每日翻篇：%s -> %s（今日待办 %s 项）",
                self._last_daily_day,
                today,
                len(core.todos_for_day(self.doc, today)),
            )
            self._last_daily_day = today
            # 翻篇后立刻同步一次，避免刚过 2 点还没拉到手机新增的待办
            self.request_sync()
            self.refresh_texts()

        if not bool(self.config.get("daily_fill_reminder", True)):
            return
        if not core.fill_reminder_needed(self.doc, moment):
            return
        remind_key = today.isoformat()
        if str(self.config.get("last_fill_reminded") or "") == remind_key:
            return

        self.config["last_fill_reminded"] = remind_key
        save_config(self.config)
        tomorrow = core.target_day(moment)
        message = (
            f"明天的待办还没填（{tomorrow.strftime('%m-%d')}），"
            "打开手机上的待办页面填一下吧～"
        )
        LOGGER.info("发送每日填写提醒：%s", message)
        if self._tray is not None:
            self._tray.showMessage(
                "📝 待办提醒",
                message,
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
        self.refresh_texts()

    def toggle_daily_fill_reminder(self) -> None:
        enabled = not bool(self.config.get("daily_fill_reminder", True))
        self.config["daily_fill_reminder"] = enabled
        save_config(self.config)
        LOGGER.info("每日 22:00 填写提醒：%s", "开" if enabled else "关")
        if self._tray is not None:
            self._tray.showMessage(
                APP_TITLE,
                "已开启：每天 22:00 提醒填明天的待办" if enabled else "已关闭每日填写提醒",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        self.refresh_texts()

    # ==================== 全屏应用自动隐藏 ====================
    def _taskbar_covered_by_other(self) -> bool:
        """任务栏那一行是否被别的窗口盖住（B 站网页全屏/客户端全屏/自动隐藏时）。

        取任务栏里两个不被自己遮住的点，看命中的窗口是不是任务栏本身；
        不是就说明任务栏被盖住/藏起来了，此时任务栏组件也应该隐藏。
        """
        taskbar = Win32Taskbar.find()
        taskbar_rect = Win32Taskbar.rect(taskbar) if taskbar else None
        if not taskbar or taskbar_rect is None:
            return False
        current = Win32Taskbar.rect(int(self.winId()))
        middle_y = (taskbar_rect[1] + taskbar_rect[3]) // 2
        candidates = [
            (taskbar_rect[2] - 40, middle_y),   # 托盘附近
            (taskbar_rect[0] + 30, middle_y),   # 最左侧
        ]
        checked = 0
        for x, y in candidates:
            if current is not None and (
                current[0] - 4 <= x <= current[2] + 4
                and current[1] - 4 <= y <= current[3] + 4
            ):
                continue  # 这个点被自己挡着，换一个
            checked += 1
            if not Win32Taskbar.is_point_on_taskbar(x, y):
                return True
        return False

    def _check_fullscreen_state(self) -> None:
        """B 站/网页视频全屏（F11）或全屏游戏时自动隐藏，退出全屏后恢复。

        判据：①前台窗口覆盖整个显示器；②任务栏那一行被别的窗口盖住/藏起来。
        带防抖：连续 2 秒确认才隐藏，连续 3 秒不满足才恢复，避免一闪一闪。
        """
        if self.selftest or self._dragging:
            return
        if not bool(self.config.get("hide_on_fullscreen", True)):
            self._fullscreen_true_streak = 0
            self._fullscreen_false_streak = 0
            if self._hidden_for_fullscreen:
                self._restore_from_fullscreen()
            return

        fg_fullscreen = Win32Taskbar.is_foreground_fullscreen(int(self.winId()))
        taskbar_covered = False if fg_fullscreen else self._taskbar_covered_by_other()
        primary_fullscreen = (
            False
            if (fg_fullscreen or taskbar_covered)
            else Win32Taskbar.has_primary_fullscreen_window(int(self.winId()))
        )
        debug = (fg_fullscreen, taskbar_covered, primary_fullscreen)
        force_debug = os.environ.get("TASKBAR_FS_DEBUG") == "1"
        if force_debug or debug != getattr(self, "_last_fs_debug", None):
            self._last_fs_debug = debug
            LOGGER.info(
                "全屏检测：前台全屏=%s 主任务栏被覆盖=%s 主屏有全屏窗口=%s（定时器=%s）",
                fg_fullscreen,
                taskbar_covered,
                primary_fullscreen,
                self.fullscreen_timer.isActive() if hasattr(self, "fullscreen_timer") else "无",
            )
        fullscreen = fg_fullscreen or taskbar_covered or primary_fullscreen
        if fullscreen:
            self._fullscreen_true_streak += 1
            self._fullscreen_false_streak = 0
        else:
            self._fullscreen_false_streak += 1
            self._fullscreen_true_streak = 0

        if self._fullscreen_true_streak >= 2 and not self._hidden_for_fullscreen:
            LOGGER.info("检测到全屏/任务栏被覆盖，暂时隐藏任务栏组件")
            self._hidden_for_fullscreen = True
            if not self._user_hidden:
                self.hide()
        elif self._fullscreen_false_streak >= 3 and self._hidden_for_fullscreen:
            self._restore_from_fullscreen()

    def _restore_from_fullscreen(self) -> None:
        self._hidden_for_fullscreen = False
        self._fullscreen_true_streak = 0
        self._fullscreen_false_streak = 0
        if self._user_hidden:
            return
        LOGGER.info("退出全屏，恢复任务栏组件")
        self.show()
        self._force_clean_rescan = True
        self.apply_taskbar_mode()
        self.refresh_texts()

    def toggle_hide_on_fullscreen(self) -> None:
        enabled = not bool(self.config.get("hide_on_fullscreen", True))
        self.config["hide_on_fullscreen"] = enabled
        save_config(self.config)
        LOGGER.info("全屏应用时自动隐藏：%s", "开" if enabled else "关")
        if not enabled:
            self._restore_from_fullscreen()
        self.refresh_texts()

    def toggle_github_direct(self) -> None:
        enabled = not bool(self.config.get("github_direct", True))
        self.config["github_direct"] = enabled
        save_config(self.config)
        if enabled:
            _add_no_proxy_hosts(("api.github.com",))
        else:
            _remove_no_proxy_hosts(("api.github.com",))
        LOGGER.info("GitHub API 直连（绕过代理）：%s", "开" if enabled else "关")
        if self._tray is not None:
            self._tray.showMessage(
                APP_TITLE,
                "已开启：api.github.com 直连，不再受代理抖动影响"
                if enabled
                else "已关闭：GitHub 同步重新走系统代理",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        self.request_sync()

    def adjust_lyric_offset(self, delta_ms: int) -> None:
        current = int(self.config.get("lyric_offset_ms", 0) or 0)
        value = max(-3000, min(3000, current + int(delta_ms)))
        self.config["lyric_offset_ms"] = value
        save_config(self.config)
        LOGGER.info("歌词同步偏移：%+d ms", value)
        if self._tray is not None:
            self._tray.showMessage(
                APP_TITLE,
                f"歌词同步偏移 {value / 1000:+.1f}s（正数=歌词更早）",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        self.refresh_texts()

    def reset_lyric_offset(self) -> None:
        self.config["lyric_offset_ms"] = 0
        save_config(self.config)
        LOGGER.info("歌词同步偏移已重置为 0 ms")
        if self._tray is not None:
            self._tray.showMessage(
                APP_TITLE,
                "歌词同步偏移已重置（0s）",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        self.refresh_texts()

    def _update_tray_tooltip(self) -> None:
        if self._tray is None:
            return
        pending = len(core.pending_todos(self.doc))
        extra = f" · 最近同步 {self.last_sync[11:16]}" if len(self.last_sync) >= 16 else ""
        self._tray.setToolTip(f"{APP_TITLE}\n未完成 {pending} 条{extra}")

    # ==================== 提醒 ====================
    def check_reminders(self) -> None:
        # 清理已经完成/删除的当前提醒
        if self._current_alert_id:
            current = self._current_alert()
            if current is None:
                self._current_alert_id = None
        due = core.upcoming_reminders(self.doc)
        known = {item["id"] for item in due}
        for todo_id in list(self._alert_queue):
            if todo_id not in known:
                self._alert_queue.remove(todo_id)
        for item in due:
            if item["id"] != self._current_alert_id and item["id"] not in self._alert_queue:
                self._alert_queue.append(item["id"])
        if self._current_alert_id is None and self._alert_queue:
            self._activate_next_alert()
        self.refresh_texts()

    def _activate_next_alert(self) -> None:
        while self._alert_queue:
            todo_id = self._alert_queue.pop(0)
            item = next((t for t in self.doc["todos"] if t["id"] == todo_id and not t["done"]), None)
            if item is None:
                continue
            self._current_alert_id = todo_id
            # 标记“已提醒”并同步到云端，手机端可看到状态；重启也不会重复提醒
            self.doc = core.mark_notified(self.doc, todo_id)
            self._persist_and_sync()
            LOGGER.info("触发到点提醒：%s", item["text"])
            return
        self._current_alert_id = None

    def complete_alert(self) -> None:
        todo_id = self._current_alert_id
        if not todo_id:
            return
        item = next((todo for todo in self.doc["todos"] if todo["id"] == todo_id), None)
        if item is None:
            self._current_alert_id = None
            self._activate_next_alert()
            return
        dialog = CompletionDialog(self, str(item.get("text") or ""))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.doc = core.set_done(
            self.doc, todo_id, True, values["done_note"], values["done_reflection"]
        )
        self._record_completion(todo_id, values["done_note"], values["done_reflection"])
        self._current_alert_id = None
        self._persist_and_sync()
        self._activate_next_alert()
        self.refresh_texts()

    def snooze_alert(self, minutes: int = 10) -> None:
        todo_id = self._current_alert_id
        if not todo_id:
            return
        self.doc = core.snooze_todo(self.doc, todo_id, minutes)
        self._current_alert_id = None
        self._persist_and_sync()
        self._activate_next_alert()
        self.refresh_texts()

    def dismiss_alert(self) -> None:
        self._current_alert_id = None
        self._activate_next_alert()
        self.refresh_texts()

    # ==================== 云同步 ====================
    def _persist_and_sync(self) -> None:
        save_state(self.doc, self.etag, self.last_sync, self.last_error)
        self.request_sync()

    def _on_sync_timer(self) -> None:
        self.request_sync()

    def request_sync(self) -> None:
        if self.store is None or not self.store.configured:
            return
        if self._sync_running:
            return
        self._sync_running = True
        snapshot = json.loads(json.dumps(self.doc))
        etag = self.etag
        self._sync_thread = threading.Thread(
            target=self._sync_worker, args=(snapshot, etag), daemon=True, name="GistSync"
        )
        self._sync_thread.start()

    def _sync_worker(self, local_doc: Dict[str, Any], etag: Optional[str]) -> None:
        assert self.store is not None
        attempt = 0
        while True:
            attempt += 1
            try:
                report = self.store.sync(local_doc, etag)
                self.bridge.sync_finished.emit(
                    {
                        "ok": True,
                        "status": report.status,
                        "doc": report.doc,
                        "remote": report.remote,
                        "pushed": report.pushed,
                        "etag": report.etag,
                        "error": "",
                    }
                )
                return
            except core.GitHubApiError as exc:
                self.bridge.sync_finished.emit(
                    {"ok": False, "status": "error", "error": str(exc), "code": exc.status}
                )
                return
            except requests.RequestException as exc:
                # 代理抖动/网络瞬断：同一轮里立刻重试一次，再失败才报错等下一轮
                if attempt < 2:
                    LOGGER.info("云同步网络错误，2 秒后重试一次：%s", exc)
                    time.sleep(2)
                    continue
                self.bridge.sync_finished.emit(
                    {"ok": False, "status": "error", "error": f"网络错误：{exc}", "code": None}
                )
                return
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("同步线程异常")
                self.bridge.sync_finished.emit(
                    {"ok": False, "status": "error", "error": str(exc), "code": None}
                )
                return

    def on_sync_finished(self, result: Dict[str, Any]) -> None:
        self._sync_running = False
        if not result.get("ok"):
            self.last_error = str(result.get("error") or "同步失败")
            save_state(self.doc, self.etag, self.last_sync, self.last_error)
            if self.last_error != self._last_notified_error:
                LOGGER.warning("云同步失败：%s", self.last_error)
                self._last_notified_error = self.last_error
                if self._tray is not None:
                    self._tray.showMessage(APP_TITLE, self.last_error, QSystemTrayIcon.MessageIcon.Warning, 4000)
            return

        remote = core.normalize_doc(result.get("doc"))
        # 同步期间用户可能又点了完成/稍后：用 merge 把本地新改动并回来
        merged = core.merge_docs(remote, self.doc)
        changed_locally = core.canonical(merged) != core.canonical(remote)
        self.doc = merged
        self.etag = result.get("etag") or self.etag
        self.last_sync = core.to_iso()
        self.last_error = ""
        self._last_notified_error = ""
        save_state(self.doc, self.etag, self.last_sync, self.last_error)
        self._sync_archive_from_doc()
        self.refresh_texts()
        self.check_reminders()
        self._update_tray_tooltip()
        if changed_locally:
            QTimer.singleShot(2500, self.request_sync)

    # ==================== 定时/位置/绘制 ====================
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._paint_count += 1
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        width, height = self.width(), self.height()

        if self._alerting:
            # 到点提醒：红底呼吸，文字保持白色
            phase = 0.5 + 0.5 * math.sin(time.monotonic() * math.pi * 1.2)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, width - 1.0, height - 1.0), 8.0, 8.0)
            painter.fillPath(path, QColor(190, 18, 60, int(165 + 70 * phase)))
        elif self._bg_image is not None:
            # 直接把“该位置的任务栏背景截图”画成底色：亚克力/渐变/壁纸逐像素一致，
            # 看起来就是任务栏本身；同时窗口不透明，整块区域都能接收鼠标点击。
            painter.drawImage(QRectF(0.0, 0.0, float(width), float(height)), self._bg_image)
        else:
            painter.fillRect(0, 0, width, height, QColor(20, 22, 26, 255))

        white = QColor(255, 255, 255)
        available = max(40, width - 24)
        main_baseline = int(height * 0.50)
        sub_baseline = int(height * 0.85)

        # 纯白文字，不加阴影
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
        metrics = painter.fontMetrics()
        main_text = metrics.elidedText(self._primary_text, Qt.TextElideMode.ElideRight, available)
        painter.setPen(white)
        painter.drawText(12, main_baseline, main_text)

        painter.setFont(QFont("Microsoft YaHei UI", 7))
        metrics = painter.fontMetrics()
        sub_text = metrics.elidedText(self._secondary_text, Qt.TextElideMode.ElideRight, available)
        painter.setPen(white)
        painter.drawText(12, sub_baseline, sub_text)

    # ==================== 鼠标交互 ====================
    def _global_window_top_left(self) -> Tuple[int, int]:
        hwnd = int(self.winId())
        rect = Win32Taskbar.rect(hwnd)
        if rect is not None:
            dpr = self._screen_dpr() or 1.0
            return int(round(rect[0] / dpr)), int(round(rect[1] / dpr))
        point = self.mapToGlobal(self.rect().topLeft())
        return point.x(), point.y()

    def _move_window_to_global(self, gx: int, gy: int) -> None:
        hwnd = int(self.winId())
        dpr = self._screen_dpr() or 1.0
        width_phys = int(round(self.width() * dpr))
        height_phys = int(round(self.height() * dpr))
        taskbar = Win32Taskbar.find()
        if self._embedded and taskbar:
            origin_x, origin_y = Win32Taskbar.client_origin(taskbar)
            Win32Taskbar.move_child(
                hwnd,
                int(round(gx * dpr)) - origin_x,
                int(round(gy * dpr)) - origin_y,
                width_phys,
                height_phys,
            )
        else:
            Win32Taskbar.move_topmost(
                hwnd, int(round(gx * dpr)), int(round(gy * dpr)), width_phys, height_phys
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._current_alert_id:
                self.complete_alert()
                event.accept()
                return
            cursor = event.globalPosition().toPoint()
            gx, gy = self._global_window_top_left()
            self._drag_offset = (cursor.x() - gx, cursor.y() - gy)
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            cursor = event.globalPosition().toPoint()
            gx = cursor.x() - self._drag_offset[0]
            gy = cursor.y() - self._drag_offset[1]
            old_x, old_y = self._global_window_top_left()
            if abs(gx - old_x) + abs(gy - old_y) > 3:
                self._dragging = True
            self._move_window_to_global(gx, gy)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            if self._dragging:
                self._save_offset_ratio()
            self._drag_offset = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _save_offset_ratio(self) -> None:
        taskbar = Win32Taskbar.find()
        rect = Win32Taskbar.rect(taskbar) if taskbar else None
        current = Win32Taskbar.rect(int(self.winId()))
        if rect is None or current is None:
            return
        dpr = self._screen_dpr() or 1.0
        screen_x = int(round(current[0] / dpr))
        ratio = (screen_x - int(round(rect[0] / dpr))) / max(
            1, int(round((rect[2] - rect[0]) / dpr))
        )
        self.config["offset_ratio"] = round(min(0.95, max(0.0, ratio)), 4)
        self.config["auto_anchor"] = False
        save_config(self.config)

    def wheelEvent(self, event) -> None:  # noqa: N802
        self._comment_offset += -1 if event.angleDelta().y() > 0 else 1
        self.refresh_texts()
        event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.selftest:
            event.accept()
            return
        if self._tray is not None:
            # 有托盘时关闭窗口 = 隐藏到托盘，继续在任务栏常驻
            event.ignore()
            self._user_hidden = True
            self.hide()
            return
        # 没有托盘图标时，关闭窗口就是退出，避免留下看不见的“僵尸进程”
        event.accept()
        QApplication.quit()

    # ==================== 菜单 / 托盘 / 设置 ====================
    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        if self._current_alert_id:
            menu.addAction("✅ 完成这条待办", self.complete_alert)
            menu.addAction("⏰ 10 分钟后再提醒", lambda: self.snooze_alert(10))
            menu.addAction("👌 本次忽略", self.dismiss_alert)
            menu.addSeparator()

        quick_menu = menu.addMenu("➕ 待办 / 记账 / 周报 / 记忆")
        quick_menu.addAction("📅 长期任务规划（AI 排期）…", self.long_term_plan_dialog)
        parents = self._plan_parents()
        if parents:
            update_menu = quick_menu.addMenu("🔄 更新任务规划（AI 重排）…")
            for info in parents[:10]:
                update_menu.addAction(
                    f"🔄 {str(info['parent_text'])[:18]}（{info['done_count']}/{info['total']}）",
                    lambda _checked=False, pid=info["parent_id"]: self.update_plan_dialog(pid),
                )
        quick_menu.addAction("➕ 添加待办…", self.add_todo_dialog)
        quick_menu.addAction("🤖 AI 拆解复杂任务…", self.ai_plan_dialog)
        if self.ai_queue:
            queue_menu = quick_menu.addMenu(f"⏳ AI 排队任务（{len(self.ai_queue)}）")
            for item in self.ai_queue[:6]:
                queue_menu.addAction(
                    f"❌ 取消：{str(item.get('text') or '')[:18]}",
                    lambda _checked=False, qid=item.get("id"): self.cancel_ai_queue_item(qid),
                )
            queue_menu.addSeparator()
            queue_menu.addAction("立即检查（仅空闲时段运行）", self._check_ai_queue)
        quick_menu.addAction("💰 记一笔 / 本周账本…", self.ledger_dialog)
        quick_menu.addAction("📊 周报预览…", self.show_weekly_report)
        quick_menu.addAction("📤 立即发送周报", self.send_weekly_report_thread)
        quick_menu.addAction("🧠 立即推送今日记忆", self.push_memory_thread)
        quick_menu.addSeparator()
        quick_menu.addAction("⚙️ AI / 邮件 / 记忆设置…", self.open_advanced_settings)
        menu.addSeparator()

        mode_menu = menu.addMenu("🔀 显示模式")
        group = QActionGroup(mode_menu)
        group.setExclusive(True)
        for code, label in (
            ("auto", "✨ 智能混排（歌词 + 热评 + 待办）"),
            ("lyrics", "🎶 只看逐行歌词"),
            ("comments", "💬 只看高赞热评"),
            ("todos", "📋 只看手机待办"),
        ):
            action = QAction(label, mode_menu)
            action.setCheckable(True)
            action.setChecked(str(self.config.get("mode", "auto")) == code)
            action.triggered.connect(lambda _checked, c=code: self.set_mode(c))
            group.addAction(action)
            mode_menu.addAction(action)

        pending = core.pending_todos(self.doc)
        taskbar_menu = menu.addMenu("🧲 任务栏集成")
        taskbar_group = QActionGroup(taskbar_menu)
        taskbar_group.setExclusive(True)
        for code, label in (
            ("overlay", "置顶覆盖（默认，显示稳定）"),
            ("embed", "嵌入任务栏（实验性，外观更原生）"),
        ):
            taskbar_action = QAction(label, taskbar_menu)
            taskbar_action.setCheckable(True)
            taskbar_action.setChecked(str(self.config.get("taskbar_mode", "embed")) == code)
            taskbar_action.triggered.connect(lambda _checked, c=code: self.set_taskbar_mode(c))
            taskbar_group.addAction(taskbar_action)
            taskbar_menu.addAction(taskbar_action)
        anchor_action = QAction("自动吸附到托盘左侧空位", taskbar_menu)
        anchor_action.setCheckable(True)
        anchor_action.setChecked(bool(self.config.get("auto_anchor", True)))
        anchor_action.triggered.connect(self.toggle_auto_anchor)
        taskbar_menu.addAction(anchor_action)
        taskbar_menu.addSeparator()
        taskbar_menu.addAction("📌 立即重新贴合", self.reapply_taskbar_layout)
        taskbar_menu.addAction("🩺 任务栏诊断", self.show_taskbar_diagnose)

        todo_menu = menu.addMenu(f"📋 未完成待办（{len(pending)}）")
        if not pending:
            empty = todo_menu.addAction("🎉 暂无未完成待办")
            empty.setEnabled(False)
        else:
            for item in pending[:15]:
                label = core.format_todo_line(item, doc=self.doc)
                action = QAction(f"⬜ {label}", todo_menu)
                action.triggered.connect(lambda _checked, tid=item["id"]: self.complete_todo(tid))
                todo_menu.addAction(action)

        menu.addSeparator()
        daily_action = QAction("⏰ 每天 22:00 提醒填待办", menu)
        daily_action.setCheckable(True)
        daily_action.setChecked(bool(self.config.get("daily_fill_reminder", True)))
        daily_action.triggered.connect(self.toggle_daily_fill_reminder)
        menu.addAction(daily_action)
        fullscreen_action = QAction("🖥️ 全屏应用时自动隐藏", menu)
        fullscreen_action.setCheckable(True)
        fullscreen_action.setChecked(bool(self.config.get("hide_on_fullscreen", True)))
        fullscreen_action.triggered.connect(self.toggle_hide_on_fullscreen)
        menu.addAction(fullscreen_action)
        direct_action = QAction("🌐 GitHub API 直连（绕过代理）", menu)
        direct_action.setCheckable(True)
        direct_action.setChecked(bool(self.config.get("github_direct", True)))
        direct_action.triggered.connect(self.toggle_github_direct)
        menu.addAction(direct_action)
        offset_ms = int(self.config.get("lyric_offset_ms", 0) or 0)
        lyric_menu = menu.addMenu(f"🎶 歌词同步微调（{offset_ms / 1000:+.1f}s）")
        lyric_menu.addAction("歌词更早 0.5s", lambda: self.adjust_lyric_offset(500))
        lyric_menu.addAction("歌词更早 0.2s", lambda: self.adjust_lyric_offset(200))
        lyric_menu.addAction("歌词更晚 0.2s", lambda: self.adjust_lyric_offset(-200))
        lyric_menu.addAction("歌词更晚 0.5s", lambda: self.adjust_lyric_offset(-500))
        lyric_menu.addSeparator()
        lyric_menu.addAction("重置偏移（0s）", self.reset_lyric_offset)
        menu.addSeparator()
        menu.addAction("🔄 立即同步", self.request_sync)
        menu.addAction("⚙️ 配置 GitHub Gist 云同步", self.open_settings)
        menu.addAction("📌 重新贴合任务栏", self.reapply_taskbar_layout)
        menu.addAction("🗂️ 打开数据目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR))))
        menu.addAction("ℹ️ 关于", self.show_about)
        menu.addSeparator()
        menu.addAction("退出程序", QApplication.quit)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._build_menu().exec(event.globalPos())

    def set_taskbar_mode(self, mode: str) -> None:
        if mode not in ("embed", "overlay"):
            return
        self.config["taskbar_mode"] = mode
        save_config(self.config)
        LOGGER.info("切换任务栏集成模式：%s", mode)
        self.apply_taskbar_mode()
        self.refresh_texts()

    def toggle_auto_anchor(self) -> None:
        enabled = not bool(self.config.get("auto_anchor", True))
        self.config["auto_anchor"] = enabled
        if enabled:
            self.config["offset_ratio"] = 0.0
        save_config(self.config)
        self.apply_taskbar_mode()

    def show_taskbar_diagnose(self) -> None:
        """把任务栏关键信息弹窗显示出来，方便反馈“没嵌进去/位置不对”的问题。"""
        try:
            import diagnose_taskbar

            info = diagnose_taskbar.collect()
            taskbar_info = info.get("taskbar") or {}
            lines = [
                f"Windows：{info.get('platform')}（build {info.get('windows_version')}）",
                f"任务栏句柄：0x{info.get('taskbar_hwnd', 0):X}",
                f"任务栏矩形：{taskbar_info.get('rect')}",
                f"任务栏 DPI：{info.get('taskbar_dpi')}  暗色任务栏：{info.get('taskbar_dark')}",
                "",
                f"配置的集成模式：{self.config.get('taskbar_mode')}",
                f"当前实际嵌入：{self._embedded}",
                f"窗口父句柄：0x{Win32Taskbar.parent_of(int(self.winId())):X}",
                f"任务栏句柄应为：0x{Win32Taskbar.find():X}",
                "",
                f"日志：{LOG_PATH}",
            ]
            QMessageBox.information(self, "任务栏诊断", "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "诊断失败", str(exc))

    def set_mode(self, mode: str) -> None:
        if mode not in ("auto", "lyrics", "comments", "todos"):
            return
        self.config["mode"] = mode
        save_config(self.config)
        self.refresh_texts()

    def complete_todo(self, todo_id: str) -> None:
        item = next((todo for todo in self.doc["todos"] if todo["id"] == todo_id), None)
        if item is None:
            return
        dialog = CompletionDialog(self, str(item.get("text") or ""))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.doc = core.set_done(
            self.doc, todo_id, True, values["done_note"], values["done_reflection"]
        )
        self._record_completion(todo_id, values["done_note"], values["done_reflection"])
        if self._current_alert_id == todo_id:
            self._current_alert_id = None
            self._activate_next_alert()
        self._persist_and_sync()
        self.refresh_texts()

    def _sync_archive_from_doc(self) -> None:
        """把已完成的待办补进归档；手机端完成的也会在这里被记录。"""
        try:
            self.archive, added = task_archive.sync_completed_from_doc(self.archive, self.doc)
            if added:
                self.archive = task_archive.prune(self.archive)
                task_archive.save_archive(ARCHIVE_PATH, self.archive)
                LOGGER.info("完成归档新增 %d 条（来自同步/历史）", added)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("同步完成归档失败：%s", exc)

    def _record_completion(self, todo_id: str, done_note: str = "", done_reflection: str = "") -> None:
        item = next((todo for todo in self.doc["todos"] if todo["id"] == todo_id), None)
        if item is None:
            return
        try:
            self.archive, _event = task_archive.add_event(
                self.archive,
                item,
                completed_at=str(item.get("done_at") or ""),
                done_note=done_note,
                done_reflection=done_reflection,
            )
            self.archive = task_archive.prune(self.archive)
            task_archive.save_archive(ARCHIVE_PATH, self.archive)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("写入完成归档失败：%s", exc)

    # ==================== v1.3 待办 / 记账 / 周报 / 记忆 ====================
    def _build_ledger_store(self) -> Any:
        store_cls = getattr(core, "LedgerStore", None)
        if store_cls is None:
            return None
        token = str(self.config.get("gh_token", "") or "").strip()
        gist_id = str(self.config.get("ledger_gist_id", "") or "").strip()
        if not token or not gist_id:
            return None
        try:
            return store_cls(token=token, gist_id=gist_id, timeout=8)
        except Exception:  # noqa: BLE001
            return None

    def _ensure_ledger_gist(self) -> None:
        if self.config.get("ledger_gist_id"):
            self.ledger_store = self._build_ledger_store()
            return
        token = str(self.config.get("gh_token", "") or "").strip()
        store_cls = getattr(core, "LedgerStore", None)
        if not token or store_cls is None:
            return
        self._notify("账本", "首次使用记账，正在创建专用账本 Gist…")

        def worker() -> None:
            try:
                store = store_cls(token=token, timeout=15)
                gist_id = store.create_gist(self.ledger_doc)
                self.bridge.ledger_sync_finished.emit(
                    {"ok": True, "created_gist": gist_id, "doc": self.ledger_doc, "etag": None}
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("创建账本 Gist 失败：%s", exc)
                self.bridge.ledger_sync_finished.emit({"ok": False, "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="LedgerGistCreate").start()

    def request_ledger_sync(self) -> None:
        if self.ledger_store is None or self._ledger_sync_running:
            return
        self._ledger_sync_running = True
        snapshot = json.loads(json.dumps(self.ledger_doc))
        etag = self.ledger_etag
        self._ledger_sync_thread = threading.Thread(
            target=self._ledger_sync_worker, args=(snapshot, etag), daemon=True, name="LedgerSync"
        )
        self._ledger_sync_thread.start()

    def _ledger_sync_worker(self, local_doc: Dict[str, Any], etag: Optional[str]) -> None:
        assert self.ledger_store is not None
        try:
            report = self.ledger_store.sync(local_doc, etag)
            self.bridge.ledger_sync_finished.emit(
                {"ok": True, "doc": report.doc, "etag": report.etag, "status": getattr(report, "status", "")}
            )
        except core.GitHubApiError as exc:
            self.bridge.ledger_sync_finished.emit({"ok": False, "error": str(exc)})
        except requests.RequestException as exc:
            self.bridge.ledger_sync_finished.emit({"ok": False, "error": f"网络错误：{exc}"})
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("账本同步线程异常")
            self.bridge.ledger_sync_finished.emit({"ok": False, "error": str(exc)})

    def on_ledger_sync_finished(self, result: Dict[str, Any]) -> None:
        self._ledger_sync_running = False
        created = str(result.get("created_gist") or "").strip()
        if created:
            self.config["ledger_gist_id"] = created
            save_config(self.config)
            self.ledger_store = self._build_ledger_store()
            self.ledger_etag = None
            self._notify("账本", "已创建专用账本 Gist，记账会同步到云端")
            return
        if result.get("ok") and result.get("doc"):
            self.ledger_doc = result["doc"]
            self.ledger_etag = result.get("etag")
            save_ledger_state(self.ledger_doc, self.ledger_etag)
            self.refresh_texts()
        elif result.get("error"):
            LOGGER.warning("账本同步失败：%s", result["error"])

    def _persist_ledger(self) -> None:
        save_ledger_state(self.ledger_doc, self.ledger_etag)
        self.request_ledger_sync()

    def add_todo_dialog(self) -> None:
        dialog = AddTodoDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            self.doc, _todo = core.add_todo(
                self.doc,
                values["text"],
                priority=values["priority"],
                due_date=values["due_date"],
                estimate_minutes=values["estimate_minutes"],
            )
        except TypeError:
            # 兼容 core 还没升级的情况
            self.doc, _todo = core.add_todo(self.doc, values["text"], priority=values["priority"])
        self._persist_and_sync()
        self.refresh_texts()
        due = f"（目标 {values['due_date']}）" if values["due_date"] else ""
        self._notify("已添加待办", f"{values['text']}{due}")

    def ai_plan_dialog(self, default_horizon: int = 14, default_granularity: str = "standard") -> None:
        """打开 AI 规划弹窗；``default_horizon`` 较大时即“长期任务规划”。"""
        if not str(self.config.get("ai_api_key", "")).strip():
            answer = QMessageBox.question(
                self,
                "AI 未配置",
                "还没有填写 AI API Key，是否现在打开设置？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_advanced_settings()
            return
        if not self._ai_idle_ok():
            dialog = AiPlanDialog(
                self,
                self.config,
                queue_mode=True,
                idle_at=self.ai_calendar.next_idle_start(),
                default_horizon=default_horizon,
                default_granularity=default_granularity,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.is_queued():
                self._enqueue_ai_task(
                    dialog.task_text(), dialog.horizon_days(), dialog.options()
                )
            return
        dialog = AiPlanDialog(
            self,
            self.config,
            default_horizon=default_horizon,
            default_granularity=default_granularity,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        plan = dialog.plan() or {}
        if not (plan.get("steps") or []):
            QMessageBox.warning(self, "没有拆解结果", "AI 没有返回可用的子任务。")
            return
        count = self._apply_ai_plan(plan, default_title=dialog.task_text())
        self._notify("AI 拆解完成", f"《{plan.get('title') or dialog.task_text()}》已拆成 {count} 个子任务")

    def long_term_plan_dialog(self) -> None:
        """「长期任务」按钮：默认 1 个月、详细粒度、工作日安排的 AI 排期。"""
        self.ai_plan_dialog(default_horizon=30, default_granularity="detailed")

    def _plan_parents(self) -> List[Dict[str, Any]]:
        """列出带子任务的未完成父任务，供「更新规划」选择。"""
        todos = self.doc.get("todos", [])
        parents: List[Dict[str, Any]] = []
        for todo in todos:
            if todo.get("parent_id") or todo.get("done"):
                continue
            children = [child for child in todos if child.get("parent_id") == todo.get("id")]
            if not children:
                continue
            done_titles = [str(child.get("text") or "") for child in children if child.get("done")]
            pending = [child for child in children if not child.get("done")]
            parents.append(
                {
                    "parent_id": todo.get("id"),
                    "parent_text": str(todo.get("text") or ""),
                    "due_date": str(todo.get("due_date") or ""),
                    "total": len(children),
                    "done_count": len(done_titles),
                    "done_titles": done_titles,
                    "incomplete_titles": [str(child.get("text") or "") for child in pending],
                }
            )
        return parents

    def update_plan_dialog(self, parent_id: str) -> None:
        """更新一个长期任务的规划：保留已完成子任务，AI 重排未完成部分。"""
        info = next(
            (item for item in self._plan_parents() if item["parent_id"] == parent_id), None
        )
        if info is None:
            QMessageBox.information(self, "没有可更新的规划", "这个任务没有未完成的子任务。")
            return
        if not str(self.config.get("ai_api_key", "")).strip():
            answer = QMessageBox.question(
                self, "AI 未配置", "更新规划需要 AI API Key，是否现在打开设置？"
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_advanced_settings()
            return
        remaining = 14
        try:
            due = core.parse_iso(info.get("due_date") or "")
            if due:
                remaining = max(3, (due.date() - core.current_day()).days)
        except Exception:  # noqa: BLE001
            remaining = 14
        remaining = max(3, min(120, remaining))
        if not self._ai_idle_ok():
            dialog = AiPlanDialog(
                self,
                self.config,
                queue_mode=True,
                idle_at=self.ai_calendar.next_idle_start(),
                default_horizon=remaining,
                default_granularity="standard",
                replan_parent=info,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.is_queued():
                self._enqueue_ai_task(
                    dialog.task_text(),
                    dialog.horizon_days(),
                    dialog.options(),
                    mode="replan",
                    parent_id=parent_id,
                    context_text=dialog.replan_context(),
                )
            return
        dialog = AiPlanDialog(
            self,
            self.config,
            default_horizon=remaining,
            default_granularity="standard",
            replan_parent=info,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        plan = dialog.plan() or {}
        if not (plan.get("steps") or []):
            QMessageBox.warning(self, "没有拆解结果", "AI 没有返回可用的子任务。")
            return
        count = self._apply_ai_replan(parent_id, plan)
        self._notify("任务规划已更新", f"已用新计划替换未完成子任务（{count} 步）")

    def _apply_ai_replan(self, parent_id: str, plan: Dict[str, Any]) -> int:
        parent = next((todo for todo in self.doc["todos"] if todo["id"] == parent_id), None)
        if parent is None:
            return 0
        steps = plan.get("steps") or []
        if not steps:
            return 0
        for child in [
            todo for todo in self.doc["todos"]
            if todo.get("parent_id") == parent_id and not todo.get("done")
        ]:
            self.doc = core.delete_todo(self.doc, str(child["id"]))
        last_due = ""
        for step in steps:
            last_due = str(step.get("due_date") or last_due)
            self.doc, _child = core.add_todo(
                self.doc,
                str(step.get("text") or "").strip(),
                due_date=str(step.get("due_date") or ""),
                parent_id=parent_id,
                estimate_minutes=int(step.get("estimate_minutes") or 0),
            )
        total_minutes = sum(int(step.get("estimate_minutes") or 0) for step in steps)
        try:
            self.doc = core.update_todo(
                self.doc,
                parent_id,
                due_date=last_due,
                estimate_minutes=total_minutes,
            )
        except Exception:  # noqa: BLE001
            pass
        self._persist_and_sync()
        self.refresh_texts()
        return len(steps)

    def _apply_ai_plan(self, plan: Dict[str, Any], default_title: str = "复杂任务") -> int:
        steps = plan.get("steps") or []
        if not steps:
            return 0
        title = str(plan.get("title") or default_title or "复杂任务")
        parent_due = str(steps[-1].get("due_date") or "")
        total_minutes = sum(int(step.get("estimate_minutes") or 0) for step in steps)
        try:
            self.doc, parent = core.add_todo(
                self.doc, title, priority="normal", due_date=parent_due, estimate_minutes=total_minutes
            )
        except TypeError:
            self.doc, parent = core.add_todo(self.doc, title)
        parent_id = str(parent["id"])
        for step in steps:
            try:
                self.doc, _child = core.add_todo(
                    self.doc,
                    str(step.get("text") or "").strip(),
                    due_date=str(step.get("due_date") or ""),
                    parent_id=parent_id,
                    estimate_minutes=int(step.get("estimate_minutes") or 0),
                )
            except TypeError:
                self.doc, _child = core.add_todo(self.doc, str(step.get("text") or ""))
        self._persist_and_sync()
        self.refresh_texts()
        return len(steps)

    # ---- AI 排队（高峰时段不调用，空闲自动运行） ----
    def _load_ai_queue(self) -> List[Dict[str, Any]]:
        try:
            if AI_QUEUE_PATH.exists():
                payload = json.loads(AI_QUEUE_PATH.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    return [
                        item for item in payload
                        if isinstance(item, dict) and str(item.get("text") or "").strip()
                    ]
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取 AI 排队失败：%s", exc)
        return []

    def _save_ai_queue(self) -> None:
        try:
            ensure_app_dir()
            _atomic_write_json(AI_QUEUE_PATH, self.ai_queue)
        except OSError as exc:
            LOGGER.warning("保存 AI 排队失败：%s", exc)

    def _enqueue_ai_task(
        self,
        text: str,
        horizon_days: int,
        options: Any = None,
        mode: str = "create",
        parent_id: str = "",
        context_text: str = "",
    ) -> None:
        text = str(text or "").strip()
        if not text:
            return
        item = {
            "id": f"aiq-{int(time.time() * 1000)}",
            "text": text,
            "horizon_days": int(horizon_days or 14),
            "options": dict(options or {}),
            "mode": str(mode or "create"),
            "parent_id": str(parent_id or ""),
            "context_text": str(context_text or ""),
            "created_at": datetime.datetime.now(ai_schedule.BEIJING_TZ).isoformat(timespec="seconds"),
            "attempts": 0,
        }
        self.ai_queue.append(item)
        self._save_ai_queue()
        self._notify(
            "已加入 AI 排队",
            f"当前是高峰时段，任务会在空闲时段（约 {self._next_idle_text()}）自动拆解并加入待办",
        )

    def cancel_ai_queue_item(self, queue_id: str) -> None:
        before = len(self.ai_queue)
        self.ai_queue = [item for item in self.ai_queue if item.get("id") != queue_id]
        if len(self.ai_queue) != before:
            self._save_ai_queue()
            self._notify("已取消排队任务", "AI 排队列表已更新")

    def _check_ai_queue(self) -> None:
        if self._ai_queue_running or not self.ai_queue:
            return
        if time.monotonic() < self._ai_queue_retry_at:
            return
        if not self._ai_idle_ok():
            return
        client = self._ai_client()
        if client is None:
            return
        item = self.ai_queue[0]
        self._ai_queue_running = True

        def worker() -> None:
            try:
                import ai_client

                options = dict(item.get("options") or {})
                if item.get("context_text"):
                    options["context_text"] = str(item.get("context_text"))
                plan = ai_client.plan_complex_task(
                    client,
                    str(item.get("text") or ""),
                    core.current_day(),
                    horizon_days=int(item.get("horizon_days") or 14),
                    options=options,
                )
                self.bridge.ai_queue_result.emit(
                    {
                        "ok": True,
                        "id": item.get("id"),
                        "mode": item.get("mode") or "create",
                        "parent_id": item.get("parent_id") or "",
                        "plan": plan,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("AI 排队任务执行失败")
                self.bridge.ai_queue_result.emit(
                    {"ok": False, "id": item.get("id"), "error": str(exc)}
                )

        threading.Thread(target=worker, daemon=True, name="AiQueue").start()

    def on_ai_queue_result(self, result: Dict[str, Any]) -> None:
        self._ai_queue_running = False
        queue_id = str(result.get("id") or "")
        item = next((entry for entry in self.ai_queue if entry.get("id") == queue_id), None)
        if result.get("ok"):
            plan = result.get("plan") or {}
            mode = str(result.get("mode") or (item or {}).get("mode") or "create")
            if mode == "replan":
                parent_id = str(result.get("parent_id") or (item or {}).get("parent_id") or "")
                count = self._apply_ai_replan(parent_id, plan)
                title = str((item or {}).get("text") or "长期任务")
                self.ai_queue = [entry for entry in self.ai_queue if entry.get("id") != queue_id]
                self._save_ai_queue()
                self._notify("任务规划已更新", f"《{title}》已替换未完成子任务（{count} 步）")
                return
            title = str(plan.get("title") or (item or {}).get("text") or "复杂任务")
            count = self._apply_ai_plan(plan, default_title=title)
            self.ai_queue = [entry for entry in self.ai_queue if entry.get("id") != queue_id]
            self._save_ai_queue()
            self._notify("AI 排队任务已完成", f"《{title}》已拆成 {count} 个子任务并加入待办")
            return
        error = str(result.get("error") or "未知错误")
        if item is not None:
            item["attempts"] = int(item.get("attempts") or 0) + 1
            if item["attempts"] >= 3:
                self.ai_queue = [entry for entry in self.ai_queue if entry.get("id") != queue_id]
                self._notify("AI 排队任务失败", f"已重试 3 次，任务已移除：{error}")
            else:
                self._notify(
                    "AI 排队任务失败",
                    f"将在约 10 分钟后重试：{error}",
                )
        self._save_ai_queue()
        self._ai_queue_retry_at = time.monotonic() + 600

    def on_calendar_ready(self, calendar: Any) -> None:
        if isinstance(calendar, ai_schedule.HolidayCalendar):
            self.ai_calendar = calendar
            LOGGER.info("节假日数据已刷新：%d 天", len(calendar.dates))
            self._check_ai_queue()

    def _refresh_holidays_worker(self) -> None:
        try:
            if HOLIDAY_CACHE_PATH.exists() and (time.time() - HOLIDAY_CACHE_PATH.stat().st_mtime) < 7 * 86400:
                return
            dates = ai_schedule.refresh_holidays(HOLIDAY_CACHE_PATH, timeout=10)
            self.bridge.calendar_ready.emit(ai_schedule.HolidayCalendar(dates))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("刷新节假日数据失败：%s", exc)

    def ledger_dialog(self) -> None:
        dialog = LedgerDialog(self, self.ledger_doc)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            self.ledger_doc, _entry = core.add_ledger_entry(
                self.ledger_doc,
                values["date"],
                values["type"],
                values["amount"],
                values["category"],
                values["note"],
                time=values["time"],
            )
        except AttributeError:
            QMessageBox.warning(self, "模块未就绪", "账本模块还没准备好，请稍后重试。")
            return
        self._persist_ledger()
        self.refresh_texts()
        self._notify(
            "已记账",
            f"{'收入' if values['type'] == 'income' else '支出'} {core.format_money(values['amount'])} · {values['category']}",
        )

    # ---- AI / 报表 ----
    def _ai_client(self) -> Any:
        if not bool(self.config.get("ai_enabled", True)):
            return None
        key = str(self.config.get("ai_api_key", "") or "").strip()
        if not key:
            return None
        try:
            import ai_client

            return ai_client.OpenAICompatibleClient(
                base_url=str(self.config.get("ai_base_url") or "https://api.deepseek.com"),
                api_key=key,
                model=str(self.config.get("ai_model") or "deepseek-chat"),
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("AI 客户端不可用：%s", exc)
            return None

    def _ai_idle_ok(self) -> bool:
        """按用户规则判断：高峰 = 北京时间周一至周五 09:00–12:00 / 14:00–18:00（不含法定节假日）。"""
        try:
            return self.ai_calendar.is_idle(datetime.datetime.now(ai_schedule.BEIJING_TZ))
        except Exception:  # noqa: BLE001
            return True

    def _next_idle_text(self) -> str:
        try:
            moment = self.ai_calendar.next_idle_start()
            return moment.strftime("%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return "空闲时段"

    def _today_context(self, day: Optional["datetime.date"] = None) -> str:
        today = day or core.current_day()
        todos = self.doc.get("todos", [])
        done_today = [
            item for item in todos
            if item.get("done") and str(item.get("done_at", ""))[:10] == today.isoformat()
        ]
        pending = core.pending_todos(self.doc)
        overdue = [
            item for item in pending
            if str(item.get("due_date") or "") and str(item.get("due_date")) < today.isoformat()
        ]
        try:
            week = core.ledger_week_summary(self.ledger_doc, _week_start(today))
            ledger_text = (
                f"本周收入 {core.format_money(week['income'])}，"
                f"支出 {core.format_money(week['expense'])}，结余 {core.format_money(week['net'])}"
            )
        except Exception:  # noqa: BLE001
            ledger_text = "账本暂无数据"
        lines = [
            f"日期：{today.isoformat()}",
            f"今日完成（{len(done_today)}）：" + "；".join(item.get("text", "") for item in done_today[:8]),
            f"未完成（{len(pending)}）：" + "；".join(item.get("text", "") for item in pending[:8]),
            f"逾期（{len(overdue)}）：" + "；".join(item.get("text", "") for item in overdue[:8]),
            f"账本：{ledger_text}",
        ]
        return "\n".join(lines)

    def _weekly_context(self) -> str:
        data = core.weekly_report_data(self.doc, self.ledger_doc, _week_start(core.current_day()))
        return json.dumps(
            {
                "week": f"{data.get('week_start')} ~ {data.get('week_end')}",
                "completed": [item.get("text", "") for item in data.get("completed", [])],
                "pending": [item.get("text", "") for item in data.get("pending", [])],
                "overdue": [item.get("text", "") for item in data.get("overdue", [])],
                "improvements": data.get("improvements", []),
                "ledger": data.get("ledger", {}),
            },
            ensure_ascii=False,
        )[:6000]

    def _rule_evaluation(
        self, kind: str, day: Optional["datetime.date"] = None, reason: str = "未配置 AI API"
    ) -> str:
        """AI 不可用 / 非空闲时段时的本地规则评价，保证记忆和周报里始终有“评价”。"""
        today = day or core.current_day()
        todos = self.doc.get("todos", [])
        pending = core.pending_todos(self.doc)
        try:
            week_ledger = core.ledger_week_summary(self.ledger_doc, _week_start(today))
        except Exception:  # noqa: BLE001
            week_ledger = {"income": 0.0, "expense": 0.0, "net": 0.0}
        if kind == "day":
            done = [
                item for item in todos
                if item.get("done") and str(item.get("done_at", ""))[:10] == today.isoformat()
            ]
            overdue = [
                item for item in pending
                if str(item.get("due_date") or "") and str(item.get("due_date")) < today.isoformat()
            ]
            parts = [f"（本地规则评价，{reason}）今天完成 {len(done)} 项，剩余 {len(pending)} 项未完成"]
            if overdue:
                first = str(overdue[0].get("text") or "逾期任务")
                parts.append(f"有 {len(overdue)} 项逾期，建议明天先清理「{first}」")
            parts.append(
                f"本周支出 {core.format_money(week_ledger['expense'])}、"
                f"结余 {core.format_money(week_ledger['net'])}"
            )
            if len(done) == 0:
                parts.append("今天还没有完成记录，明天从最小的一步开始即可")
            elif len(pending) == 0:
                parts.append("待办已清空，保持这个节奏")
            else:
                parts.append("先完成最重要的一项，比同时铺开更有效")
            return "；".join(parts) + "。"
        data = core.weekly_report_data(self.doc, self.ledger_doc, _week_start(today))
        completed = len(data.get("completed", []))
        pending_count = len(data.get("pending", []))
        overdue_count = len(data.get("overdue", []))
        ledger = data.get("ledger", {}) or {}
        return (
            f"（本地规则评价，{reason}）本周完成 {completed} 项，未完成 {pending_count} 项"
            f"{f'，其中逾期 {overdue_count} 项' if overdue_count else ''}；"
            f"账单收入 {core.format_money(ledger.get('income', 0))}、"
            f"支出 {core.format_money(ledger.get('expense', 0))}、"
            f"结余 {core.format_money(ledger.get('net', 0))}。"
            "建议下周把逾期任务拆成更小的步骤，并给重要任务预留固定时间。"
        )

    def _ai_evaluate(self, kind: str, day: Optional["datetime.date"] = None) -> str:
        target = day or core.current_day()
        client = self._ai_client()
        if client is None:
            return self._rule_evaluation(kind, target, "未配置 AI API")
        if not self._ai_idle_ok():
            LOGGER.info("当前是高峰时段，跳过 AI 调用，使用本地规则评价")
            return self._rule_evaluation(kind, target, "当前是高峰时段")
        try:
            import ai_client

            context = self._weekly_context() if kind == "week" else self._today_context(target)
            text = str(ai_client.evaluate(kind, client, context) or "")
            return text or self._rule_evaluation(kind, target, "AI 返回为空")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("AI 评价失败（%s）：%s", kind, exc)
            return self._rule_evaluation(kind, target, "AI 调用失败")

    def build_weekly_report(self) -> Tuple[Dict[str, Any], str, str]:
        data = core.weekly_report_data(self.doc, self.ledger_doc, _week_start(core.current_day()))
        ai_comment = self._ai_evaluate("week")
        markdown = core.weekly_report_markdown(data, ai_comment)
        return data, markdown, ai_comment

    def show_weekly_report(self) -> None:
        try:
            _data, markdown, _ai = self.build_weekly_report()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "周报生成失败", str(exc))
            return
        dialog = WeeklyReportDialog(self, markdown)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.send_requested:
            self.send_weekly_report_thread(preview_markdown=markdown)

    def send_weekly_report_thread(self, preview_markdown: str = "") -> None:
        if self._mail_running:
            return
        if not self.config.get("mail_user") or not self.config.get("mail_password"):
            answer = QMessageBox.question(
                self,
                "邮件未配置",
                "还没有填写 Gmail 发件邮箱和应用密码，是否现在打开设置？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_advanced_settings()
            return
        self._mail_running = True
        self._notify("周报", "正在生成并发送周报…")
        config_snapshot = dict(self.config)
        doc_snapshot = json.loads(json.dumps(self.doc))
        ledger_snapshot = json.loads(json.dumps(self.ledger_doc))

        def worker() -> None:
            try:
                import reporting

                data = core.weekly_report_data(
                    doc_snapshot, ledger_snapshot, _week_start(core.current_day())
                )
                ai_comment = self._ai_evaluate("week")
                subject, html, text = reporting.build_weekly_email(data, ai_comment)
                reporting.send_email(config_snapshot, subject, html, text)
                self.bridge.mail_finished.emit({"ok": True, "subject": subject})
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("发送周报失败")
                self.bridge.mail_finished.emit({"ok": False, "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="WeeklyMail").start()

    # ---- 每日记忆推送（默认凌晨 4 点推送“前一天”） ----
    def push_memory_thread(self, target_day: Optional["datetime.date"] = None) -> None:
        if self._memory_running:
            return
        token = str(self.config.get("memory_token", "") or self.config.get("gh_token", "") or "").strip()
        if not token or not self.config.get("memory_repo"):
            QMessageBox.warning(self, "记忆仓库未配置", "请先在「高级设置 → 记忆仓库」填写仓库和 Token。")
            return
        if target_day is None:
            target_day = core.current_day() - datetime.timedelta(days=1)
        moment = datetime.datetime.combine(target_day, datetime.time(23, 59))
        self._memory_running = True
        self._notify("记忆", f"正在生成并推送 {target_day.isoformat()} 的记忆…")
        self._sync_archive_from_doc()
        config_snapshot = dict(self.config)
        config_snapshot["memory_token"] = token
        doc_snapshot = json.loads(json.dumps(self.doc))
        ledger_snapshot = json.loads(json.dumps(self.ledger_doc))
        archive_snapshot = json.loads(json.dumps(self.archive))
        target_iso = target_day.isoformat()

        def worker() -> None:
            try:
                import memory_sync

                # 记忆里的“当天完成”以完成时间为准，并合并归档（被删除的完成项也保留）
                events = task_archive.events_for_day(archive_snapshot, target_day)
                memory_doc = json.loads(json.dumps(doc_snapshot))
                memory_doc["todos"] = [
                    todo for todo in memory_doc.get("todos", [])
                    if not (
                        todo.get("done")
                        and str(todo.get("done_at") or "")[:10] == target_iso
                    )
                ]
                for event in events:
                    memory_doc["todos"].append(
                        {
                            "id": event.get("todo_id") or event.get("id") or "",
                            "text": event.get("text") or "",
                            "priority": event.get("priority") or "normal",
                            "done": True,
                            "done_at": event.get("completed_at") or "",
                            "done_note": event.get("done_note") or "",
                            "done_reflection": event.get("done_reflection") or "",
                            "due_date": event.get("due_date") or "",
                            "parent_id": event.get("parent_id") or "",
                            "created_at": event.get("created_at") or "",
                            "updated_at": event.get("completed_at") or "",
                            "remind_at": "",
                            "remind_date": "",
                            "notified_at": "",
                            "estimate_minutes": 0,
                        }
                    )
                ai_comment = self._ai_evaluate("day", target_day)
                memory_sync.push_daily_memory(
                    config_snapshot, memory_doc, ledger_snapshot, moment, ai_comment
                )
                # 机器可读归档：完成 + 未完成/逾期快照
                if bool(config_snapshot.get("memory_enabled", True)):
                    try:
                        payload = task_archive.archive_payload(
                            archive_snapshot, target_day, doc_snapshot
                        )
                        repo = memory_sync.MemoryRepo(
                            token=token,
                            repo=str(config_snapshot.get("memory_repo") or ""),
                            branch=str(config_snapshot.get("memory_branch") or "main"),
                        )
                        repo.put_file(
                            f"archive/{target_iso}.json",
                            json.dumps(payload, ensure_ascii=False, indent=2),
                            f"archive {target_iso}",
                        )
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("推送记忆归档 JSON 失败：%s", exc)
                self.bridge.memory_finished.emit({"ok": True, "date": target_iso})
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("推送记忆失败")
                self.bridge.memory_finished.emit({"ok": False, "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="DailyMemory").start()

    def _check_scheduled_jobs(self) -> None:
        now = datetime.datetime.now()
        target_day = now.date() - datetime.timedelta(days=1)   # 凌晨推“前一天”
        target_iso = target_day.isoformat()
        if bool(self.config.get("memory_enabled", True)) and _hhmm_reached(
            now, str(self.config.get("memory_time") or "04:00")
        ):
            if time.monotonic() >= self._memory_retry_at and str(self.config.get("last_memory_date") or "") != target_iso:
                self.config["last_memory_date"] = target_iso
                save_config(self.config)
                self.push_memory_thread(target_day)
        if bool(self.config.get("weekly_report_enabled", True)):
            iso = now.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            try:
                target_weekday = int(self.config.get("weekly_report_weekday") or 7)
            except (TypeError, ValueError):
                target_weekday = 7
            if (
                now.isoweekday() == target_weekday
                and _hhmm_reached(now, str(self.config.get("weekly_report_time") or "21:00"))
                and time.monotonic() >= self._mail_retry_at
                and str(self.config.get("last_report_week") or "") != week_key
            ):
                self.config["last_report_week"] = week_key
                save_config(self.config)
                self.send_weekly_report_thread()
        self._check_ai_queue()

    def on_mail_finished(self, result: Dict[str, Any]) -> None:
        self._mail_running = False
        if result.get("ok"):
            self._notify("周报已发送", str(result.get("subject") or "本周总结"))
        else:
            # 失败则允许下一轮重试（10 分钟后，避免代理/网络故障时每 30 秒刷屏）
            self._mail_retry_at = time.monotonic() + 600
            self.config["last_report_week"] = ""
            save_config(self.config)
            self._notify("周报发送失败", str(result.get("error") or "未知错误"))

    def on_memory_finished(self, result: Dict[str, Any]) -> None:
        self._memory_running = False
        if result.get("ok"):
            self._notify("记忆已推送", f"已写入 taskbar-memory（{result.get('date')}）")
        else:
            # 失败则允许当天稍后重试（10 分钟后）
            self._memory_retry_at = time.monotonic() + 600
            self.config["last_memory_date"] = ""
            save_config(self.config)
            self._notify("记忆推送失败", str(result.get("error") or "未知错误"))

    def open_advanced_settings(self) -> None:
        dialog = AdvancedSettingsDialog(self, self.config)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.update(dialog.result_config())
        save_config(self.config)
        self.ledger_store = self._build_ledger_store()
        self.ledger_etag = None
        self._ensure_ledger_gist()
        self.request_ledger_sync()
        self.refresh_texts()

    def _notify(self, title: str, message: str) -> None:
        LOGGER.info("%s：%s", title, message)
        if getattr(self, "_tray", None) is not None:
            self._tray.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Information, 4000
            )

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.config, self.doc)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.update(dialog.result_config())
        save_config(self.config)
        self.store = self._build_store()
        self.etag = None
        self._sync_running = False
        self.ledger_store = self._build_ledger_store()
        self._ensure_ledger_gist()
        self.request_sync()
        self.request_ledger_sync()
        self.refresh_texts()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            f"媒体后端：{MEDIA_BACKEND or '窗口标题兜底（未安装 winsdk / pywinrt）'}\n"
            f"配置目录：{APP_DIR}\n"
            f"日志文件：{LOG_PATH}\n\n"
            "数据存储在你自己账号的私密 Gist 中，不经过任何第三方服务器。",
        )

    def start_tray(self) -> None:
        if not self.config.get("show_tray", True):
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            LOGGER.info("系统托盘不可用，跳过托盘图标")
            return
        self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
        self._tray.setToolTip(APP_TITLE)
        tray_menu = QMenu(self)
        tray_menu.addAction("显示 / 隐藏", self.toggle_visibility)
        tray_quick = tray_menu.addMenu("➕ 待办 / 记账 / 周报")
        tray_quick.addAction("📅 长期任务规划（AI 排期）…", self.long_term_plan_dialog)
        parents = self._plan_parents()
        if parents:
            tray_update = tray_quick.addMenu("🔄 更新任务规划…")
            for info in parents[:8]:
                tray_update.addAction(
                    f"🔄 {str(info['parent_text'])[:16]}（{info['done_count']}/{info['total']}）",
                    lambda _checked=False, pid=info["parent_id"]: self.update_plan_dialog(pid),
                )
        tray_quick.addAction("➕ 添加待办…", self.add_todo_dialog)
        tray_quick.addAction("🤖 AI 拆解复杂任务…", self.ai_plan_dialog)
        tray_quick.addAction("💰 记一笔…", self.ledger_dialog)
        tray_quick.addAction("📊 周报预览…", self.show_weekly_report)
        tray_quick.addAction("📤 立即发送周报", self.send_weekly_report_thread)
        tray_quick.addAction("🧠 立即推送今日记忆", self.push_memory_thread)
        tray_menu.addAction("📌 重新贴合任务栏", self.reapply_taskbar_layout)
        tray_menu.addAction("🔄 立即同步", self.request_sync)
        daily_tray_action = QAction("⏰ 每天 22:00 提醒填待办", tray_menu)
        daily_tray_action.setCheckable(True)
        daily_tray_action.setChecked(bool(self.config.get("daily_fill_reminder", True)))
        daily_tray_action.triggered.connect(self.toggle_daily_fill_reminder)
        tray_menu.addAction(daily_tray_action)
        fullscreen_tray_action = QAction("🖥️ 全屏应用时自动隐藏", tray_menu)
        fullscreen_tray_action.setCheckable(True)
        fullscreen_tray_action.setChecked(bool(self.config.get("hide_on_fullscreen", True)))
        fullscreen_tray_action.triggered.connect(self.toggle_hide_on_fullscreen)
        tray_menu.addAction(fullscreen_tray_action)
        direct_tray_action = QAction("🌐 GitHub API 直连（绕过代理）", tray_menu)
        direct_tray_action.setCheckable(True)
        direct_tray_action.setChecked(bool(self.config.get("github_direct", True)))
        direct_tray_action.triggered.connect(self.toggle_github_direct)
        tray_menu.addAction(direct_tray_action)
        offset_ms = int(self.config.get("lyric_offset_ms", 0) or 0)
        lyric_tray_menu = tray_menu.addMenu(f"🎶 歌词同步微调（{offset_ms / 1000:+.1f}s）")
        lyric_tray_menu.addAction("歌词更早 0.5s", lambda: self.adjust_lyric_offset(500))
        lyric_tray_menu.addAction("歌词更早 0.2s", lambda: self.adjust_lyric_offset(200))
        lyric_tray_menu.addAction("歌词更晚 0.2s", lambda: self.adjust_lyric_offset(-200))
        lyric_tray_menu.addAction("歌词更晚 0.5s", lambda: self.adjust_lyric_offset(-500))
        lyric_tray_menu.addSeparator()
        lyric_tray_menu.addAction("重置偏移（0s）", self.reset_lyric_offset)
        tray_menu.addAction("⚙️ 配置同步", self.open_settings)
        tray_menu.addAction("⚙️ AI / 邮件 / 记忆设置", self.open_advanced_settings)
        tray_menu.addSeparator()
        tray_menu.addAction("退出", QApplication.quit)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self._update_tray_tooltip()

    @staticmethod
    def _make_tray_icon() -> QIcon:
        logo = find_logo_file()
        if logo is not None:
            pixmap = QPixmap(str(logo))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    64,
                    64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                return QIcon(scaled)
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
        painter.fillPath(path, QColor(15, 23, 42, 235))
        painter.setPen(QPen(QColor(56, 189, 248), 1))
        painter.drawPath(path)
        font = QFont("Segoe UI Symbol", 30)
        painter.setFont(font)
        painter.setPen(QColor(56, 189, 248))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
        painter.end()
        return QIcon(pixmap)

    def _on_tray_activated(self, reason) -> None:  # noqa: ANN001
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self._user_hidden = True
            self.hide()
        else:
            self._user_hidden = False
            self._hidden_for_fullscreen = False
            self.show()
            QTimer.singleShot(0, self.apply_taskbar_mode)
            self.refresh_texts()

    # ==================== 退出清理 ====================
    def shutdown(self) -> None:
        try:
            self.media_watcher.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._embedded:
                Win32Taskbar.detach(int(self.winId()))
        except Exception:  # noqa: BLE001
            pass
        save_state(self.doc, self.etag, self.last_sync, self.last_error)
        try:
            task_archive.save_archive(ARCHIVE_PATH, self.archive)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--selftest", action="store_true", help="无界面自检后退出（验证环境/依赖）")
    parser.add_argument("--no-tray", action="store_true", help="不创建系统托盘图标")
    parser.add_argument("--diagnose", action="store_true", help="打印任务栏诊断信息后退出")
    args, qt_args = parser.parse_known_args(argv)

    ensure_app_dir()
    core.setup_logging(str(LOG_PATH), logging.INFO)
    LOGGER.info(
        "启动 %s v%s，媒体后端=%s", APP_NAME, APP_VERSION, MEDIA_BACKEND or "无"
    )

    if args.diagnose:
        try:
            import diagnose_taskbar

            return diagnose_taskbar.main()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("任务栏诊断失败：%s", exc)
            return 1

    if args.selftest:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import ai_client
            import memory_sync
            import reporting

            LOGGER.info(
                "selftest: %s / %s / %s 导入成功",
                ai_client.__name__,
                reporting.__name__,
                memory_sync.__name__,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("selftest: 集成模块导入失败：%s", exc)
            return 1

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if not args.selftest and not acquire_single_instance():
        QMessageBox.information(
            None, APP_TITLE, "程序已经在运行了：请从任务栏胶囊或托盘图标操作。"
        )
        return 0

    widget = TaskbarWidget(selftest=args.selftest or args.no_tray)
    widget.show()

    if args.selftest:
        QTimer.singleShot(1500, app.quit)
        exit_code = app.exec()
        widget.shutdown()
        print(f"MEDIA_BACKEND={MEDIA_BACKEND or 'none'}")
        print(f"CONFIG={CONFIG_PATH}")
        print("SELFTEST OK" if exit_code == 0 else f"SELFTEST FAILED: {exit_code}")
        return 0 if exit_code == 0 else 1

    exit_code = app.exec()
    widget.shutdown()
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())

