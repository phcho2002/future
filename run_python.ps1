<#
.SYNOPSIS
    统一用 D:\work_ai\.venv（Python 3.12 共享环境）运行脚本。

.EXAMPLE
    .\run_python.ps1 -m pip list
    .\run_python.ps1 future_4\run_scan.py
    .\run_python.ps1 -c "import numpy,pandas; print(numpy.__version__)"
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsRest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[run_python] 未找到共享环境: $py" -ForegroundColor Red
    Write-Host "请先执行:" -ForegroundColor Yellow
    Write-Host '  py -3.12 -m venv .venv --system-site-packages'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -U pip'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

& $py @ArgsRest
exit $LASTEXITCODE
