# v1.2.6 — B 站客户端全屏时自动隐藏（按主显示器检测）

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **修复“B 站客户端全屏时组件仍不隐藏”**：
  - 根因：B 站客户端（Electron/Chromium）全屏时，它的窗口**不一定在前台**；旧逻辑只检查“前台窗口是否全屏”，所以漏判；
  - 现在**只看主显示器**（不枚举所有显示器）：每秒扫描主屏 z 序里的窗口，只要任意一个窗口铺满主显示器就隐藏，不要求它是前台；扫描到桌面（Progman/WorkerW）即停止，避免把壁纸/装饰层当成全屏；
  - 保留另外两条判据：①前台窗口铺满主显示器；②主任务栏那一行被别的窗口盖住/自动隐藏。
- 防抖：连续 2 秒确认才隐藏，连续 3 秒确认恢复，避免闪一下。
- 真机验证：你的 B 站客户端全屏窗口（非前台）打开时，日志输出
  `全屏检测：前台全屏=False 主任务栏被覆盖=False 主屏有全屏窗口=True`，2 秒内组件隐藏；退出全屏后恢复。
- 副屏全屏不会影响主屏组件（按你的要求只检测主显示器）。
- 保留右键「🖥️ 全屏应用时自动隐藏」开关。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.6-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.6
- 发布文件：`Win11TaskbarLyrics-v1.2.6-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`B224F30037BCB54167BF5C3F7F3D3EB67ECE79EA1E53BC03C9DB72AC21C6FB90`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 只检测**主显示器**：副屏全屏不会隐藏主屏组件；
- 日志会打印三条判据的实时值，排查时可直接看 `%APPDATA%\Win11TaskbarLyrics\app.log`。
