#!/usr/bin/env bash
# Start Donna's scheduler (run by donna-scheduler.service). Secrets come from the secrets file into this
# process's environment only, like scripts/scheduler.ps1 does on Windows.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/venv/bin/python"
cd "$ROOT"  # so `from donna import vault` resolves

POSTGRES_URL="$("$PY" -c "from donna import vault; print(vault.postgres_url() or '')")"
[ -n "$POSTGRES_URL" ] || { echo "PostgreSQL isn't set up for Donna. Run scripts/linux/setup.sh." >&2; exit 1; }
DONNA_INTERNAL_API_TOKEN="$("$PY" -c "from donna import vault; print(vault.internal_token())")"
export POSTGRES_URL DONNA_INTERNAL_API_TOKEN
export REDIS_URL="redis://127.0.0.1:6379" DONNA_API_URL="http://127.0.0.1:8765"

cd "$ROOT/scheduler"
exec node dist/server.js
