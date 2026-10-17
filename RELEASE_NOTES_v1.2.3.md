# v1.2.3 — 全屏应用自动隐藏

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **全屏时自动隐藏**：看 B 站/网页视频按 **F11**、B 站网页全屏能覆盖整个屏幕时、或运行全屏游戏时，任务栏组件自动隐藏；退出全屏 1 秒内自动恢复，并重新截图任务栏背景（避免底色残留）。
- 检测方式：前台窗口是否覆盖整个显示器（容差 2px）+ `SHQueryUserNotificationState` 识别 D3D 独占全屏。
- 右键菜单 / 托盘新增「🖥️ 全屏应用时自动隐藏」开关（默认开，配置项 `hide_on_fullscreen`）。
- 手动隐藏（关闭到托盘）与全屏隐藏状态分开记录，手动隐藏的组件不会被自动恢复弹出来。
- 其他沿用 v1.2.2：每天 22:00 提醒填明天待办、凌晨 2:00 自动翻篇、任务栏图标避开与自动换位、项目 Logo 图标。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.3-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.3
- 发布文件：`Win11TaskbarLyrics-v1.2.3-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`52864BE96640EB410DE34A38D0AFB5FC9CF93701011477C10F148CED38E8DB3E`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- **B 站“网页全屏”**（只占浏览器内容区、不覆盖任务栏）不算系统全屏，组件会保留；真正 F11 全屏才会隐藏。
- 多显示器：当前判断“前台窗口所在显示器是否被完全覆盖”；视频全屏在副屏、组件在主屏时不会隐藏。
- 歌词/热评来自网易云非官方接口，失败只影响这两项；跨端同步走 Gist 30 秒轮询。

## 升级说明

- 覆盖解压目录即可，配置在 `%APPDATA%\Win11TaskbarLyrics\config.json`，Token 不用重填；
- 如果之前用源码运行，直接 `python desktop\taskbar_widget.py` 即可（v1.2.3）。
