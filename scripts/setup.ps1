<#
.SYNOPSIS
    One-time development setup for CanopyLens on Windows.

.DESCRIPTION
    Creates the Python virtual environment, installs backend and ML
    dependencies, installs frontend packages, and creates .env from the example
    if it does not exist. Safe to re-run.

.PARAMETER WithDeepLearning
    Also installs CPU-only torch plus DeepForest (~2.5 GB download). The app
    works without it using the classical computer-vision engine.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
[CmdletBinding()]
param(
    [switch]$WithDeepLearning
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host '== CanopyLens setup ==' -ForegroundColor Cyan

# --- Python virtual environment ---------------------------------------------
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating virtual environment (.venv)...' -ForegroundColor Yellow
    python -m venv .venv
}
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment creation failed: $venvPython not found."
}

Write-Host 'Installing backend dependencies...' -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r backend\requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency install failed.' }

Write-Host 'Installing the canopylens_ml package (editable)...' -ForegroundColor Yellow
& $venvPython -m pip install -e .\ml
if ($LASTEXITCODE -ne 0) { throw 'canopylens_ml install failed.' }

if ($WithDeepLearning) {
    Write-Host 'Installing CPU torch + DeepForest (large download)...' -ForegroundColor Yellow
    & $venvPython -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw 'torch install failed.' }
    & $venvPython -m pip install -r backend\requirements-ml.txt
    if ($LASTEXITCODE -ne 0) { throw 'DeepForest install failed.' }
}

# --- Frontend ----------------------------------------------------------------
Write-Host 'Installing frontend dependencies...' -ForegroundColor Yellow
Push-Location frontend
npm install
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) { throw 'npm install failed.' }

# --- Environment files -------------------------------------------------------
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example' -ForegroundColor Green
}
if (-not (Test-Path 'frontend\.env')) {
    Copy-Item 'frontend\.env.example' 'frontend\.env'
    Write-Host 'Created frontend\.env from frontend\.env.example' -ForegroundColor Green
}

Write-Host ''
Write-Host 'Setup complete. Start both servers with:' -ForegroundColor Green
Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1'
