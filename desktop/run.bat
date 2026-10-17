@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 前台运行（保留控制台便于看日志）。日常使用建议用 install_autostart.ps1
rem 或打包成 exe 后启动，避免多一个黑色窗口。
python taskbar_widget.py %*
if errorlevel 1 (
  echo.
  echo [错误] 程序退出异常，请查看日志：%%APPDATA%%\Win11TaskbarLyrics\app.log
  pause
)
