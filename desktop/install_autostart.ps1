<#
.SYNOPSIS
    把任务栏组件设为开机自启（当前用户，无需管理员权限）。
.DESCRIPTION
    在「启动」文件夹创建快捷方式，默认用 pythonw.exe 静默运行 taskbar_widget.py；
    如果已经打包了 exe，可用 -ExePath 指向 exe。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
    powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -ExePath "D:\apps\Win11TaskbarLyrics\Win11TaskbarLyrics.exe"
    powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall
#>
param(
    [string]$Python = "python",
    [string]$ExePath = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "Win11 任务栏歌词.lnk"

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "已移除开机自启：$shortcutPath" -ForegroundColor Green
    } else {
        Write-Host "没有找到开机自启项，无需移除" -ForegroundColor Yellow
    }
    exit 0
}

$workingDir = $PSScriptRoot
if ($ExePath) {
    if (-not (Test-Path -LiteralPath $ExePath)) { throw "找不到指定的 exe：$ExePath" }
    $target = (Resolve-Path -LiteralPath $ExePath).Path
    $arguments = ""
} else {
    $scriptPath = Join-Path $PSScriptRoot "taskbar_widget.py"
    if (-not (Test-Path -LiteralPath $scriptPath)) { throw "找不到 taskbar_widget.py" }

    $pythonw = $null
    $candidate = (& $Python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $candidate) {
        $trimmed = $candidate.Trim()
        if (Test-Path -LiteralPath $trimmed) { $pythonw = $trimmed }
    }
    if (-not $pythonw) {
        $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if ($found) { $pythonw = $found.Source }
    }
    if (-not $pythonw) {
        throw "找不到 pythonw.exe。请先激活正确的 Python 环境，或用 -ExePath 指定打包后的 exe。"
    }
    $target = $pythonw
    $arguments = '"' + $scriptPath + '"'
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $workingDir
$shortcut.WindowStyle = 7  # 最小化启动，避免闪现窗口
$shortcut.Description = "Win11 任务栏歌词 / 网易云热评 / 跨端待办"
$shortcut.Save()

Write-Host "已创建开机自启快捷方式：" -ForegroundColor Green
Write-Host "  $shortcutPath"
Write-Host "  -> $target $arguments"
Write-Host "如需取消：powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall"
