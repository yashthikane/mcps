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
# The scheduler runs as its own process so restarting Donna doesn't stop scheduled tasks.
if (Test-Path "scheduler\dist\server.js") {
    Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "$root\scripts\scheduler.ps1", "-Log")
} else {
    Write-Host "Scheduled tasks are off. Run scripts\setup-scheduler.ps1 once to enable them."
}
& venv\Scripts\python.exe -m donna @args
