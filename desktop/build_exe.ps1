<#
.SYNOPSIS
    用 PyInstaller 把任务栏组件打包成独立 exe。
.DESCRIPTION
    默认 --onedir（启动快，任务栏常驻组件推荐）；加 -OneFile 可生成单文件 exe（启动慢 5~10 秒）。
    会自动检测当前 Python 装了 winsdk 还是 pywinrt，并把对应媒体后端一起打包。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
    powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -OneFile
#>
param(
    [string]$Python = "python",
    [switch]$OneFile,
    [string]$Icon = ""
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "== 1/3 安装运行依赖 ==" -ForegroundColor Cyan
& $Python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Write-Host "== 2/3 安装 PyInstaller ==" -ForegroundColor Cyan
& $Python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }

Write-Host "== 3/3 打包 ==" -ForegroundColor Cyan
$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "Win11TaskbarLyrics",
    "--collect-submodules", "PyQt6.QtCore"
)
if ($OneFile) { $pyInstallerArgs += "--onefile" } else { $pyInstallerArgs += "--onedir" }
# 默认使用项目自带的 logo.ico（如果存在），无需每次手动传 -Icon
if (-not $Icon) {
    $defaultIcon = Join-Path $PSScriptRoot "logo.ico"
    if (Test-Path -LiteralPath $defaultIcon) { $Icon = $defaultIcon }
}
if ($Icon -and (Test-Path -LiteralPath $Icon)) {
    $pyInstallerArgs += @("--icon", (Resolve-Path -LiteralPath $Icon).Path)
    Write-Host "使用图标：$Icon" -ForegroundColor DarkGray
}
# 把 logo.png 一起打进去，托盘图标/窗口图标运行时读取
$logoPng = Join-Path $PSScriptRoot "logo.png"
if (Test-Path -LiteralPath $logoPng) {
    $pyInstallerArgs += @("--add-data", "$logoPng;.")
    Write-Host "内置 Logo：logo.png" -ForegroundColor DarkGray
}
# 内置中国法定节假日数据（AI 高峰/空闲判断用）
$holidaysJson = Join-Path $PSScriptRoot "holidays_cn.json"
if (Test-Path -LiteralPath $holidaysJson) {
    $pyInstallerArgs += @("--add-data", "$holidaysJson;.")
    Write-Host "内置节假日：holidays_cn.json" -ForegroundColor DarkGray
}

# 当前环境装了哪个媒体后端，就把哪个整体收集进包。
# 注意：这里用 cmd 静默探测，避免 PowerShell 把 Python 的 stderr/ImportError 当成脚本错误。
cmd /c "`"$Python`" -c `"import winsdk`" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Host "检测到 winsdk，打包时一并收集" -ForegroundColor DarkGray
    $pyInstallerArgs += @("--collect-all", "winsdk")
}
cmd /c "`"$Python`" -c `"import winrt`" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Host "检测到 pywinrt(winrt)，打包时一并收集" -ForegroundColor DarkGray
    $pyInstallerArgs += @("--collect-all", "winrt")
}

# Anaconda 环境的 Python 扩展模块依赖 Library\bin 下一些非系统 DLL（ffi.dll、
# libcrypto/libssl 等），PyInstaller 不一定自动收集；缺了会在启动时报
# "DLL load failed while importing _ctypes" 之类错误，这里显式补进去。
$prefixFile = Join-Path $env:TEMP "taskbar_py_prefix.txt"
cmd /c "`"$Python`" -c `"import sys; print(sys.prefix)`" > `"$prefixFile`" 2>nul"
if (Test-Path -LiteralPath $prefixFile) {
    $pyPrefix = (Get-Content -LiteralPath $prefixFile -TotalCount 1).Trim()
    Remove-Item -LiteralPath $prefixFile -ErrorAction SilentlyContinue
    $condaBin = Join-Path $pyPrefix "Library\bin"
    foreach ($dllName in @("ffi.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll", "libbz2.dll", "liblzma.dll", "libexpat.dll", "sqlite3.dll")) {
        $candidate = Join-Path $condaBin $dllName
        if (Test-Path -LiteralPath $candidate) {
            $pyInstallerArgs += @("--add-binary", "$candidate;.")
            Write-Host "补充 DLL：$dllName" -ForegroundColor DarkGray
        }
    }
}

$pyInstallerArgs += "taskbar_widget.py"
& $Python -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

Write-Host ""
Write-Host "打包完成！输出目录：" -ForegroundColor Green
Write-Host "  $PSScriptRoot\dist\Win11TaskbarLyrics" 
if ($OneFile) {
    Write-Host "  可执行文件：$PSScriptRoot\dist\Win11TaskbarLyrics.exe"
    Write-Host "  开机自启：powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -ExePath `"$PSScriptRoot\dist\Win11TaskbarLyrics.exe`""
} else {
    Write-Host "  可执行文件：$PSScriptRoot\dist\Win11TaskbarLyrics\Win11TaskbarLyrics.exe"
    Write-Host "  开机自启：powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -ExePath `"$PSScriptRoot\dist\Win11TaskbarLyrics\Win11TaskbarLyrics.exe`""
}
