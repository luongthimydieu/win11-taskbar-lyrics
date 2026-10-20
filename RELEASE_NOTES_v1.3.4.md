# v1.3.4 — AI 高峰/空闲时段 + 复杂任务自动排队

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

### 按你的规则判断 AI 调用时段（北京时间 UTC+8）

- **高峰（不调用 AI）**：周一至周五 **09:00–12:00、14:00–18:00**，且当天不是中国法定节假日；
- **空闲（可调用）**：其余所有时间——12:00–14:00、18:00–次日 09:00、周末、法定节假日全天；
- 高峰判断使用随包的 `holidays_cn.json`（2025–2026 法定放假日，来自国务院安排 / holiday-cn），并每 7 天自动从 GitHub 刷新缓存，不需要联网也能判断。

### 复杂任务自动排队

- 高峰时段点「🤖 AI 拆解复杂任务」**不会调用 API**，任务写入本机 `%APPDATA%\Win11TaskbarLyrics\ai_queue.json`；
- 程序每 30 秒检查，进入空闲时段后自动用 `deepseek-flash` 拆解成带日期、带耗时的子任务并加入待办，托盘提示「AI 排队任务已完成」；
- 队列跨重启保留；右键「⏳ AI 排队任务（N）」可查看、取消，并可手动触发检查（只会在空闲时运行）；
- 失败自动重试：最多 3 次，间隔约 10 分钟；
- 手动拆解在空闲时段仍然即时运行；高峰时只排队。

### 其他

- 定时 AI 评价（每日记忆 / 周报）也共用同一时段规则：高峰时自动使用本地规则评价，不消耗 token；
- 模型仍为 `deepseek-flash`；
- 新增 `tests/test_ai_schedule.py`（10 个用例）；Python 共 **239 个**、前端协议 **46 个**全部通过；
- 打包验证：exe `--selftest` 通过，并从 `_internal/holidays_cn.json` 正确加载 61 条节假日数据。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.4-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.3.4
- 发布文件：`Win11TaskbarLyrics-v1.3.4-onedir.zip`（52.9 MB 压缩包）
- SHA256：`EF0DB889E5328788B060A96A4BD76B59B75255E675C8D61EE379F8BC02B57041`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
