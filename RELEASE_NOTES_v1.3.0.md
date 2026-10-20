# v1.3.0 — 待办升级：目标日期 / AI 拆解 / 记账 / 周报 / 每日记忆

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

### 1. 待办目标日期
- 每条待办可指定未来完成日期 `due_date`；
- 任务栏显示 `📅 今天 / 明天 / MM-DD / 逾期`，未来任务不会提前占用今天；
- 手机端添加/编辑表单也支持「目标完成日期」。

### 2. AI 复杂任务拆解
- 右键「🤖 AI 拆解复杂任务」输入任务描述，调用 OpenAI 兼容 API（默认 DeepSeek）拆成多个带日期、带预计耗时的子任务；
- 自动建立父子关系，任务栏/手机端显示父任务 `[done/total]` 进度；
- 没有 API Key 时不影响其它功能。

### 3. 每日记账
- 右键「💰 记一笔 / 本周账本」记录收入/支出（分类、备注）；
- 账本独立存 `taskbar_ledger.json`（专用私密 Gist，首次启动自动创建），显示本周收入/支出/结余与明细；
- 手机 PWA 新增「💰 账本」标签页，可录入、查看本周汇总和删除。

### 4. 每周邮件周报（Gmail）
- 默认周日 21:00 自动发送；内容包括本周完成/未完成/逾期/子任务进度/改进建议/账单汇总与明细/AI 评价；
- 需在「⚙️ AI / 邮件 / 记忆设置」填 Gmail 发件邮箱 + **应用专用密码**；
- 可右键「📊 周报预览…」先看，或「📤 立即发送周报」。

### 5. 每日记忆推送到 GitHub
- 每天 23:30 把当日完成情况、账单与 AI 评价写入私有仓库 `luongthimydieu/taskbar-memory`；
- 文件：`memories/YYYY-MM-DD.md` + `memories/index.json`（倒序、同日去重、最多 400 条）；
- 需在设置里填带 `repo` 写权限的 Token；也可右键「🧠 立即推送今日记忆」。

### 6. 协议与兼容性
- 待办 schema 升到 **v3**（`due_date/parent_id/estimate_minutes`），桌面端与 PWA 的 canonical 逐字节一致；
- 账本协议独立 schema 1；旧配置自动补齐新键；
- 测试：**Python 227 个 + 前端协议 46 个**全部通过；新增 `ai_client.py` / `reporting.py` / `memory_sync.py` 与 100 个集成测试。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.0-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」填 Token + Gist ID；
4. 右键「⚙️ AI / 邮件 / 记忆设置」按需填 AI Key / Gmail 应用密码 / 记忆仓库 Token；
5. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 手机端

- 新 PWA 已部署：<https://jinnn.xyz/taskbar-todo/>（Service Worker 升到 `tbl-shell-v4`，刷新/重开页面生效）；
- 手机配置里新增「账本 Gist ID」，填与电脑端相同的账本 ID 才能同步记账。

## 文件校验

- 打包版本：v1.3.0
- 发布文件：`Win11TaskbarLyrics-v1.3.0-onedir.zip`（52.9 MB 压缩包，解压后约 142 MB）
- SHA256：`249B427328D312D213FDA7D5C448A5FA4881E5A7DEFE8AE5EF6909D0FBED919E`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- AI 拆解的日期/步骤由模型生成，可能不合理，程序会做日期范围校验，但仍建议手动调整；
- 周报/记忆只在程序运行期间按设定时间触发，程序关闭时不会发送；
- 凭据以明文保存在本机 `%APPDATA%\Win11TaskbarLyrics\config.json`，不会提交到仓库。
