$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Url = "http://127.0.0.1:5173/"

Set-Location $Root
& (Join-Path $Root "start_hidden.ps1")

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
      $ready = $true
      break
    }
  } catch {
    Start-Sleep -Seconds 1
  }
}

Start-Process $Url

if (-not $ready) {
  Write-Warning "Local ResearchCast Workbench was started, but the browser endpoint did not respond yet. Refresh the page in a moment."
}

