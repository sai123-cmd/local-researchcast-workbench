$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PIP_NO_COLOR = "1"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env from .env.example"
}

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

try {
  & ".\.venv\Scripts\python.exe" -m playwright install chromium
} catch {
  Write-Host "Playwright Chromium install failed. NotebookLM login may need manual repair."
}

Push-Location ".\frontend"
npm install
Pop-Location

try {
  wx --version | Out-Null
  Write-Host "wx CLI already installed"
} catch {
  Write-Host "Installing @jackwener/wx-cli globally with npm"
  npm install -g @jackwener/wx-cli
}

Write-Host ""
Write-Host "Install complete."
Write-Host "Next: run .\init_wechat_admin.ps1 in an Administrator PowerShell while WeChat is logged in."

