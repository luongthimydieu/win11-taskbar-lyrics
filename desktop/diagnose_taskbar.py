#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diagnose_taskbar.py — 任务栏环境诊断（只读，不修改系统）。

用途：当你反馈“胶囊没有和任务栏融为一体 / 位置不对 / 嵌入失败”时，运行这个脚本，
把输出发出来即可定位原因。它会打印：

- Windows 版本与构建号、DPI、当前进程是否管理员
- 主任务栏 `Shell_TrayWnd` 的句柄、矩形、客户区、DPI、暗色/亮色主题
- 任务栏窗口的**直接子窗口**与关键子窗口（托盘区 TrayNotifyWnd、任务按钮区）的类名、位置、可见性
- 副屏任务栏 `Shell_SecondaryTrayWnd` 是否存在

用法::

    python desktop\\diagnose_taskbar.py
    python desktop\\diagnose_taskbar.py --json      # 机器可读

不依赖 PyQt6，任何 Python 3.8+ 都可以跑。
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import platform
import sys
from typing import Any, Dict, List, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT

GW_CHILD = 5
GW_HWNDNEXT = 2
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _rect(hwnd: int) -> Optional[List[int]]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return [rect.left, rect.top, rect.right, rect.bottom]


def _client_rect(hwnd: int) -> Optional[List[int]]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return [
        origin.x + rect.left,
        origin.y + rect.top,
        origin.x + rect.right,
        origin.y + rect.bottom,
    ]


def _direct_children(hwnd: int) -> List[int]:
    children: List[int] = []
    child = user32.GetWindow(hwnd, GW_CHILD)
    while child:
        children.append(child)
        child = user32.GetWindow(child, GW_HWNDNEXT)
    return children


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _dark_mode(hwnd: int) -> Optional[bool]:
    value = ctypes.c_int(0)
    size = ctypes.c_uint(ctypes.sizeof(value))
    try:
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), size
        )
        return bool(value.value) if result == 0 else None
    except OSError:
        return None


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _describe_window(hwnd: int, depth: int = 0, max_depth: int = 2) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "hwnd": int(hwnd),
        "class": _class_name(hwnd),
        "text": _window_text(hwnd)[:60],
        "rect": _rect(hwnd),
        "client": _client_rect(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "dpi": int(user32.GetDpiForWindow(hwnd)),
        "children": [],
    }
    if depth < max_depth:
        for child in _direct_children(hwnd):
            info["children"].append(_describe_window(child, depth + 1, max_depth))
    return info


def collect() -> Dict[str, Any]:
    taskbar = user32.FindWindowW("Shell_TrayWnd", None)
    secondary = user32.FindWindowW("Shell_SecondaryTrayWnd", None)
    info: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "windows_version": platform.version(),
        "is_admin": _is_admin(),
        "taskbar_hwnd": int(taskbar) if taskbar else 0,
        "taskbar": _describe_window(taskbar, max_depth=2) if taskbar else None,
        "secondary_taskbar_hwnd": int(secondary) if secondary else 0,
        "secondary_taskbar": _describe_window(secondary, max_depth=1) if secondary else None,
    }
    if taskbar:
        info["taskbar_owner"] = _process_name(taskbar)
        info["taskbar_dark"] = _dark_mode(taskbar)
        info["taskbar_dpi"] = int(user32.GetDpiForWindow(taskbar))
    return info


def _walk(node: Dict[str, Any], depth: int = 0) -> None:
    rect = node.get("rect") or [0, 0, 0, 0]
    size = (rect[2] - rect[0], rect[3] - rect[1])
    text = f" text={node['text']!r}" if node.get("text") else ""
    print(
        f"{'  ' * depth}- [{node['class']}] hwnd={node['hwnd']} "
        f"rect={rect} size={size} visible={node['visible']}{text}"
    )
    for child in node.get("children") or []:
        _walk(child, depth + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="任务栏环境诊断")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    info = collect()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print(f"Python        : {info['python']}")
    print(f"Windows       : {info['platform']}  (version {info['windows_version']})")
    print(f"管理员进程    : {info['is_admin']}")
    print(f"任务栏句柄    : 0x{info['taskbar_hwnd']:X}   owner={info.get('taskbar_owner', '')}")
    print(f"任务栏 DPI    : {info.get('taskbar_dpi')}  （96=100%, 120=125%, 144=150%）")
    print(f"任务栏暗色    : {info.get('taskbar_dark')}  （None 表示系统未提供）")
    print(f"副屏任务栏    : 0x{info['secondary_taskbar_hwnd']:X}")
    print("=" * 78)
    if info.get("taskbar"):
        print("任务栏窗口树（最多两层）:")
        _walk(info["taskbar"])
    else:
        print("!! 没有找到 Shell_TrayWnd：任务栏可能被第三方工具接管，或不是标准 explorer 任务栏")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
