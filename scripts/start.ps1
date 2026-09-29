# Start Donna: builds the web UI if needed, then serves everything on http://127.0.0.1:8765
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "Creating the Python virtual environment..."
    python -m venv venv
    & venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path "web\dist\index.html")) {
    Write-Host "Building the web UI (first run only)..."
    Push-Location web
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    Pop-Location
}
& venv\Scripts\python.exe -m donna @args
