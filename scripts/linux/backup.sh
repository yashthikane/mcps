#!/usr/bin/env bash
# Nightly backup (donna-backup.timer): a consistent copy of the SQLite store, a PostgreSQL dump and the
# secrets file into ~/backups/donna-YYYY-MM-DD/. Keeps the last 14. Folder is owner-only (it holds secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/venv/bin/python"
cd "$ROOT"  # so `from donna import vault` resolves
DEST="$HOME/backups/donna-$(date +%F)"
umask 077
mkdir -p "$DEST"

# The SQLite backup API copies a consistent snapshot while Donna keeps running (WAL mode).
"$PY" - "$ROOT/data/donna.db" "$DEST/donna.db" <<'EOF'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
EOF

URL="$("$PY" -c "from donna import vault; print(vault.postgres_url() or '')")"
[ -z "$URL" ] || pg_dump --dbname="$URL" --format=custom --file="$DEST/postgres.dump"

SECRETS="${DONNA_SECRETS_FILE:-$HOME/.config/donna/secrets.json}"
[ ! -f "$SECRETS" ] || cp "$SECRETS" "$DEST/secrets.json"

ls -1dt "$HOME"/backups/donna-* | tail -n +15 | xargs -r rm -rf
echo "Backed up to $DEST"
