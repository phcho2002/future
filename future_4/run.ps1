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

# 定位项目根目录（脚本所在目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# 若存在虚拟环境则激活
$venvActivate = Join-Path $scriptDir ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    Write-Host "[run] 激活虚拟环境 ..." -ForegroundColor Cyan
    & $venvActivate
}
else {
    Write-Host "[run] 未找到 .venv，使用系统 Python。首次请运行: .\run.ps1 install" -ForegroundColor Yellow
}

# 确保输出目录存在
$outputDir = Join-Path $scriptDir "output"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir | Out-Null }

switch ($Command) {
    "install" {
        Write-Host "[run] 安装依赖 ..." -ForegroundColor Cyan
        if (-not (Test-Path ".venv")) {
            python -m venv .venv
            & ".venv\Scripts\Activate.ps1"
        }
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    }

    "backtest" {
        Write-Host "[run] 回测 ..." -ForegroundColor Cyan
        python backtest.py @ScriptArgs
    }

    "scan" {
        Write-Host "[run] 扫描最新信号 ..." -ForegroundColor Cyan
        python run_scan.py @ScriptArgs
    }

    "optimize" {
        Write-Host "[run] 参数优化 ..." -ForegroundColor Cyan
        python optimize.py @ScriptArgs
    }

    "analyze" {
        Write-Host "[run] 深度分析 ..." -ForegroundColor Cyan
        python analyze.py @ScriptArgs
    }

    default {
        Write-Host "未知命令: $Command" -ForegroundColor Red
        exit 1
    }
}
