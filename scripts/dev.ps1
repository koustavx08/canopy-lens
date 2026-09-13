<#
.SYNOPSIS
    Runs the CanopyLens backend and frontend development servers together.

.DESCRIPTION
    Starts uvicorn with reload on port 8000 and the Vite dev server on port
    5173, streaming both logs into this console. Ctrl+C stops both.
    Vite proxies /api to the backend, so open http://localhost:5173 only.

.PARAMETER BackendOnly
    Start only the FastAPI server.

.PARAMETER FrontendOnly
    Start only the Vite dev server.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
#>
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "No virtual environment found. Run scripts\setup.ps1 first."
}

$jobs = @()

if (-not $FrontendOnly) {
    Write-Host 'Starting API on http://127.0.0.1:8000 ...' -ForegroundColor Cyan
    $jobs += Start-Job -Name 'canopylens-api' -ScriptBlock {
        param($root, $python)
        Set-Location $root
        & $python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
    } -ArgumentList $repoRoot, $venvPython
}

if (-not $BackendOnly) {
    Write-Host 'Starting web client on http://localhost:5173 ...' -ForegroundColor Cyan
    $jobs += Start-Job -Name 'canopylens-web' -ScriptBlock {
        param($root)
        Set-Location (Join-Path $root 'frontend')
        npm run dev
    } -ArgumentList $repoRoot
}

if ($jobs.Count -eq 0) { throw 'Nothing to start.' }

Write-Host ''
Write-Host 'Open http://localhost:5173 - press Ctrl+C to stop both servers.' -ForegroundColor Green
Write-Host ''

try {
    while ($true) {
        foreach ($job in $jobs) {
            Receive-Job -Job $job
            if ($job.State -in 'Failed', 'Completed') {
                Write-Host "Job '$($job.Name)' exited with state $($job.State)." -ForegroundColor Red
                throw "Development server '$($job.Name)' stopped."
            }
        }
        Start-Sleep -Milliseconds 400
    }
}
finally {
    Write-Host 'Shutting down development servers...' -ForegroundColor Yellow
    foreach ($job in $jobs) {
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        Receive-Job -Job $job -ErrorAction SilentlyContinue
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}
