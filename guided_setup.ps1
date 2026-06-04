param(
  [switch]$NoPrompt,
  [switch]$InstallStartup,
  [switch]$SkipNotebookLM,
  [switch]$SkipTesseract
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp.com 65001 | Out-Null

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $Root ".env"
$EnvExamplePath = Join-Path $Root ".env.example"

function Confirm-Step {
  param(
    [string]$Question,
    [bool]$Default = $true
  )
  if ($NoPrompt) {
    return $false
  }
  $suffix = if ($Default) { "Y/n" } else { "y/N" }
  $answer = Read-Host "$Question [$suffix]"
  if ([string]::IsNullOrWhiteSpace($answer)) {
    return $Default
  }
  return $answer.Trim().ToLowerInvariant().StartsWith("y")
}

function Ensure-EnvFile {
  if (-not (Test-Path $EnvPath)) {
    Copy-Item $EnvExamplePath $EnvPath
    Write-Host "Created .env from .env.example"
  }
}

function Set-EnvValue {
  param(
    [string]$Key,
    [string]$Value
  )
  Ensure-EnvFile
  $lines = @(Get-Content -Path $EnvPath -Encoding utf8)
  $escaped = if ($Value -match "\s|#|=|`"|'") { $Value | ConvertTo-Json -Compress } else { $Value }
  $found = $false
  $next = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
      $found = $true
      "$Key=$escaped"
    } else {
      $line
    }
  }
  if (-not $found) {
    $next += "$Key=$escaped"
  }
  Set-Content -Path $EnvPath -Encoding utf8 -Value $next
}

function Convert-SecureStringToPlainText {
  param([System.Security.SecureString]$Value)
  if (-not $Value -or $Value.Length -eq 0) {
    return ""
  }
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

function Get-Readiness {
  $json = & (Join-Path $Root "setup_check.ps1") -Json
  return ($json | ConvertFrom-Json)
}

function Configure-LLM {
  param([object]$Readiness)
  $item = $Readiness.items | Where-Object { $_.id -eq "llm" } | Select-Object -First 1
  if (-not $item -or $item.status -eq "done") {
    return
  }
  if (-not (Confirm-Step "Configure model API in .env now?" $true)) {
    return
  }
  $baseUrl = Read-Host "LLM_BASE_URL"
  $model = Read-Host "LLM_MODEL"
  $keySecure = Read-Host "LLM_API_KEY" -AsSecureString
  $apiKey = Convert-SecureStringToPlainText $keySecure
  if ($baseUrl) {
    Set-EnvValue "LLM_BASE_URL" $baseUrl.Trim().TrimEnd("/")
  }
  if ($model) {
    Set-EnvValue "LLM_MODEL" $model.Trim()
  }
  if ($apiKey) {
    Set-EnvValue "LLM_API_KEY" $apiKey.Trim()
  }
  Write-Host "Saved model API settings to .env. Use the System page or setup_check.ps1 to verify."
}

function Configure-NotebookLM {
  param([object]$Readiness)
  if ($SkipNotebookLM) {
    return
  }
  $item = $Readiness.items | Where-Object { $_.id -eq "notebooklm" } | Select-Object -First 1
  if (-not $item -or $item.status -eq "done") {
    return
  }
  if (-not (Confirm-Step "Run NotebookLM login now?" $true)) {
    return
  }
  $bin = Join-Path $Root ".venv\Scripts\notebooklm.exe"
  if (-not (Test-Path $bin)) {
    throw "Missing notebooklm.exe. Run install.ps1 first."
  }
  & $bin login
  & $bin auth check --test --json
}

function Configure-Tesseract {
  param([object]$Readiness)
  if ($SkipTesseract) {
    return
  }
  $item = $Readiness.items | Where-Object { $_.id -eq "ocr" } | Select-Object -First 1
  if (-not $item -or $item.status -eq "done") {
    return
  }
  $commonPath = "C:\Program Files\Tesseract-OCR\tesseract.exe"
  if (Test-Path $commonPath) {
    if ($NoPrompt) {
      Write-Host "Found Tesseract at $commonPath. Rerun without -NoPrompt to update TESSERACT_BIN."
      return
    }
    Set-EnvValue "TESSERACT_BIN" $commonPath
    Write-Host "Found Tesseract and updated TESSERACT_BIN."
    return
  }
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget -and (Confirm-Step "Install Tesseract OCR with winget now?" $false)) {
    winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements
    if (Test-Path $commonPath) {
      Set-EnvValue "TESSERACT_BIN" $commonPath
      Write-Host "Installed Tesseract and updated TESSERACT_BIN."
    } else {
      Write-Host "Tesseract install finished, but common path was not found. Set TESSERACT_BIN in .env manually."
    }
  } else {
    Write-Host "Install Tesseract OCR, then rerun guided_setup.ps1 or set TESSERACT_BIN in .env."
  }
}

function Configure-Startup {
  param([object]$Readiness)
  $item = $Readiness.items | Where-Object { $_.id -eq "startup" } | Select-Object -First 1
  if (-not $item -or $item.status -eq "done") {
    return
  }
  if ($InstallStartup -or (Confirm-Step "Install login startup task?" $false)) {
    & (Join-Path $Root "install_startup_task.ps1")
  }
}

Set-Location $Root
Ensure-EnvFile

Write-Host "Local ResearchCast Workbench guided setup"
Write-Host ""
$readiness = Get-Readiness
Write-Host "Current score: $($readiness.score)%"
Write-Host "$($readiness.summary)"
Write-Host ""

Configure-LLM $readiness
$readiness = Get-Readiness
Configure-NotebookLM $readiness
$readiness = Get-Readiness
Configure-Tesseract $readiness
$readiness = Get-Readiness
Configure-Startup $readiness

Write-Host ""
Write-Host "Final readiness check"
& (Join-Path $Root "setup_check.ps1")

