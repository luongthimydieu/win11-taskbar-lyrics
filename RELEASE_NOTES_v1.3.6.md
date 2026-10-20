# v1.3.6 — 完成记录 / 删除后仍归档 / 按完成时间归类

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）；手机端 PWA 已同步部署。

## 本次更新

### 点击完成时弹窗记录
- 电脑端点完成（待办列表、到点提醒点击）会弹出窗口，填写「完成情况」与「心得/反思」；
- 可「直接完成」跳过、或「取消」不完成；
- 手机端勾选完成也会弹相同窗口（PWA 已同步部署）。

### 完成时间与按天归类
- 完成时写入 `done_at`；
- 每日记忆按完成时间归类：04:00 推送前一天完成的条目（不是按创建日期）。

### 删除已完成后仍会推送到 GitHub
- 新增本机完成归档 `%APPDATA%\Win11TaskbarLyrics\archive.json`；
- 完成时立即归档；即使随后删除/「清除已完成」该待办，04:00 的记忆仍会从归档中取出并写入 GitHub；
- 每日记忆包含：当天完成（含完成情况/心得）+ 未完成/逾期快照；
- 额外推送机器可读的 `archive/YYYY-MM-DD.json`（completed / unfinished / overdue）。

### 协议 v4
- 待办新增 `done_note`（完成情况）、`done_reflection`（心得），桌面端与手机 PWA 一致；
- 旧数据自动补空字符串；canonical 键序已对齐。

### 测试
- Python **247 个**（新增完成归档 6 个 + schema v4 用例）、前端协议 **46 个**全部通过；
- 打包 exe `--selftest` 通过，完成归档已从历史完成项回填（首次启动记录 8 条）。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.6-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 手机端更新

打开 <https://jinnn.xyz/taskbar-todo/> 刷新或重开页面（Service Worker 升到 `tbl-shell-v7`）；手机勾选完成会弹出完成情况/心得窗口。

## 文件校验

- 打包版本：v1.3.6
- 发布文件：`Win11TaskbarLyrics-v1.3.6-onedir.zip`（53 MB 压缩包）
- SHA256：`5BB1B504FC8842901FE116C81046D21A791734C5BE9E5CA57393F178B1A78098`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
