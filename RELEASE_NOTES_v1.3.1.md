# v1.3.1 — 记忆/周报的本地规则评价兜底

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **本地规则评价兜底**：没有配置 AI API Key 时，每日记忆与每周周报也会生成「本地规则评价」——包含完成/未完成/逾期数量、本周收支/结余、改进建议；不再显示「今日暂无 AI 评价」。配置 AI Key 后自动切换为 AI 评价。
- **定时任务失败自动重试**：每日记忆/周报发送失败后 10 分钟自动重试，并清除当天/当周的去重标记，避免代理/网络抖动导致漏发。
- **实测**：19:01 定时记忆推送成功写入 `taskbar-memory/memories/2026-10-20.md`，评价段落为：
  `（本地规则评价，未配置 AI API）今天完成 8 项，剩余 8 项未完成；本周支出 ¥0.00、结余 ¥0.00；先完成最重要的一项，比同时铺开更有效。`
- 其余功能沿用 v1.3.0：目标日期、AI 拆解、记账、周报邮件、每日记忆、schema v3、手机端账本。
- 测试：Python 227 个 + 前端协议 46 个全部通过；打包 exe `--selftest` 通过。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.3.1-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「⚙️ 配置 GitHub Gist 云同步」填 Token + Gist ID；
4. 右键「⚙️ AI / 邮件 / 记忆设置」按需填 AI Key / Gmail 应用密码 / 记忆仓库 Token；
5. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.3.1
- 发布文件：`Win11TaskbarLyrics-v1.3.1-onedir.zip`（52.9 MB 压缩包，解压后约 142 MB）
- SHA256：`8FA83B536A4B9E785BB877AC263BF4F28FF16B2CA9FE4E7FB2BC5B7BDA7B3E83`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 本地规则评价是固定规则，不如 AI 评价细腻；配置 AI Key 后自动升级为模型评价。
- 周报/记忆只在程序运行期间按设定时间触发，程序关闭时不会发送；失败后 10 分钟重试。
