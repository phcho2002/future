# =====================================================================
# Renko 量化系统 (future_5) — PowerShell 统一入口
# =====================================================================
param(
    [string]$Action = "scan",
    [Parameter(ValueFromRemainingArguments=$true)][string[]$Rest
)

# 统一使用 work_ai 共享环境（Python 3.12 + D:\work_ai\.venv）
$workAiRoot = Split-Path -Parent $PSScriptRoot
$py = Join-Path $workAiRoot ".venv\Scripts\python.exe"

Set-Location $PSScriptRoot

switch ($Action.ToLower()) {
    "install" {
        Write-Host "安装 work_ai 共享依赖 (Python 3.12)..."
        $venvDir = Join-Path $workAiRoot ".venv"
        if (-not (Test-Path $py)) {
            py -3.12 -m venv $venvDir --system-site-packages
            $py = Join-Path $venvDir "Scripts\python.exe"
        }
        & $py -m pip install -U pip
        & $py -m pip install -r (Join-Path $workAiRoot "requirements.txt")
    }
    "scan" {
        if (-not (Test-Path $py)) { Write-Host "未找到 $py，请先: .\run.ps1 install" -ForegroundColor Red; exit 1 }
        & $py run_scan.py @Rest
    }
    "analyze" {
        if (-not (Test-Path $py)) { Write-Host "未找到 $py，请先: .\run.ps1 install" -ForegroundColor Red; exit 1 }
        & $py analyze.py @Rest
    }
    "selftest" {
        if (-not (Test-Path $py)) { Write-Host "未找到 $py，请先: .\run.ps1 install" -ForegroundColor Red; exit 1 }
        Write-Host "=== 自检：renko.py ==="
        & $py renko.py
        Write-Host "=== 自检：signals.py ==="
        & $py signals.py
    }
    default {
        Write-Host "用法: .\run.ps1 <install|scan|analyze|selftest> [args]"
        Write-Host "  install   安装依赖"
        Write-Host "  scan      扫描 top40 全部品种，输出信号/砖块大小/图表"
        Write-Host "  analyze   单品种深度分析，例: .\run.ps1 analyze --symbol AU0"
        Write-Host "  selftest  自检 renko/signals 模块"
    }
}
