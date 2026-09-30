# Development: backend with auto-reload on :8765 and the Vite dev server (hot reload) on :5173.
$root = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList "-m", "donna", "--reload", "--no-browser" -WorkingDirectory $root
if (Test-Path "$root\scheduler\node_modules") {
    Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\scheduler.ps1", "-Dev")
}
Set-Location "$root\web"
if (-not (Test-Path "node_modules")) { npm install }
Start-Process "http://localhost:5173"
npm run dev
