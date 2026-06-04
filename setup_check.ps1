param(
  [switch]$Json,
  [switch]$FailOnIncomplete
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp.com 65001 | Out-Null

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendUrl = "http://127.0.0.1:8787/api/settings/readiness"

function Get-ReadinessJson {
  try {
    return (Invoke-RestMethod -Uri $BackendUrl -TimeoutSec 12 | ConvertTo-Json -Depth 8)
  } catch {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
      throw "Backend is not reachable and .venv is missing. Run install.ps1 first."
    }
    $oldPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $Root "backend"
    try {
      $script = @'
import json
from app.routers.settings import readiness
print(json.dumps(readiness(), ensure_ascii=False))
'@
      return ($script | & $python -)
    } finally {
      $env:PYTHONPATH = $oldPythonPath
    }
  }
}

function Write-Item {
  param(
    [object]$Item
  )
  $prefix = if ($Item.status -eq "done") { "[OK]" } elseif ($Item.required) { "[TODO]" } else { "[OPTIONAL]" }
  Write-Host "$prefix $($Item.label): $($Item.detail)"
  if ($Item.status -ne "done") {
    Write-Host "     Next: $($Item.action)"
    if ($Item.command) {
      Write-Host "     Command: $($Item.command)"
    }
  }
}

$raw = Get-ReadinessJson
if ($Json) {
  Write-Output $raw
  exit 0
}

$readiness = $raw | ConvertFrom-Json
Write-Host "Local ResearchCast Workbench readiness"
Write-Host "Score: $($readiness.score)%"
Write-Host "Status: $($readiness.summary)"
Write-Host ""

Write-Host "Required"
$readiness.items | Where-Object { $_.required } | ForEach-Object { Write-Item $_ }

$optional = @($readiness.items | Where-Object { -not $_.required })
if ($optional.Count) {
  Write-Host ""
  Write-Host "Optional"
  $optional | ForEach-Object { Write-Item $_ }
}

if (-not $readiness.complete) {
  Write-Host ""
  Write-Host "Core setup is not complete yet. Fix the required TODO items above, then rerun setup_check.ps1."
  if ($FailOnIncomplete) {
    exit 1
  }
  exit 0
}

Write-Host ""
Write-Host "Core setup is ready."

