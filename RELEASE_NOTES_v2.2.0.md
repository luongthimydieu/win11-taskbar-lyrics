# v2.2.0 — 时间线待办

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）；手机端 PWA 已同步部署。

## 本次更新

### 🗓️ 电脑端：时间线待办

右键任务栏胶囊（或托盘图标）→「➕ 待办 / 记账 / 周报 / 记忆」→ **「🗓️ 时间线待办…」**

- 按目标完成日期分组；没有目标日期的按提醒日期；两者都没有的放「未排期」；
- 分组顺序：**逾期 → 今天 → 明天 → 未来 → 未排期**；
- 组内按提醒时间、紧急程度排序；
- 每条显示：提醒时间、紧急标记、父任务进度 `[done/total]`、预计耗时、子任务标记；
- 只显示未完成待办，可点「刷新」。

### 🗓️ 手机端：时间线标签

PWA 新增 **「🗓️ 时间线」标签**：

- 与电脑端相同的日期分组逻辑；
- 可以直接勾选完成（会弹完成记录窗口）、编辑、删除；
- 已完成和账本仍保留在各自标签里。

### 协议与测试

- 新增 `timelineDay` / `timelineEntries` 纯函数，桌面端 `core.timeline_groups` 与 PWA 语义一致；
- Python **249 个** + 前端协议 **47 个**用例全部通过；
- 手机 PWA 升到 v2.2.0（Service Worker `tbl-shell-v10`）。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v2.2.0-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v2.2.0
- 发布文件：`Win11TaskbarLyrics-v2.2.0-onedir.zip`（53 MB 压缩包）
- SHA256：`AC9A272760B459C885BB20C6514E0BE8DD9D13D909F4A261AAA40EC369FB2F5A`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
