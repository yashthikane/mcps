# Start OpenWA (the WhatsApp gateway) in the background on 127.0.0.1:2785, if it isn't running.
# Donna does this by itself (Connect, and at startup once WhatsApp is connected); this is for troubleshooting.
# Output goes to data\openwa.log. The API key is passed from Credential Manager to the process only.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& venv\Scripts\python.exe -m donna openwa-start
exit $LASTEXITCODE
