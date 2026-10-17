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
from PyQt6.QtCore import QObject, QRectF, QTimer, Qt, QUrl, pyqtSignal
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
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import core

LOGGER = logging.getLogger("taskbar.widget")

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

APP_NAME = "Win11TaskbarLyrics"
APP_TITLE = "Win11 任务栏歌词 · 跨端待办"
APP_VERSION = "1.2.4"

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
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
            "Windows.UI.Core.CoreWindow",
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
        return core.rect_covers_monitor(window_rect, monitor, tolerance=2) or cls.shell_d3d_fullscreen()

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


# ---------------------------------------------------------------------------
# 媒体监听线程（常驻事件循环，避免在 Qt 主线程反复 asyncio.run）
# ---------------------------------------------------------------------------

class MediaWatcher(threading.Thread):
    """每 2 秒读取一次当前播放状态：优先 SMTC，失败时退回网易云窗口标题。"""

    def __init__(self, bridge: Bridge, interval: float = 2.0) -> None:
        super().__init__(daemon=True, name="MediaWatcher")
        self.bridge = bridge
        self.interval = max(1.0, float(interval))
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
        try:
            timeline = target.get_timeline_properties()
            position = float(timeline.position.total_seconds())
        except Exception:  # noqa: BLE001
            position = -1.0

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

        self.netease = core.NetEaseClient()
        self.store = self._build_store()

        # 媒体 / 歌词状态
        self._media: Optional[Dict[str, Any]] = None
        self._media_mono = 0.0
        self._song_key = ""
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

        try:
            app = QGuiApplication.instance()
            if app is not None:
                app.screenAdded.connect(lambda *_: self.snap_to_taskbar())
                app.screenRemoved.connect(lambda *_: self.snap_to_taskbar())
        except Exception:  # noqa: BLE001
            pass

    def start_media_watcher(self) -> None:
        self.media_watcher = MediaWatcher(self.bridge, interval=2.0)
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
        self.refresh_texts()

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

    def _estimated_position(self) -> float:
        if self._media is None:
            return 0.0
        anchor = float(self._media.get("position", -1) or -1)
        if self._media.get("is_playing"):
            if anchor >= 0:
                return max(0.0, anchor + (time.monotonic() - self._media_mono))
            return max(0.0, time.monotonic() - self._fallback_start_mono)
        return max(0.0, anchor)

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
            return core.format_next_todo_line(todo, len(core.todos_for_day(self.doc, today)))
        return core.format_next_todo_line(
            todo, len(core.overdue_todos(self.doc, today)), lead="逾期"
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
    def _check_fullscreen_state(self) -> None:
        """B 站/网页视频全屏（F11）或全屏游戏时自动隐藏，退出全屏后恢复。

        带防抖：连续 2 秒检测到全屏才隐藏，连续 3 秒不是全屏才恢复，
        避免全屏游戏/视频切换那一瞬间的窗口变化导致组件一闪一闪。
        """
        if self.selftest or self._dragging:
            return
        if not bool(self.config.get("hide_on_fullscreen", True)):
            self._fullscreen_true_streak = 0
            self._fullscreen_false_streak = 0
            if self._hidden_for_fullscreen:
                self._restore_from_fullscreen()
            return

        fullscreen = Win32Taskbar.is_foreground_fullscreen(int(self.winId()))
        if fullscreen:
            self._fullscreen_true_streak += 1
            self._fullscreen_false_streak = 0
        else:
            self._fullscreen_false_streak += 1
            self._fullscreen_true_streak = 0

        if self._fullscreen_true_streak >= 2 and not self._hidden_for_fullscreen:
            LOGGER.info("检测到全屏应用，暂时隐藏任务栏组件")
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
        self.doc = core.set_done(self.doc, todo_id, True)
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
        except core.GitHubApiError as exc:
            self.bridge.sync_finished.emit(
                {"ok": False, "status": "error", "error": str(exc), "code": exc.status}
            )
        except requests.RequestException as exc:
            self.bridge.sync_finished.emit(
                {"ok": False, "status": "error", "error": f"网络错误：{exc}", "code": None}
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("同步线程异常")
            self.bridge.sync_finished.emit(
                {"ok": False, "status": "error", "error": str(exc), "code": None}
            )

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
                label = core.format_todo_line(item)
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
        self.doc = core.set_done(self.doc, todo_id, True)
        if self._current_alert_id == todo_id:
            self._current_alert_id = None
            self._activate_next_alert()
        self._persist_and_sync()
        self.refresh_texts()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.config, self.doc)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.update(dialog.result_config())
        save_config(self.config)
        self.store = self._build_store()
        self.etag = None
        self._sync_running = False
        self.request_sync()
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
        tray_menu.addAction("⚙️ 配置同步", self.open_settings)
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

