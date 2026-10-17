# v1.2.8 — 代理频繁断时 Gist 同步更稳

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **api.github.com 默认直连（绕过系统代理）**：
  - Clash/VPN 重启或线路抖动时，`requests` 会报 `ProxyError('Unable to connect to proxy', RemoteDisconnected(...))`；
  - 现在程序启动时自动把 `api.github.com` 加入 `NO_PROXY`，GitHub 同步固定走直连，不再受代理影响；
  - 配置项 `github_direct`（默认 `true`），右键菜单「🌐 GitHub API 直连（绕过代理）」可随时切换。
- **同步失败即时重试**：网络/代理瞬断时，同一轮里 2 秒后自动重试一次，再失败才告警等下一轮。
- **实测**：把 `HTTP_PROXY/HTTPS_PROXY` 指向不可用代理后启动，网易云接口按预期失败，但 **Gist 同步仍然成功**（`last_sync` 更新、`last_error` 为空）。
- 其他沿用 v1.2.7：全屏时隐藏后不再被 5 秒重排重新显示（闪烁修复）；v1.2.6 主屏全屏检测。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.8-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 代理说明（Clash for Windows）

程序默认已经让 api.github.com 直连，一般不需要改 Clash。如果你想让**所有** GitHub 请求都直连，可以：

1. 左侧「Profiles」→ 当前订阅右键 → **Edit**；
2. 在 `rules:` 最上面加：

   ```yaml
   - DOMAIN,api.github.com,DIRECT
   ```

3. 保存 → 主界面 **Reload**，并确认是 **Rule（规则）模式**；
4. 订阅会覆盖 profile 时，用 **Settings → Parsers** 加 `prepend-rules` 覆写。

验证直连：`curl.exe --noproxy "*" -sS -o NUL -w "%{http_code} %{time_total}s`n" https://api.github.com`（返回 200 且 <1s 即可）。

## 文件校验

- 打包版本：v1.2.8
- 发布文件：`Win11TaskbarLyrics-v1.2.8-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`CBFA913D3FAD3A674D83DB0B5CB811CB1279B3C63DDE2104E52019084F74DDC5`
- 需要 Windows 11 x64；无需安装 Python / Anaconda
