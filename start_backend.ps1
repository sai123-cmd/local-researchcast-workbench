$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv")) {
  Write-Host "Missing .venv. Running install.ps1 first."
  .\install.ps1
}

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload --app-dir ".\backend"

