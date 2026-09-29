# Development: backend with auto-reload on :8765 and the Vite dev server (hot reload) on :5173.
$root = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath "$root\venv\Scripts\python.exe" -ArgumentList "-m", "donna", "--reload", "--no-browser" -WorkingDirectory $root
Set-Location "$root\web"
if (-not (Test-Path "node_modules")) { npm install }
Start-Process "http://localhost:5173"
npm run dev
