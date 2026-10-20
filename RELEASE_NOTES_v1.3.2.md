# v1.3.2 — 凌晨 4 点推前一天 + AI 只在空闲时段调用

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **每日记忆改为每天凌晨 04:00 推送“前一天”**：
  - 例如 10-21 04:00 推送 `memories/2026-10-20.md`（内容为 10-20 的完成/未完成/账单/评价）；
  - **自动**执行，不需要你每天打开电脑手动推；如果 04:00 时电脑关机/程序没运行，下次启动会自动补推（`last_memory_date` 记录已推送日期，同一天不会重复）；
  - 也可右键「🧠 立即推送今日记忆」手动触发。
- **AI 只在空闲时段调用**：
  - 默认安静时段 **00:30–08:30**（DeepSeek 低谷时段），或系统 5 分钟无键盘/鼠标输入；
  - 不满足条件时自动改用**本地规则评价**，不调用 API、不消耗 token；
  - 手动「🤖 AI 拆解复杂任务」如果在非空闲时段，会先弹窗确认。
- **模型切换为 `deepseek-flash`**：实测 `/models` 返回 `deepseek-flash` / `deepseek-v4-pro`，flash 调用验证通过（0.59s）；设置里可随时改。
- **实测**：
  - 19:41 重启后自动补推 `memories/2026-10-19.md`（当时非空闲，使用规则评价）；
  - Gmail 测试邮件发送成功（你的 SMTP 配置已验证）；
  - 记忆仓库现有 `2026-10-19.md` 与 `2026-10-20.md`。
- 周报邮件仍默认周日 21:00；如果希望周报也用 flash 评价，把发送时间改到 00:30–08:30 即可。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.2-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.3.2
- 发布文件：`Win11TaskbarLyrics-v1.3.2-onedir.zip`（52.9 MB 压缩包）
- SHA256：`96E31FCB4ABE0FCC26E91039BFD7A352497D55ABAE44E7F18497114DF3402C51`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 04:00 时程序没运行不会实时推送，会在下次启动补推（补推的是前一天）；
- AI 空闲判断依据系统键鼠输入 + 安静时段；如果 04:00 你仍在用电脑，会先用规则评价，不调用 API；
- 规则评价是固定文本，不如 AI 评价细腻；在空闲时段运行且配置 Key 时会自动使用 AI。
