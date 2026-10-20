# v1.3.3 — 移动端界面优化 + 记账精确到分钟

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）；手机端 PWA 已同步部署。

## 本次更新

### 记账记录具体时间
- 账目新增 `time`（`HH:MM`）：手机端「账本」页可直接选时间，电脑端「💰 记一笔」新增时间选择；
- 本周账单、周报邮件、每日记忆都会显示每笔账的日期 + 时间；
- 旧账目没有时间时会从 `created_at` 自动反推，不会丢数据；
- 两端 canonical 已对齐（`time` 位于 `note` 与 `type` 之间）。

### 手机端界面优化
- 顶部标签改为 **2×2 网格**：未完成 / 全部 / 已完成 / 账本全部一屏可见，不再横向溢出；
- 添加待办：输入→日期/时间/优先级一行→目标日期→整行大按钮；
- 账本表单：日期 + 时间一行、金额大字输入、分类快捷 chips（餐饮/交通/购物/学习/娱乐/工资/其他）、整行「记一笔」；
- 输入框 16px 防止 iOS 自动缩放；按钮最小 44px；适配刘海屏安全区；
- 状态胶囊、标题与卡片间距、圆角、列表字号统一优化；Service Worker 缓存升到 `tbl-shell-v5`。

### 测试与部署
- Python **229 个** + 前端协议 **46 个**用例全部通过；
- 手机端已部署：<https://jinnn.xyz/taskbar-todo/>（线上已验证包含 `ledgerTime` / `ledgerChips` 与 `tbl-shell-v5`）；
- 桌面端 v1.3.3 已安装并运行。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.3-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 手机端更新

打开 <https://jinnn.xyz/taskbar-todo/> 刷新或重开页面（Service Worker 会更新到 v5）；账本同步需要填与电脑端一致的 Token + 账本 Gist ID。

## 文件校验

- 打包版本：v1.3.3
- 发布文件：`Win11TaskbarLyrics-v1.3.3-onedir.zip`（52.9 MB 压缩包）
- SHA256：`6EC1B6723700E10AA446A2CC7DDB9B90D1C514BD9937C7F4ED16DBF8D4EC0456`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
