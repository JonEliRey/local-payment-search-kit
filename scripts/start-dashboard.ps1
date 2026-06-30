$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$LocalCli = Join-Path $Root ".venv\Scripts\payment-search.exe"
if (Test-Path $LocalCli) {
  & $LocalCli start @args
} else {
  & payment-search start @args
}
