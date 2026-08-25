# Native Windows launcher for LLM Embodiments. Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\run.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot "backend\venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "Python environment not found. Run .\setup.ps1 first."
}

Set-Location $ProjectRoot
$listeners = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    Write-Host "Stopping existing process on port 3000 (PID $($listener.OwningProcess))..."
    Stop-Process -Id $listener.OwningProcess -Force
}

Write-Host "Starting Python backend server..."
& $VenvPython -m backend.server
exit $LASTEXITCODE