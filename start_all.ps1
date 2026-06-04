$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$Root\start_backend.ps1`""
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$Root\start_frontend.ps1`""

Write-Host "Backend:  http://127.0.0.1:8787/docs"
Write-Host "Frontend: http://127.0.0.1:5173"

