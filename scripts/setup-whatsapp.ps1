# Optional: install OpenWA (the local WhatsApp Web gateway) ahead of time. You don't need this:
# Connections -> WhatsApp -> Connect in Donna does the same thing (donna/openwa.py), then shows the QR code.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path "venv\Scripts\python.exe")) { throw "Run scripts\start.ps1 once first (it creates the Python environment)." }
& venv\Scripts\python.exe -m donna openwa-setup
exit $LASTEXITCODE
