$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$ProcessPath = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $ProcessPath) {
  $ProcessPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $ProcessPath, "Process")

function Test-PortListening {
  param([int]$Port)
  return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-WorkbenchProcess {
  param(
    [string]$Name,
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$WorkingDirectory
  )

  $stdout = Join-Path $LogDir "$Name.out.log"
  $stderr = Join-Path $LogDir "$Name.err.log"
  Start-Process -FilePath $FilePath `
    -ArgumentList $ArgumentList `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr
}

Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  throw "Missing .venv. Run install.ps1 before enabling startup."
}

if (-not (Test-Path "frontend\node_modules")) {
  throw "Missing frontend\node_modules. Run install.ps1 before enabling startup."
}

if (-not (Test-PortListening -Port 8787)) {
  Start-WorkbenchProcess `
    -Name "backend" `
    -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8787", "--app-dir", "backend") `
    -WorkingDirectory $Root
}

if (-not (Test-PortListening -Port 5173)) {
  Start-WorkbenchProcess `
    -Name "frontend" `
    -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1") `
    -WorkingDirectory (Join-Path $Root "frontend")
}

