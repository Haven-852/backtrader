# start-backend.ps1 - FastAPI (uvicorn) from ./backend
# ASCII-only output: avoids PowerShell 5.x misparsing UTF-8 scripts without BOM.

$ProjectRoot = $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
Set-Location $ProjectRoot

Write-Host "=== Yuxi Backtrader API (FastAPI) ===" -ForegroundColor Cyan
Write-Host "Task: Task-20260427-001" -ForegroundColor Yellow
Write-Host "ProjectRoot: $ProjectRoot" -ForegroundColor Gray
Write-Host "BackendDir:  $BackendDir" -ForegroundColor Gray

$PythonExe = "python"
$VenvPython = Join-Path $BackendDir "venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
    Write-Host "[OK] Using venv: $PythonExe" -ForegroundColor Green
} else {
    try {
        $pythonVersion = & python --version 2>&1
        Write-Host "[OK] Python: $pythonVersion" -ForegroundColor Green
    } catch {
        Write-Host "[ERROR] Python not found in PATH" -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path (Join-Path $BackendDir "venv"))) {
    Write-Host "Creating venv and installing deps..." -ForegroundColor Yellow
    & $PythonExe -m venv (Join-Path $BackendDir "venv")
    $PythonExe = Join-Path $BackendDir "venv\Scripts\python.exe"
    & $PythonExe -m pip install -r (Join-Path $BackendDir "requirements.txt")
    Write-Host "[OK] venv ready" -ForegroundColor Green
}

$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Copy-Item $EnvFile (Join-Path $BackendDir ".env") -Force
    Write-Host "[OK] Copied .env to backend\.env" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Starting FastAPI on http://0.0.0.0:8000" -ForegroundColor Cyan
Write-Host "Docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow

Write-Host "[START] uvicorn ..." -ForegroundColor Cyan
& $PythonExe -m uvicorn main:app `
    --host 0.0.0.0 --port 8000 --reload `
    --app-dir $BackendDir `
    --reload-dir $BackendDir
