# v1.2.4 — 修复任务栏组件闪烁

**发布内容**：Windows 免安装版（onedir 目录打包为 zip，解压后双击 `Win11TaskbarLyrics.exe` 即可运行）

## 本次更新

- **修复“任务栏组件一直闪烁”**：
  - 根因：每 5 秒的主题检测把“系统暗色模式”和“背景采样值”拿来比较，两者来源不同，导致每隔 5 秒误判主题变化、触发一次重新贴合；窗口被临时移出屏幕再放回，看起来就像不停闪烁；
  - 现在主题变化只更新缓存/重绘，不再重新排版。
- **全屏检测加防抖**：连续 2 秒检测到全屏才隐藏，连续 3 秒不是全屏才恢复，避免全屏切换瞬间闪一下。
- 真机验证：连续运行 45 秒只有启动时 1 次贴合，窗口位置保持不变，不再有每 5 秒的重排日志。
- 其他沿用 v1.2.3 / v1.2.2：B 站 F11 / 网页视频全屏 / 全屏游戏自动隐藏、每天 22:00 填明天待办提醒、凌晨 2:00 自动翻篇、任务栏图标自动避让、项目 Logo 图标。

## 安装 / 运行

1. 下载 `Win11TaskbarLyrics-v1.2.4-onedir.zip`，解压到任意目录（例如 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics`）；
2. 双击 `Win11TaskbarLyrics.exe`（onedir 模式，**必须整个文件夹一起**，不能只拷 exe）；
3. 右键任务栏组件 →「配置 GitHub Gist 云同步」填入 Token + Gist ID；
4. 开机自启（可选）：

   ```powershell
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -ExePath "<解压目录>\Win11TaskbarLyrics.exe"
   ```

## 文件校验

- 打包版本：v1.2.4
- 发布文件：`Win11TaskbarLyrics-v1.2.4-onedir.zip`（52.8 MB 压缩包，解压后约 141 MB）
- SHA256：`791A03EE39F440DD4905FF235F5622E1FA4F643C65466E0E944E31D76DF79914`
- 需要 Windows 11 x64；无需安装 Python / Anaconda

## 已知边界

- B 站“网页全屏”（不覆盖系统任务栏）不算系统全屏，组件会保留；真正 F11 全屏才会隐藏；
- 多显示器：当前判断“前台窗口所在显示器是否被完全覆盖”；
- 歌词/热评来自网易云非官方接口，失败只影响这两项。
