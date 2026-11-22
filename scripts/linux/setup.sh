#!/usr/bin/env bash
# Set up (or update) Donna on an Ubuntu 24.04 server, e.g. the Oracle Always Free A1 VM. Safe to run again.
#
#   sudo install -d -o "$USER" -g "$USER" /opt/donna
#   git clone https://github.com/yashthikane/mcps.git /opt/donna && /opt/donna/scripts/linux/setup.sh
#
# Installs: Python venv, Node 22, PostgreSQL 18 (PGDG), Redis, Chromium (for WhatsApp), the Claude Code CLI;
# builds the web UI and the scheduler; creates the database (URL -> the secrets file); installs systemd
# services (donna, donna-scheduler, donna-backup.timer); and publishes Donna on the tailnet with
# `tailscale serve` at https://<this machine>.<tailnet>.ts.net. Everything else listens on 127.0.0.1 only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_USER="$(id -un)"
PY="$ROOT/venv/bin/python"
cd "$ROOT"
step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
[ "$(id -u)" -ne 0 ] || { echo "Run this as your normal user (it uses sudo where needed)."; exit 1; }

# ------------------------------------------------------------------------------------------ packages
step "1/9  System packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  git curl ca-certificates gnupg build-essential python3-venv python3-dev sqlite3 redis-server postgresql-common

if ! command -v node >/dev/null || ! node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a==22&&b>=19)?0:1)'; then
  step "     Node.js 22 (NodeSource)"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
fi

if ! dpkg -s postgresql-18 >/dev/null 2>&1; then
  step "     PostgreSQL 18 (PGDG)"
  sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-18
fi

command -v chromium >/dev/null || [ -x /snap/bin/chromium ] || { step "     Chromium (snap, for WhatsApp)"; sudo snap install chromium; }

if ! command -v claude >/dev/null && [ ! -x "$HOME/.local/bin/claude" ]; then
  step "     Claude Code CLI"
  curl -fsSL https://claude.ai/install.sh | bash
fi

# ------------------------------------------------------------------------------------------ redis
step "2/9  Redis (127.0.0.1 only, append-only file)"
sudo sed -i -E 's/^#?\s*appendonly .*/appendonly yes/; s/^#?\s*bind .*/bind 127.0.0.1 -::1/; s/^#?\s*protected-mode .*/protected-mode yes/' /etc/redis/redis.conf
sudo systemctl enable --now redis-server >/dev/null
sudo systemctl restart redis-server

# ------------------------------------------------------------------------------------------ python + builds
step "3/9  Python environment"
[ -x "$PY" ] || python3 -m venv venv
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt

step "4/9  Web UI build"
(cd web && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)

step "5/9  Scheduler build"
(cd scheduler && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)

# ------------------------------------------------------------------------------------------ postgres
step "6/9  PostgreSQL database 'donna'"
sudo systemctl enable --now postgresql >/dev/null
if "$PY" -c "from donna import vault; import psycopg, sys; u = vault.postgres_url(); sys.exit(0 if u and psycopg.connect(u, connect_timeout=5).close() is None else 1)" 2>/dev/null; then
  echo "Already set up."
else
  DB_PASSWORD="$(openssl rand -hex 24)"
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='donna'" | grep -q 1; then VERB=ALTER; else VERB=CREATE; fi
  sudo -u postgres psql -qc "$VERB ROLE donna WITH LOGIN PASSWORD '$DB_PASSWORD'"
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='donna'" | grep -q 1 || sudo -u postgres psql -qc "CREATE DATABASE donna OWNER donna"
  # The URL goes to the secrets file through stdin, never the command line.
  printf 'postgresql://donna:%s@127.0.0.1:5432/donna' "$DB_PASSWORD" |
    "$PY" -c "import sys; from donna import vault; vault.set(vault.POSTGRES_URL, sys.stdin.read().strip())"
  unset DB_PASSWORD
fi
"$PY" -m donna migrate

# ------------------------------------------------------------------------------------------ tailscale name
TS_HOST=""
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  TS_HOST="$(tailscale status --json | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
fi

# ------------------------------------------------------------------------------------------ systemd
step "7/9  systemd services"
PATH_LINE="$HOME/.local/bin:$ROOT/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"

sudo tee /etc/systemd/system/donna.service >/dev/null <<EOF
[Unit]
Description=Donna AI assistant (FastAPI on 127.0.0.1:8765)
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$ROOT
Environment=PATH=$PATH_LINE
Environment=PYTHONUNBUFFERED=1
Environment=DONNA_ALLOWED_HOSTS=$TS_HOST
ExecStart=$PY -m donna --no-browser
Restart=always
RestartSec=5
# Only stop Donna itself: OpenWA (WhatsApp) is a child that keeps running across Donna restarts, as on Windows.
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/donna-scheduler.service >/dev/null <<EOF
[Unit]
Description=Donna scheduler (BullMQ worker on 127.0.0.1:8766)
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$ROOT/scheduler
Environment=PATH=$PATH_LINE
ExecStart=$ROOT/scripts/linux/scheduler.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/donna-backup.service >/dev/null <<EOF
[Unit]
Description=Back up Donna's data (SQLite + PostgreSQL)

[Service]
Type=oneshot
User=$RUN_USER
Environment=PATH=$PATH_LINE
ExecStart=$ROOT/scripts/linux/backup.sh
EOF

sudo tee /etc/systemd/system/donna-backup.timer >/dev/null <<EOF
[Unit]
Description=Nightly backup of Donna's data

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

chmod +x "$ROOT/scripts/linux/"*.sh
sudo systemctl daemon-reload
sudo systemctl enable donna donna-scheduler donna-backup.timer >/dev/null
sudo systemctl restart donna donna-scheduler
sudo systemctl start donna-backup.timer

# ------------------------------------------------------------------------------------------ tailscale serve
step "8/9  Tailscale HTTPS"
if [ -n "$TS_HOST" ]; then
  sudo tailscale serve --bg --https=443 http://127.0.0.1:8765 >/dev/null
  echo "Donna: https://$TS_HOST  (only devices on your tailnet can open it)"
else
  echo "Tailscale isn't connected; skipped. Install it, run 'sudo tailscale up', then run this script again."
fi

# ------------------------------------------------------------------------------------------ check
step "9/9  Health check"
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8765/api/v1/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8765/api/v1/health >/dev/null && echo "Donna is up." || echo "Donna didn't answer: journalctl -u donna -n 50"
systemctl is-active --quiet donna-scheduler && echo "Scheduler is up." || echo "Scheduler isn't running: journalctl -u donna-scheduler -n 50"
command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ] && echo "Claude Code: sign in once with 'claude' (needed for the claude_code provider)."
