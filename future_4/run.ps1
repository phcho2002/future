<#
.SYNOPSIS
    双均线缠绕放量突破量化系统 - PowerShell 统一入口

.DESCRIPTION
    自动激活虚拟环境、切换工作目录并调用对应 Python 脚本。
    支持 install / backtest / scan / optimize / analyze 五种模式。

.EXAMPLE
    .\run.ps1 install
    .\run.ps1 backtest --symbol AU0
    .\run.ps1 scan
    .\run.ps1 optimize
    .\run.ps1 analyze --symbol TA0 --lookback 200
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("install", "backtest", "scan", "optimize", "analyze")]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ErrorActionPreference = "Stop"

# 定位项目目录与 work_ai 共享环境（Python 3.12 + D:\work_ai\.venv）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$workAiRoot = Split-Path -Parent $scriptDir
$py = Join-Path $workAiRoot ".venv\Scripts\python.exe"
Set-Location $scriptDir

if (-not (Test-Path $py)) {
    Write-Host "[run] 未找到共享环境: $py" -ForegroundColor Yellow
    Write-Host "[run] 首次请在 work_ai 根目录安装: .\run.ps1 install" -ForegroundColor Yellow
    if ($Command -ne "install") { exit 1 }
}

# 确保输出目录存在
$outputDir = Join-Path $scriptDir "output"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir | Out-Null }

switch ($Command) {
    "install" {
        Write-Host "[run] 安装 work_ai 共享依赖 (Python 3.12) ..." -ForegroundColor Cyan
        $venvDir = Join-Path $workAiRoot ".venv"
        if (-not (Test-Path $py)) {
            py -3.12 -m venv $venvDir --system-site-packages
            $py = Join-Path $venvDir "Scripts\python.exe"
        }
        & $py -m pip install --upgrade pip
        & $py -m pip install -r (Join-Path $workAiRoot "requirements.txt")
    }

    "backtest" {
        Write-Host "[run] 回测 ..." -ForegroundColor Cyan
        & $py backtest.py @ScriptArgs
    }

    "scan" {
        Write-Host "[run] 扫描最新信号 ..." -ForegroundColor Cyan
        & $py run_scan.py @ScriptArgs
    }

    "optimize" {
        Write-Host "[run] 参数优化 ..." -ForegroundColor Cyan
        & $py optimize.py @ScriptArgs
    }

    "analyze" {
        Write-Host "[run] 深度分析 ..." -ForegroundColor Cyan
        & $py analyze.py @ScriptArgs
    }

    default {
        Write-Host "未知命令: $Command" -ForegroundColor Red
        exit 1
    }
}
