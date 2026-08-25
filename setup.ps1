# Native Windows setup for LLM Embodiments. Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.13 --version *> $null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = "py"; Arguments = @("-3.13") } }
        & py -3 --version *> $null
        if ($LASTEXITCODE -eq 0) { return @{ Executable = "py"; Arguments = @("-3") } }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Executable = "python"; Arguments = @() }
    }
    return $null
}

$PythonCommand = Get-PythonCommand
if (-not $PythonCommand) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.13+ is required. Install it from https://www.python.org/downloads/windows/ and run this script again."
    }
    Write-Host "Installing Python 3.13 with winget..."
    winget install --id Python.Python.3.13 --exact --accept-source-agreements --accept-package-agreements
    throw "Restart PowerShell so Python is available on PATH, then run .\setup.ps1 again."
}

$PythonVersion = & $PythonCommand.Executable @($PythonCommand.Arguments) --version
Write-Host "Using $PythonVersion"

$VenvPython = Join-Path $ProjectRoot "backend\venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Python virtual environment..."
    & $PythonCommand.Executable @($PythonCommand.Arguments) -m venv "backend\venv"
}

Write-Host "Installing Python dependencies..."
& $VenvPython -m pip install --upgrade pip wheel setuptools
& $VenvPython -m pip install -r "backend\requirements.txt"

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Warning "Ollama is not installed. Install it from https://ollama.com/download/windows, then run: ollama pull qwen3:14b"
} else {
    Write-Host "Ollama found: $(& ollama --version)"
}

Write-Host ""
Write-Host "Setup complete. Start the backend with: .\run.ps1"