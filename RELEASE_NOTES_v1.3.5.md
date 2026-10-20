# v1.3.5 — AI 拆解选项 + 移动端轻奢界面

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）；手机端 PWA 已同步部署。

## 本次更新

### AI 拆解复杂任务：先选偏好再生成

右键「🤖 AI 拆解复杂任务」，现在可以选：

| 选项 | 可选项 |
| --- | --- |
| 完成期限 | **3 天 / 1 周 / 2 周 / 1 个月** 快捷按钮，或自定义 3–60 天 |
| 每天可投入 | 不限 / 30 分钟 / 1 小时 / 2 小时 / 3 小时 |
| 拆分粒度 | 简洁（3–5 步） / 适中（5–8 步） / 详细（8–12 步） |
| 周末安排 | 只在工作日 / 周末也可以（选工作日时，周末日期自动顺延到工作日） |

这些偏好会写进 AI prompt：模型按期限、每天可投入时间与粒度来安排步骤数量和日期。
高峰时段排队时也会保存选项，空闲时段自动运行沿用同一套设置。

### 移动端「轻奢」界面

- 顶部新增项目 Logo、渐变标题、状态胶囊；
- 多层径向光晕背景 + 玻璃拟态卡片、渐变按钮、渐变选中标签；
- 待办卡片悬浮/描边动效、渐变复选框；
- 账本汇总卡片按收入/支出/结余配色，分类 chips、大金额输入、安全区适配保留；
- 2×2 标签网格保留；
- Service Worker 缓存升到 `tbl-shell-v6`。

### 测试与部署

- Python **240 个**（新增 AI 选项用例）+ 前端协议 **46 个**全部通过；
- 手机端已部署：<https://jinnn.xyz/taskbar-todo/>（线上已包含 `brand-logo`、`grad-accent`、`section-head`、`tbl-shell-v6`）；
- 桌面端 v1.3.5 已安装并运行，节假日数据正常加载。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.5-onedir.zip`，解压到任意目录；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，必须整个文件夹一起）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」/「⚙️ AI / 邮件 / 记忆设置」；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 手机端更新

打开 <https://jinnn.xyz/taskbar-todo/> 刷新或重开页面，Service Worker 会更新到 v6。

## 文件校验

- 打包版本：v1.3.5
- 发布文件：`Win11TaskbarLyrics-v1.3.5-onedir.zip`（52.9 MB 压缩包）
- SHA256：`7B327B13786CDFE1C033544D3966F391B49BA3B30B19AC0E1C6C6CAB616A0B0D`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
