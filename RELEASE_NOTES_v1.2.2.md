# v1.2.2 — Win11 任务栏歌词 · 跨端待办

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **每天 22:00 提醒填明天的待办**（电脑托盘气泡 + 任务栏提示；手机端页面提示条，可右键关闭）
- **凌晨 2:00 自动翻篇**：只显示今天的待办；22:00–01:59 添加的待办自动算到第二天
- 逾期任务自动显示「⏰ 逾期」，不会消失
- 统一使用项目 Logo：exe 图标、托盘/窗口图标、手机端 PWA 图标
- 任务栏显示：置顶覆盖 + 任务栏背景截图，自动避让图标；白色文字无阴影
- 歌词/热评/下一项待办实时刷新；配置、Gist Token 保存在 `%APPDATA%\Win11TaskbarLyrics\`

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.2-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**不能只拷 exe**，必须整个文件夹一起）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.2
- 发布文件：`Win11TaskbarLyrics-v1.2.2-onedir.zip`
- SHA256：见 Release 页面附件旁的说明或本文件所在目录的 `SHA256SUMS.txt`
- 目录大小：约 141 MB（压缩后约 60–70 MB）；内存占用约 150–160 MB 工作集
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 覆盖模式为置顶窗口：全屏独占游戏会遮住；任务栏自动隐藏时不会跟隐；
- 歌词/热评来自网易云非官方接口，失败只影响这两项；
- 跨端同步依赖 GitHub Gist，30 秒轮询，非实时推送。

## 升级说明

- 覆盖解压目录即可，配置保存在 `%APPDATA%\Win11TaskbarLyrics\config.json`，不需要重填 Token；
- 旧版源码用户：`build_exe.ps1` 现在会自动补 `ffi.dll` 等 Anaconda DLL，并自动使用 `desktop/logo.ico`。
