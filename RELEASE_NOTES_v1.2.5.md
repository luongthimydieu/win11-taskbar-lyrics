# v1.2.5 — B 站/网页全屏时任务栏组件自动隐藏（补强）

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **修复“B 站全屏看视频时组件还挡着”**：
  - 原因：B 站网页全屏 / 客户端全屏的窗口形态不一定是“覆盖整个显示器”的经典全屏，旧版只判断前台窗口是否盖满显示器，会漏判；
  - 新判据：额外检查**任务栏那一行是否被别的窗口盖住**——取任务栏里两个不被自身遮挡的坐标点，用 `WindowFromPoint` 看命中的窗口根是不是 `Shell_TrayWnd`；被盖住/任务栏自动隐藏时也隐藏组件；
  - 样本点自动避开组件自身所在区域，拖到托盘附近也不会误判。
- **防抖**：连续 2 秒确认“全屏/任务栏被覆盖”才隐藏，连续 3 秒确认恢复，避免闪一下。
- 保留右键菜单「🖥️ 全屏应用时自动隐藏」开关。
- 真机验证：①9 秒全屏窗口：隐藏后保持，退出约 3 秒恢复；②只覆盖任务栏的窗口：同样隐藏/恢复；③正常桌面：不误隐藏。
- 其他沿用 v1.2.4：修复每 5 秒重新贴合导致的闪烁。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.5-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.5
- 发布文件：`Win11TaskbarLyrics-v1.2.5-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`CA9D11A5FE1C4EEDA148CE3F781D671C36117BA8FDB6A822C737788F7A5AA2BE`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- 多显示器：任务栏被覆盖检测针对主任务栏；副屏全屏一般由“前台窗口覆盖显示器”分支处理；
- 如果 B 站客户端使用特殊置顶窗口仍不隐藏，把 `%APPDATA%\Win11TaskbarLyrics\app.log` 最后 100 行发我，可再加“按进程名（bilibili/chrome/msedge）兜底”的判据。
