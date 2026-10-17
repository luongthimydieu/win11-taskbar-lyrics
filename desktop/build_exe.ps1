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
    [switch]$OneFile
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

# 当前环境装了哪个媒体后端，就把哪个整体收集进包
& $Python -c "import winsdk" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "检测到 winsdk，打包时一并收集" -ForegroundColor DarkGray
    $pyInstallerArgs += @("--collect-all", "winsdk")
}
& $Python -c "import winrt" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "检测到 pywinrt(winrt)，打包时一并收集" -ForegroundColor DarkGray
    $pyInstallerArgs += @("--collect-all", "winrt")
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
