# One-time setup for scheduled tasks: Redis in WSL Ubuntu, a PostgreSQL database and role for Donna,
# and the Node scheduler service. Safe to run again (it rotates the database password).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = "$root\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run scripts\start.ps1 once first: it creates the Python environment." }

# ---------------------------------------------------------------- 1. Redis (WSL Ubuntu)
Write-Host "1/4  Redis in WSL Ubuntu..."
wsl -d Ubuntu -u root -- sh -c "command -v redis-server >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server)"
if ($LASTEXITCODE -ne 0) { throw "Installing Redis in WSL Ubuntu failed." }
# Donna starts redis-server itself (scripts\scheduler.ps1) with its own data folder.
wsl -d Ubuntu -u root -- sh -c "systemctl disable --now redis-server >/dev/null 2>&1; mkdir -p /var/lib/redis-donna && chown redis:redis /var/lib/redis-donna"

# ---------------------------------------------------------------- 2. PostgreSQL
Write-Host "2/4  PostgreSQL database 'donna'..."
$pgDir = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
    Sort-Object { [int]($_.Name -replace '\D', '') } -Descending | Select-Object -First 1
if (-not $pgDir) { throw "PostgreSQL isn't installed in C:\Program Files\PostgreSQL." }
$psql = Join-Path $pgDir.FullName "bin\psql.exe"

$secure = Read-Host "Password of the PostgreSQL superuser 'postgres'" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

$bytes = New-Object byte[] 24
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$dbPassword = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
try {
    $role = & $psql -U postgres -h 127.0.0.1 -w -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'donna'"
    if ($LASTEXITCODE -ne 0) { throw "Couldn't sign in to PostgreSQL as 'postgres'. Check the password." }
    $verb = if ("$role".Trim() -eq "1") { "ALTER" } else { "CREATE" }
    & $psql -U postgres -h 127.0.0.1 -w -qc "$verb ROLE donna WITH LOGIN PASSWORD '$dbPassword'" | Out-Null
    $db = & $psql -U postgres -h 127.0.0.1 -w -tAc "SELECT 1 FROM pg_database WHERE datname = 'donna'"
    if ("$db".Trim() -ne "1") { & $psql -U postgres -h 127.0.0.1 -w -qc "CREATE DATABASE donna OWNER donna" | Out-Null }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
# The connection URL goes to Windows Credential Manager (via stdin, never the command line).
"postgresql://donna:$dbPassword@127.0.0.1:5432/donna" |
    & $py -c "import sys; from donna import vault; vault.set(vault.POSTGRES_URL, sys.stdin.read().strip())"
Remove-Variable dbPassword

# ---------------------------------------------------------------- 3. Scheduler service
Write-Host "3/4  Scheduler service (npm install + build)..."
Push-Location scheduler
npm install --no-audit --no-fund
npm run build
Pop-Location

# ---------------------------------------------------------------- 4. Tables
Write-Host "4/4  Creating the scheduling tables..."
& $py -m donna migrate
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }

Write-Host "Done. Start Donna with scripts\start.ps1; scheduled tasks are on the Tasks page."
