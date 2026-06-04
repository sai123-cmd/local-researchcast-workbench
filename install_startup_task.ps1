$ErrorActionPreference = "Stop"

$TaskName = "Local ResearchCast Workbench"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartupScript = Join-Path $Root "start_hidden.ps1"
$StartupFolder = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
if (-not $StartupFolder) {
  $StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
}
$StartupFile = Join-Path $StartupFolder "Local ResearchCast Workbench.cmd"

if (-not (Test-Path $StartupScript)) {
  throw "Cannot find $StartupScript"
}

function Install-StartupFolderFallback {
  New-Item -ItemType Directory -Force -Path $StartupFolder | Out-Null
  $content = @(
    "@echo off",
    "start `"`" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartupScript`""
  )
  Set-Content -Path $StartupFile -Value $content -Encoding ASCII
  Write-Host "Installed startup fallback: $StartupFile"
  Write-Host "It will run: $StartupScript"
}

try {
  $Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartupScript`""
  $Trigger = New-ScheduledTaskTrigger -AtLogOn
  $Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -MultipleInstances IgnoreNew
  $Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Start Local ResearchCast Workbench backend and frontend on user logon." `
    -Force `
    -ErrorAction Stop | Out-Null

  if (Test-Path $StartupFile) {
    Remove-Item -LiteralPath $StartupFile -Force
  }

  Write-Host "Installed startup task: $TaskName"
  Write-Host "It will run: $StartupScript"
  Write-Host "You can start it now with: Start-ScheduledTask -TaskName `"$TaskName`""
} catch {
  Write-Host "Scheduled task install failed: $($_.Exception.Message)"
  Install-StartupFolderFallback
}

