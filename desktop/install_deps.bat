@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 python。请先打开 Anaconda Prompt 并激活要用的环境，再运行本脚本。
  pause
  exit /b 1
)

echo 正在安装桌面端依赖（PyQt6 / requests / 媒体后端）...
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo [错误] 依赖安装失败。若是 Python 3.12+ 安装 winsdk 失败，请改用 pywinrt：
  echo   python -m pip install PyQt6 requests winrt-Windows.Media.Control
  pause
  exit /b 1
)

echo.
echo 依赖安装完成。运行 run.bat 启动任务栏组件。
pause
