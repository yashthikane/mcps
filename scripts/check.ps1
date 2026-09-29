# Local quality gate: backend tests, TypeScript check and a production build of the UI.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& venv\Scripts\python.exe -m pytest -q
Push-Location web
npx tsc -b
npm run build
Pop-Location
Write-Host "All checks passed."
