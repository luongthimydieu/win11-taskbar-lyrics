#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Win11 任务栏多功能显示器（网易云实时歌词 + 高赞乐评 + GitHub 手机端跨端待办提醒）
依赖安装：
    pip install PyQt6 requests winsdk psutil pywin32
"""

import asyncio
import bisect
import ctypes
from ctypes import wintypes
import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QAction, QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QMessageBox,
    QWidget,
)

# 可选加载 Windows SMTC 媒体控制接口（用于精准获取网易云播放进度与切歌状态）
try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )
    HAS_WINSDK = True
except ImportError:
    HAS_WINSDK = False

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".win11_taskbar_lyrics_todo.json")
GIST_FILENAME = "taskbar_todos.json"


# ==================== 1. 网易云音乐歌词与高赞乐评引擎 ====================
class NetEaseClient:
    """负责根据歌曲名 searching 歌曲 ID，并拉取逐行时间轴歌词与高分热评"""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://music.163.com/",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.cache: Dict[str, dict] = {}

    def fetch_song_bundle(self, title: str, artist: str = "") -> dict:
        query = f"{title} {artist}".strip()
        if query in self.cache:
            return self.cache[query]

        result = {"song_id": None, "lyrics": [], "hot_comments": []}
        try:
            # 1. 搜索歌曲 ID
            search_url = "https://music.163.com/api/search/get/web"
            resp = self.session.post(
                search_url,
                data={"s": query, "type": 1, "offset": 0, "total": "true", "limit": 3},
                timeout=5,
            ).json()
            songs = resp.get("result", {}).get("songs", [])
            if not songs:
                return result
            song_id = songs[0]["id"]
            result["song_id"] = song_id

            # 2. 获取 LRC 歌词
            lrc_url = f"https://music.163.com/api/song/lyric?os=pc&id={song_id}&lv=-1&tv=-1"
            lrc_resp = self.session.get(lrc_url, timeout=5).json()
            raw_lrc = lrc_resp.get("lrc", {}).get("lyric", "")
            result["lyrics"] = self.parse_lrc(raw_lrc)

            # 3. 获取高赞乐评 (hotComments)
            comment_url = f"https://music.163.com/api/v1/resource/comments/R_SO_4_{song_id}?limit=15&offset=0"
            c_resp = self.session.get(comment_url, timeout=5).json()
            hot_list = c_resp.get("hotComments", [])
            parsed_comments = []
            for item in hot_list[:10]:
                content = item.get("content", "").replace("\n", " ").strip()
                liked = item.get("likedCount", 0)
                user = item.get("user", {}).get("nickname", "云村村民")
                if content:
                    parsed_comments.append(f"「{content}」 —— @{user} (👍{liked})")
            result["hot_comments"] = parsed_comments
            self.cache[query] = result
        except Exception:
            pass
        return result

    @staticmethod
    def parse_lrc(lrc_text: str) -> List[Tuple[float, str]]:
        """将 LRC 歌词解析为按秒排序的 [(timestamp_sec, text), ...] 列表"""
        pattern = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")
        lines: List[Tuple[float, str]] = []
        for raw_line in lrc_text.splitlines():
            match = pattern.match(raw_line.strip())
            if match:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                ms_str = match.group(3) or "0"
                ms = int(ms_str.ljust(3, "0")[:3])
                total_sec = minutes * 60 + seconds + ms / 1000.0
                content = match.group(4).strip()
                if content:
                    lines.append((total_sec, content))
        lines.sort(key=lambda x: x[0])
        return lines


# ==================== 2. Windows 媒体状态与网易云窗口检测 ====================
class MediaDetector:
    """优先使用 Windows 11 SMTC 读取播放状态与进度，同时提供网易云窗口标题兜底"""

    @staticmethod
    async def _get_smtc_info() -> Optional[dict]:
        if not HAS_WINSDK:
            return None
        try:
            manager = await MediaManager.request_async()
            sessions = manager.get_sessions()
            target_session = None
            for s in sessions:
                app_id = (s.source_app_user_model_id or "").lower()
                if "cloudmusic" in app_id or "netease" in app_id or "163" in app_id:
                    target_session = s
                    break
            if target_session is None:
                target_session = manager.get_current_session()
            if target_session is None:
                return None

            info = await target_session.try_get_media_properties_async()
            timeline = target_session.get_timeline_properties()
            playback = target_session.get_playback_info()
            # PlaybackStatus: 4 表示 Playing
            is_playing = (playback.playback_status == 4) if playback else True
            pos_sec = timeline.position.total_seconds() if timeline else 0.0

            return {
                "title": info.title or "",
                "artist": info.artist or "",
                "position": pos_sec,
                "is_playing": is_playing,
            }
        except Exception:
            return None

    @classmethod
    def poll_current_media(cls) -> Optional[dict]:
        if HAS_WINSDK:
            try:
                res = asyncio.run(cls._get_smtc_info())
                if res and res.get("title"):
                    return res
            except Exception:
                pass
        # 兜底方案：通过 Win32 EnumWindows 查找网易云音乐主窗口标题 ("歌名 - 歌手")
        title_str = cls._find_netease_window_title()
        if title_str and " - " in title_str:
            parts = title_str.split(" - ", 1)
            return {
                "title": parts[0].strip(),
                "artist": parts[1].strip(),
                "position": -1.0,  # 无精确毫秒进度时按时间戳估算递进
                "is_playing": True,
            }
        return None

    @staticmethod
    def _find_netease_window_title() -> Optional[str]:
        user32 = ctypes.windll.user32
        found_title = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                class_buf = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(hwnd, class_buf, 128)
                # 网易云 PC 端常见窗口类名 OrpheusBrowserHost / CefBrowserWindow
                if class_buf.value in ("OrpheusBrowserHost", "Win32Window"):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        found_title.append(buf.value)
                        return False
            return True

        user32.EnumWindows(enum_proc, 0)
        return found_title[0] if found_title else None


# ==================== 3. 后台工作信号与数据同步总线 ====================
class WorkerSignals(QObject):
    song_loaded = pyqtSignal(str, list, list)  # key, lyrics, hot_comments
    todos_updated = pyqtSignal(list)           # todo list


# ==================== 4. Win11 任务栏悬浮胶囊主窗口 ====================
class TaskbarWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self.signals.song_loaded.connect(self.on_song_loaded)
        self.signals.todos_updated.connect(self.on_todos_updated)

        self.netease = NetEaseClient()
        self.config = self.load_config()

        # 运行时状态
        self.current_song_key = ""
        self.is_playing = False
        self.play_start_wallclock = time.time()
        self.current_pos_sec = 0.0
        self.lyrics: List[Tuple[float, str]] = []
        self.hot_comments: List[str] = []
        self.todos: List[dict] = []

        # 显示控制：mode 可选 'auto' (智能混排), 'lyrics', 'comments', 'todos'
        self.display_mode = "auto"
        self.comment_idx = 0
        self.todo_idx = 0
        self.last_rotate_time = time.time()
        self.reminded_ids = set()
        self.alert_todo: Optional[dict] = None
        self.flash_state = False

        self. primary_text = "🎵 任务栏显示器已就绪 · 正在等待网易云音乐或同步待办..."
        self.secondary_text = "💡 右键点击可配置手机端 GitHub Gist 待办同步、切换显示模式"

        self.init_ui()
        self.init_timers()
        self.sync_todos_async()

    def load_config(self) -> dict:
        default_cfg = {
            "gh_token": "",
            "gist_id": "",
            "offset_x": 220,  # 距离任务栏左侧偏移像素（避开开始按钮或组件按钮）
            "width": 520,
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    default_cfg.update(json.load(f))
            except Exception:
                pass
        return default_cfg

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.config.get("width", 520), 44)
        self.snap_to_win11_taskbar()

    def snap_to_win11_taskbar(self):
        """定位 Windows 11 Shell_TrayWnd 任务栏矩形并自动吸附"""
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        rect = wintypes.RECT()
        if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            tb_height = rect.bottom - rect.top
            x = rect.left + int(self.config.get("offset_x", 220))
            y = rect.top + max(2, (tb_height - self.height()) // 2)
            self.move(x, y)
        else:
            screen = QGuiApplication.primaryScreen().geometry()
            self.move(220, screen.height() - 46)

    def init_timers(self):
        # 250ms 刷新一次歌词进度与 UI
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.tick_ui)
        self.ui_timer.start(250)

        # 2 秒检测一次系统媒体状态
        self.media_timer = QTimer(self)
        self.media_timer.timeout.connect(self.poll_media)
        self.media_timer.start(2000)

        # 30 秒从 GitHub Gist 同步一次手机端待办并检查到期时间
        self.todo_timer = QTimer(self)
        self.todo_timer.timeout.connect(self.sync_todos_async)
        self.todo_timer.start(30000)

    # ==================== 媒体与歌词更新 ====================
    def poll_media(self):
        info = MediaDetector.poll_current_media()
        if not info or not info.get("title"):
            self.is_playing = False
            return

        title = info["title"]
        artist = info.get("artist", "")
        song_key = f"{title} - {artist}".strip(" -")
        self.is_playing = info.get("is_playing", True)

        if song_key != self.current_song_key:
            self.current_song_key = song_key
            self.play_start_wallclock = time.time()
            self.lyrics = []
            self.hot_comments = []
            self.primary_text = f"🎵 正在播放：{song_key}"
            self.secondary_text = "正在拉取网易云歌词与高赞热评..."
            threading.Thread(
                target=self._fetch_netease_worker,
                args=(song_key, title, artist),
                daemon=True,
            ).start()

        if info.get("position", -1.0) >= 0:
            self.current_pos_sec = info["position"]
            self.play_start_wallclock = time.time() - self.current_pos_sec
        else:
            self.current_pos_sec = time.time() - self.play_start_wallclock

    def _fetch_netease_worker(self, song_key: str, title: str, artist: str):
        bundle = self.netease.fetch_song_bundle(title, artist)
        self.signals.song_loaded.emit(
            song_key, bundle.get("lyrics", []), bundle.get("hot_comments", [])
        )

    def on_song_loaded(self, song_key: str, lyrics: list, comments: list):
        if song_key != self.current_song_key:
            return
        self.lyrics = lyrics
        self.hot_comments = comments
        self.comment_idx = 0

    # ==================== GitHub Gist 待办同步与提醒 ====================
    def sync_todos_async(self):
        token = self.config.get("gh_token", "").strip()
        gist_id = self.config.get("gist_id", "").strip()
        if not token or not gist_id:
            return
        threading.Thread(
            target=self._fetch_gist_worker, args=(token, gist_id), daemon=True
        ).start()

    def _fetch_gist_worker(self, token: str, gist_id: str):
        try:
            url = f"https://api.github.com/gists/{gist_id}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
            resp = requests.get(url, headers=headers, timeout=6)
            if resp.status_code == 200:
                files = resp.json().get("files", {})
                if GIST_FILENAME in files:
                    content = json.loads(files[GIST_FILENAME]["content"])
                    self.signals.todos_updated.emit(content.get("todos", []))
        except Exception:
            pass

    def on_todos_updated(self, todos: list):
        self.todos = todos
        self.check_due_reminders()

    def check_due_reminders(self):
        now_hm = datetime.datetime.now().strftime("%H:%M")
        pending = [t for t in self.todos if not t.get("done")]
        for item in pending:
            remind_at = item.get("remind_at", "")
            item_id = item.get("id", "")
            if remind_at and remind_at <= now_hm and item_id not in self.reminded_ids:
                self.reminded_ids.add(item_id)
                self.alert_todo = item
                break

    def mark_todo_done_cloud(self, todo_id: str):
        for t in self.todos:
            if t.get("id") == todo_id:
                t["done"] = True
        if self.alert_todo and self.alert_todo.get("id") == todo_id:
            self.alert_todo = None

        token = self.config.get("gh_token", "").strip()
        gist_id = self.config.get("gist_id", "").strip()
        if not token or not gist_id:
            return

        def _patch():
            try:
                requests.patch(
                    f"https://api.github.com/gists/{gist_id}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={
                        "files": {
                            GIST_FILENAME: {
                                "content": json.dumps(
                                    {
                                        "updated_at": datetime.datetime.now().isoformat(),
                                        "todos": self.todos,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                            }
                        }
                    },
                    timeout=6,
                )
            except Exception:
                pass

        threading.Thread(target=_patch, daemon=True).start()

    # ==================== 状态机与界面重绘 ====================
    def tick_ui(self):
        now = time.time()
        # 每 10 秒轮换一次热评或待办索引
        if now - self.last_rotate_time >= 10.0:
            self.last_rotate_time = now
            self.comment_idx += 1
            self.todo_idx += 1

        pending_todos = [t for t in self.todos if not t.get("done")]

        # 1. 若有到点强提醒待办，优先高亮闪烁显示
        if self.alert_todo:
            self.flash_state = not self.flash_state
            self.primary_text = f"⏰ 到点待办提醒：{self.alert_todo.get('text')}"
            self.secondary_text = "👉 左键点击此任务栏直接标记为「已完成」并同步回手机"
            self.update()
            return

        # 2. 根据当前模式计算主副行文字
        current_lyric = self._get_current_lyric_line()
        current_comment = (
            self.hot_comments[self.comment_idx % len(self.hot_comments)]
            if self.hot_comments
            else "💬 暂无热评，享受纯粹音乐时刻"
        )
        if pending_todos:
            td = pending_todos[self.todo_idx % len(pending_todos)]
            prefix = "🔥" if td.get("priority") == "high" else "📋"
            time_tag = f" ({td['remind_at']})" if td.get("remind_at") else ""
            current_todo_str = f"{prefix} 待办({len(pending_todos)})：{td.get('text')}{time_tag}"
        else:
            current_todo_str = "✅ 今日待办已全部清空"

        if self.display_mode == "lyrics":
            self.primary_text = current_lyric
            self.secondary_text = f"🎵 {self.current_song_key or '未播放'}"
        elif self.display_mode == "comments":
            self.primary_text = current_comment
            self.secondary_text = f"🎵 {self.current_song_key or '网易云热评'}"
        elif self.display_mode == "todos":
            self.primary_text = current_todo_str
            self.secondary_text = "📱 手机端更新后自动同步至此处"
        else:
            # 智能混排模式 (auto)：播放时主行歌词，副行交替展示「高赞热评」与「待办事项」
            if self.is_playing and self.current_song_key:
                self.primary_text = current_lyric
                show_todo_sub = (int(now // 8) % 2 == 0) and bool(pending_todos)
                self.secondary_text = current_todo_str if show_todo_sub else current_comment
            else:
                self.primary_text = current_todo_str
                self.secondary_text = current_comment

        self.update()

    def _get_current_lyric_line(self) -> str:
        if not self.lyrics:
            return f"🎵 {self.current_song_key or '等待播放音乐...'}"
        elapsed = time.time() - self.play_start_wallclock
        timestamps = [item[0] for item in self.lyrics]
        idx = bisect.bisect_right(timestamps, elapsed) - 1
        if idx >= 0:
            return f"🎶 {self.lyrics[idx][1]}"
        return f"🎶 {self.lyrics[0][1]}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制圆角半透明毛玻璃感胶囊背景
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
        if self.alert_todo and self.flash_state:
            bg_color = QColor(225, 29, 72, 215)  # 到点提醒醒目红
        else:
            bg_color = QColor(15, 23, 42, 185)   # 深色任务栏融合底色
        painter.fillPath(path, bg_color)

        # 主行文字（歌词 / 到点提醒）
        painter.setPen(QColor(248, 250, 252))
        font_main = QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold)
        painter.setFont(font_main)
        fm_main = painter.fontMetrics()
        elided_main = fm_main.elidedText(
            self.primary_text, Qt.TextElideMode.ElideRight, self.width() - 20
        )
        painter.drawText(10, 19, elided_main)

        # 副行文字（高赞乐评 / 手机待办轮播）
        painter.setPen(QColor(148, 163, 184) if not self.alert_todo else QColor(254, 226, 226))
        font_sub = QFont("Microsoft YaHei UI", 8)
        painter.setFont(font_sub)
        fm_sub = painter.fontMetrics()
        elided_sub = fm_sub.elidedText(
            self.secondary_text, Qt.TextElideMode.ElideRight, self.width() - 20
        )
        painter.drawText(10, 36, elided_sub)

    # ==================== 鼠标交互（拖拽、点击完成、右键菜单） ====================
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.alert_todo:
                todo_id = self.alert_todo.get("id")
                self.mark_todo_done_cloud(todo_id)
                self.tick_ui()
                return
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "_drag_pos"):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self.config["offset_x"] = new_pos.x()
            self.save_config()

    def wheelEvent(self, event):
        """鼠标滚轮快速切换下一条热评或待办"""
        self.comment_idx += 1
        self.todo_idx += 1
        self.tick_ui()

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        mode_menu = menu.addMenu("🔀 切换显示模式")
        for code, label in [
            ("auto", "✨ 智能混排（歌词 + 热评 + 待办轮播）"),
            ("lyrics", "🎶 仅显示实时歌词"),
            ("comments", "💬 仅轮播高赞乐评"),
            ("todos", "📋 仅轮播手机待办"),
        ]:
            act = QAction(label, self, checkable=True)
            act.setChecked(self.display_mode == code)
            act.triggered.connect(lambda checked, c=code: self.set_mode(c))
            mode_menu.addAction(act)

        # 待办快速勾选子菜单
        todo_menu = menu.addMenu("📋 今日待办列表（点击直接完成）")
        pending = [t for t in self.todos if not t.get("done")]
        if not pending:
            empty_act = QAction("🎉 暂无未完成待办", self)
            empty_act.setEnabled(False)
            todo_menu.addAction(empty_act)
        else:
            for item in pending:
                t_act = QAction(f"⬜ {item.get('text')}", self)
                t_act.triggered.connect(
                    lambda checked, tid=item.get("id"): self.mark_todo_done_cloud(tid)
                )
                todo_menu.addAction(t_act)

        menu.addSeparator()
        cfg_act = QAction("⚙️ 配置 GitHub Gist 云同步参数", self)
        cfg_act.triggered.connect(self.open_gist_Dialog)
        menu.addAction(cfg_act)

        sync_act = QAction("🔄 立即从手机端同步待办", self)
        sync_act.triggered.connect(self.sync_todos_async)
        menu.addAction(sync_act)

        snap_act = QAction("📌 重新对齐 Win11 任务栏", self)
        snap_act.triggered.connect(self.snap_to_win11_taskbar)
        menu.addAction(snap_act)

        menu.addSeparator()
        quit_act = QAction("退出程序", self)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)

        menu.exec(event.globalPos())

    def set_mode(self, mode: str):
        self.display_mode = mode
        self.tick_ui()

    def open_gist_Dialog(self):
        token, ok1 = QInputDialog.getText(
            self,
            "配置 GitHub Token",
            "请输入与手机端一致的 GitHub Personal Access Token (gist 权限)：",
            text=self.config.get("gh_token", ""),
        )
        if not ok1:
            return
        gist_id, ok2 = QInputDialog.getText(
            self,
            "配置 Gist ID",
            "请输入手机端生成的 Gist ID：",
            text=self.config.get("gist_id", ""),
        )
        if not ok2:
            return
        self.config["gh_token"] = token.strip()
        self.config["gist_id"] = gist_id.strip()
        self.save_config()
        self.sync_todos_async()
        QMessageBox.information(self, "保存成功", "GitHub Gist 同步配置已更新，正在拉取待办！")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = TaskbarWidget()
    widget.show()
    sys.exit(app.exec())
