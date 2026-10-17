<#
.SYNOPSIS
    一键运行本项目的全部离线验证（不需要联网、不需要 PyQt6）。
.DESCRIPTION
    依次执行：Python 语法编译 -> core 单元测试 -> 前端同步协议测试 -> sw.js 语法检查。
    如果当前 Python 环境装了 PyQt6，还会额外跑一次无界面烟测（--selftest）。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tests\run_all.ps1
    powershell -ExecutionPolicy Bypass -File .\tests\run_all.ps1 -Python "D:\APP\Anaconda\python.exe" -Node "C:\Program Files\nodejs\node.exe"
#>
param(
    [string]$Python = "python",
    [string]$Node = "node"
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$failed = 0

function Step([string]$name, [scriptblock]$action) {
    Write-Host ""
    Write-Host "== $name ==" -ForegroundColor Cyan
    & $action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   -> 失败（exit $LASTEXITCODE）" -ForegroundColor Red
        $script:failed += 1
    } else {
        Write-Host "   -> 通过" -ForegroundColor Green
    }
}

Step "1/5 Python 语法编译（desktop/core.py, taskbar_widget.py）" {
    & $Python -m py_compile (Join-Path $root "desktop\core.py") (Join-Path $root "desktop\taskbar_widget.py")
}

Step "2/5 core 单元测试（python -m unittest）" {
    Push-Location -LiteralPath $root
    try {
        & $Python -m unittest discover -s tests -p "test_*.py" -v
    } finally {
        Pop-Location
    }
}

Step "3/5 前端同步协议测试（node tests/test_web_protocol.js）" {
    & $Node (Join-Path $root "tests\test_web_protocol.js")
}

Step "4/5 Service Worker 语法检查（node --check）" {
    & $Node --check (Join-Path $root "github-pages\sw.js")
}

Write-Host ""
Write-Host "== 5/5 可选的 PyQt6 无界面烟测 ==" -ForegroundColor Cyan
$pyQtProbe = & $Python -c "import PyQt6.QtWidgets" 2>$null
if ($LASTEXITCODE -eq 0) {
    $env:QT_QPA_PLATFORM = "offscreen"
    Push-Location -LiteralPath $root
    try {
        & $Python "desktop\taskbar_widget.py" --selftest
        if ($LASTEXITCODE -ne 0) {
            Write-Host "   -> 烟测失败" -ForegroundColor Red
            $failed += 1
        } else {
            Write-Host "   -> 烟测通过" -ForegroundColor Green
        }
    } finally {
        Pop-Location
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "   -> 跳过：当前 Python 环境没有 PyQt6（可用 venv 或 conda 环境安装）" -ForegroundColor Yellow
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "有 $failed 项验证失败，请查看上面的输出。" -ForegroundColor Red
    exit 1
}
Write-Host "全部验证通过 ✅" -ForegroundColor Green
exit 0
