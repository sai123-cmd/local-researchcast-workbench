$ErrorActionPreference = "Stop"

Write-Host "Checking wx CLI..."
wx --version

Write-Host "Initializing WeChat database access. Keep WeChat running and logged in."
wx init

Write-Host "Verifying sessions..."
wx sessions --json

Write-Host "Verifying incremental messages..."
wx new-messages --json

Write-Host "WeChat init verification complete."

