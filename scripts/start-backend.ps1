$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$PythonExecutable = Join-Path $BackendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Python environment is missing. Follow the setup steps in README.md."
}

$env:HRA_DATA_DIR = Join-Path $env:LOCALAPPDATA "HorseRacingAnalytics"

Push-Location $BackendRoot
try {
    & $PythonExecutable -m uvicorn app.main:app --host 127.0.0.1 --port 8000
}
finally {
    Pop-Location
}

