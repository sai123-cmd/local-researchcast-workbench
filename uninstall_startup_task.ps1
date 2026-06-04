$ErrorActionPreference = "Stop"

$TaskName = "Local ResearchCast Workbench"
$StartupFolder = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
if (-not $StartupFolder) {
  $StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
}
$StartupFile = Join-Path $StartupFolder "Local ResearchCast Workbench.cmd"
$removed = $false

try {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed startup task: $TaskName"
    $removed = $true
  }
} catch {
  Write-Host "Could not remove startup task: $($_.Exception.Message)"
}

if (Test-Path $StartupFile) {
  Remove-Item -LiteralPath $StartupFile -Force
  Write-Host "Removed startup fallback: $StartupFile"
  $removed = $true
}

if (-not $removed) {
  Write-Host "Startup entry not installed: $TaskName"
}

