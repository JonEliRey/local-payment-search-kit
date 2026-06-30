$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$Venv = if ($env:VENV_DIR) { $env:VENV_DIR } else { ".venv" }

if (-not (Test-Path $Venv)) {
  & $Python -m venv $Venv
}

& (Join-Path $Venv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $Venv "Scripts\python.exe") -m pip install -e .

Write-Host "Payment Search local kit is installed."
Write-Host "Next:"
Write-Host "  payment-search add-merchant"
Write-Host "  payment-search start"
