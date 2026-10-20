# v2.0.0 — 任务栏歌词 → AI 任务管家

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）；手机端 PWA 已同步部署。

## 里程碑

v2.0.0 汇总 v1.3.0–v1.3.8 的全部能力，形成完整的「任务栏 + AI 任务管家」：

### 🎵 任务栏
歌词逐行滚动、网易云精选热评、下一项待办、到点提醒、全屏自动隐藏、歌词同步微调、开机自启与托盘。

### ✅ 待办 + AI 规划
- 目标完成日期、父子任务进度；
- **长期任务规划**：期限/每天可投入/拆分粒度/周末安排，AI 拆成带日期子任务；
- **更新任务规划**：按进度重排，保留已完成子任务，只替换未完成部分；
- **高峰/空闲调度**：北京时间工作日 09:00–12:00、14:00–18:00（不含法定节假日）为高峰，不调用 AI；其余时间空闲；
- 高峰任务自动排队，空闲后自动用 `deepseek-flash` 执行。

### ✅ 完成闭环与归档
- 点击完成弹窗记录完成时间、完成情况、心得；
- 本机 `archive.json` 跨删除保留完成记录；
- 每日 04:00 记忆仍包含已删除的完成项；未完成/逾期做快照；
- 推送 `memories/YYYY-MM-DD.md` + `archive/YYYY-MM-DD.json` 到私有仓库。

### 💰 记账 / 周报 / 记忆
- 精确到分钟的收支、独立账本 Gist、本周汇总；
- Gmail 周报（完成/未完成/逾期/改进建议/账单/AI 评价）；
- 每日 04:00 记忆推送（前一天），失败自动重试。

### 📱 手机 PWA（v2）
玻璃拟态界面、2×2 标签、目标日期、子任务进度、账本、完成记录弹窗；SW 缓存 `tbl-shell-v8`。

### 🔒 协议与可靠性
schema v4（`done_note`/`done_reflection`）、墓碑合并、ETag 轮询、失败重试、节假日缓存、单实例、开机自启。

## 测试与验证

- Python **248 个** + 前端协议 **46 个**用例全部通过；
- 桌面端语法编译、静态检查、无界面自检通过；打包 exe `--selftest` 通过；
- Windows 11 真机验证歌词/热评、待办、目标日期、完成归档、账本、记忆推送、打包与自启；
- 手机端已部署并验证 v2 页面与 SW v8。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v2.0.0-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 升级说明

- 桌面端与手机 PWA 请一起升级到 v2.0.0（schema v4）；
- 旧数据自动补齐 `done_note` / `done_reflection`，不会丢待办；
- AI / Gmail / 记忆仓库需要自备凭据，只保存在本机 `config.json`。

## 文件校验

- 打包版本：v2.0.0
- 发布文件：`Win11TaskbarLyrics-v2.0.0-onedir.zip`（53 MB 压缩包）
- SHA256：`02448B6A5F33A9C8D8EF534D29040A30E5DB2D69D91D850985E95452EF6EFD06`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
