# v1.2.7 — 修复全屏时“隐藏后又闪出来 / 一直闪”

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **根因**：组件全屏时 `hide()` 之后，每 5 秒一次的“任务栏重新置顶”调用了带 `SWP_SHOWWINDOW` 的 `SetWindowPos`，把刚隐藏的窗口又显示出来了；表现为“隐藏 → 5 秒后又冒出来 → 再隐藏”的循环闪烁（只检查日志会以为已经隐藏，实际窗口又被重排显示）。
- **修复**：`ensure_taskbar_position` 和 `apply_taskbar_mode` 在全屏隐藏/用户手动隐藏期间直接返回，不再做任何置顶或重排。
- **真机验证**（B 站客户端全屏窗口保持打开）：
  - v1.2.7 启动后约 2 秒隐藏；
  - 第 4 秒、第 24 秒两次检查窗口 `IsWindowVisible=False`，不再被 5 秒重排重新显示；
  - 日志不再反复出现“隐藏/恢复”。
- 沿用 v1.2.6：只检测主显示器（前台全屏 / 主屏任意窗口铺满 / 主任务栏被覆盖），连续 2 秒确认隐藏、连续 3 秒确认恢复。
- 沿用右键「🖥️ 全屏应用时自动隐藏」开关。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.7-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.7
- 发布文件：`Win11TaskbarLyrics-v1.2.7-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`08C46CBAE46D74B3CF228274E83F5DBC16F0EFE065860F9B93DE62A1366E627D`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 只检测主显示器；副屏全屏不影响主屏组件；
- 日志会打印「全屏检测：前台全屏=… 主任务栏被覆盖=… 主屏有全屏窗口=…」，排查时直接看最后 100 行即可。
