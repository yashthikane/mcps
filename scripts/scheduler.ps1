# Start Donna's scheduler: Redis (WSL Ubuntu) if it isn't running, then the Node service on 127.0.0.1:8766.
# Secrets are read from Windows Credential Manager into this process only; nothing is written to disk.
#   -Dev  run with tsx watch (auto-reload)      -Log  append output to data\scheduler.log
param([switch]$Dev, [switch]$Log)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = "$root\venv\Scripts\python.exe"

function Test-Port([int]$port) {
    $client = New-Object Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $port); return $true } catch { return $false } finally { $client.Close() }
}

if (Test-Port 8766) { Write-Host "The scheduler is already running."; exit 0 }

if (-not (Test-Port 6379)) {
    Write-Host "Starting Redis in WSL Ubuntu..."
    # Runs in the foreground of a hidden wsl.exe, which also keeps the WSL VM from idling out.
    # The redis account has no login shell, so start as root and drop to it with runuser.
    Start-Process wsl -WindowStyle Hidden -ArgumentList @("-d", "Ubuntu", "-u", "root", "--", "runuser", "-u", "redis", "--", "redis-server",
        "--bind", "127.0.0.1", "--port", "6379", "--protected-mode", "yes",
        "--appendonly", "yes", "--dir", "/var/lib/redis-donna")
    for ($i = 0; $i -lt 40 -and -not (Test-Port 6379); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Port 6379)) { throw "Redis didn't start. Run scripts\setup-scheduler.ps1 first." }
}

$env:POSTGRES_URL = & $py -c "from donna import vault; print(vault.postgres_url() or '')"
if (-not $env:POSTGRES_URL) { throw "PostgreSQL isn't set up for Donna. Run scripts\setup-scheduler.ps1." }
$env:DONNA_INTERNAL_API_TOKEN = & $py -c "from donna import vault; print(vault.internal_token())"
$env:REDIS_URL = "redis://127.0.0.1:6379"
$env:DONNA_API_URL = "http://127.0.0.1:8765"

Set-Location "$root\scheduler"
if ($Dev) {
    npm run dev
} elseif ($Log) {
    New-Item -ItemType Directory -Force "$root\data" | Out-Null
    cmd /c "node dist\server.js >> `"$root\data\scheduler.log`" 2>&1"
} else {
    node dist\server.js
}
