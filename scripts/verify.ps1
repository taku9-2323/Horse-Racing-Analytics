$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$PythonExecutable = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$NodeRoot = "C:\Program Files\nodejs"
$NpmExecutable = Join-Path $NodeRoot "npm.cmd"

$env:Path = "$NodeRoot;$env:Path"

Push-Location $BackendRoot
try {
    & $PythonExecutable -m mypy app
    if ($LASTEXITCODE -ne 0) { throw "Backend type checking failed." }

    & $PythonExecutable -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
}
finally {
    Pop-Location
}

Push-Location $FrontendRoot
try {
    & $NpmExecutable run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Frontend type checking failed." }

    & $NpmExecutable run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
}
finally {
    Pop-Location
}

