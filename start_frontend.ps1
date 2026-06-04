$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$Root\frontend"

if (-not (Test-Path "node_modules")) {
  npm install
}

npm run dev

