"""OpenWA (the WhatsApp Web gateway) managed by Donna, so Connections → WhatsApp → Connect does it all:

1. check Node.js (22.19+) and git;
2. first time only: clone the pinned OpenWA tag into external/openwa, `npm ci`, `npm run build`, write its
   non-secret .env, and generate its API key into the vault (a few minutes: it downloads Chromium);
3. start it on 127.0.0.1:2785 as a background process that outlives Donna (log: data/openwa.log). The API
   key is passed as process env, never written to disk, and scripts/openwa-localhost.cjs keeps it on loopback;
4. the ChannelManager then creates the session and registers the webhook, and the dashboard shows the QR code.

Donna is the only thing that starts OpenWA (also at startup when WhatsApp is connected), so two copies
never race for the port. scripts/setup-whatsapp.ps1 and scripts/openwa.ps1 call the same code.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

import httpx

from . import DATA_DIR, ROOT, vault
from .hub import resolve_command

log = logging.getLogger("donna.openwa")
VERSION = "v0.30.3"
REPO = "https://github.com/rmyndharis/OpenWA.git"
DIR = ROOT / "external" / "openwa"
PRELOAD = ROOT / "scripts" / "openwa-localhost.cjs"
PORT = 2785
MIN_NODE = (22, 19)
# Dependencies whose install scripts OpenWA needs (see install()); better-sqlite3 and cpu-features would
# compile native code, and @scarf/scarf only sends telemetry.
SCRIPTED = ["puppeteer", "protobufjs", "msgpackr-extract", "@parcel/watcher", "unrs-resolver"]
ENV_FILE = """# Written by Donna (donna/openwa.py). Secrets are passed as process env when Donna starts OpenWA, never stored here.
PORT=2785
DATABASE_TYPE=sqlite
ENGINE_TYPE=whatsapp-web.js
SESSION_DATA_PATH=./data/sessions
STORAGE_TYPE=local
AUTO_START_SESSIONS=true
REDIS_ENABLED=false
QUEUE_ENABLED=false
CACHE_ENABLED=false
# Donna's webhook is on 127.0.0.1, which OpenWA's SSRF guard blocks unless allowed.
SSRF_ALLOWED_HOSTS=127.0.0.1,localhost
# WhatsApp privacy IDs (@lid): also report the sender's phone number so contacts match.
RESOLVE_LID_TO_PHONE=true
"""
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(RuntimeError):
    """A user-facing reason the gateway couldn't be installed or started."""


Log = Callable[[str], None]


# ------------------------------------------------------------------------------ checks
def installed() -> bool:
    return (DIR / "dist" / "main.js").exists()


def running(port: int = PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def problems() -> list[str]:
    """What's missing on this computer, as instructions. Empty when OpenWA can be installed."""
    out = []
    node = shutil.which("node")
    if not node:
        out.append("Install Node.js 22 LTS or newer from https://nodejs.org, then restart Donna.")
    else:
        try:
            raw = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15,
                                 creationflags=_NO_WINDOW).stdout.strip()
            version = tuple(int(x) for x in re.findall(r"\d+", raw)[:2])
        except (OSError, subprocess.SubprocessError, ValueError):
            version = (0, 0)
        if version < MIN_NODE:
            out.append(f"OpenWA needs Node.js {MIN_NODE[0]}.{MIN_NODE[1]} or newer (found {raw or 'unknown'}). "
                       "Update it from https://nodejs.org, then restart Donna.")
    if not installed() and not shutil.which("git"):
        out.append("Install Git for Windows from https://git-scm.com, then restart Donna.")
    return out


# ------------------------------------------------------------------------------ install
def _run(cmd: list[str], cwd, say: Log) -> None:
    say(f"$ {' '.join(cmd)}")
    exe = resolve_command(cmd[0])  # npm → npm.cmd on Windows
    try:
        proc = subprocess.Popen([exe, *cmd[1:]], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                creationflags=_NO_WINDOW)
    except OSError as e:
        raise SetupError(f"Couldn't run {cmd[0]}: {e}") from e
    last = ""
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip()
        if line:
            last = line
            say(line)
    if proc.wait() != 0:
        raise SetupError(f"`{' '.join(cmd)}` failed: {last[:300]}")


def install(say: Log = print) -> None:
    """Clone (or update to) the pinned version, install dependencies, build, write .env, create the key."""
    if missing := problems():
        raise SetupError(" ".join(missing))
    DIR.parent.mkdir(parents=True, exist_ok=True)
    if not (DIR / "package.json").exists():
        say(f"Downloading OpenWA {VERSION}…")
        _run(["git", "clone", "--depth", "1", "--branch", VERSION, REPO, str(DIR)], DIR.parent, say)
    else:
        say(f"Updating OpenWA to {VERSION}…")
        _run(["git", "fetch", "--depth", "1", "origin", "tag", VERSION], DIR, say)
        _run(["git", "checkout", "--force", VERSION], DIR, say)
    say("Installing dependencies (this takes a few minutes)…")
    # A plain `npm ci` compiles better-sqlite3 with node-gyp, which needs Visual Studio's C++ tools. Its
    # package already ships prebuilds/win32-x64.node (Node-API), which it loads first, so skip install
    # scripts and run only the ones OpenWA needs: puppeteer's postinstall downloads Chromium.
    _run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], DIR, say)
    say("Downloading Chromium for WhatsApp Web…")
    _run(["npm", "rebuild", *SCRIPTED, "--foreground-scripts"], DIR, say)
    _run(["node", "-e", "new (require('better-sqlite3'))(':memory:').close()"], DIR, say)  # the prebuilt loads
    # OpenWA's own postinstall patches whatsapp-web.js for current WhatsApp Web. Without it every send
    # reports "the engine returned no message" even when the message was delivered.
    say("Applying OpenWA's WhatsApp Web patches…")
    _run(["npm", "run", "postinstall"], DIR, say)
    say("Building…")
    _run(["npm", "run", "build"], DIR, say)
    (DIR / ".env").write_text(ENV_FILE, encoding="utf-8")
    vault.secret(vault.OPENWA_KEY, 40)
    say("OpenWA is installed.")


# ------------------------------------------------------------------------------ run
def _last_error(log_path) -> str:
    try:
        lines = [ln.strip() for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return "no output"
    errors = [ln for ln in lines[-60:] if re.search(r"error", ln, re.I)]
    return (errors or lines or ["no output"])[-1][:300]


_start_lock = threading.Lock()


def start(say: Log = print, timeout: float = 120) -> None:
    """Start OpenWA in the background (it keeps running when Donna stops) and wait until it answers.
    Serialised: Donna's startup and the Connect button may both ask at once, and a second copy that loses the
    port race still starts the WhatsApp engine and knocks the session offline."""
    with _start_lock:
        _start(say, timeout)


def _start(say: Log, timeout: float) -> None:
    if running():
        say("OpenWA is already running.")
        return
    if not installed():
        raise SetupError("OpenWA isn't installed yet.")
    node = shutil.which("node")
    if not node:
        raise SetupError("Node.js isn't installed.")
    (DIR / ".env").write_text(ENV_FILE, encoding="utf-8")  # also repairs an install that stopped half-way
    env = {**os.environ, "API_MASTER_KEY": vault.secret(vault.OPENWA_KEY, 40),
           # NODE_OPTIONS treats backslashes as escapes: use forward slashes for the path.
           "NODE_OPTIONS": f'--require "{PRELOAD.as_posix()}"'}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DATA_DIR / "openwa.log"
    logfile = open(log_path, "ab")  # noqa: SIM115 - handed to the child process
    say("Starting OpenWA…")
    proc = subprocess.Popen([node, "dist/main.js"], cwd=str(DIR), env=env, stdout=logfile, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, close_fds=True,
                            creationflags=_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    logfile.close()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if running():
            break
        if proc.poll() is not None:
            raise SetupError(f"OpenWA stopped while starting: {_last_error(log_path)} (full log: {log_path})")
        time.sleep(1)
    else:
        raise SetupError(f"OpenWA didn't start within {timeout:.0f} seconds. See {log_path}.")
    # The port opens before the API is ready; wait for an authenticated answer.
    key = vault.get(vault.OPENWA_KEY) or ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{PORT}/api/sessions", headers={"X-API-Key": key}, timeout=5)
            if r.status_code == 200:
                say("OpenWA is running.")
                return
            if r.status_code in (401, 403):
                raise SetupError("OpenWA is running with a different API key. Stop it (close node.exe running "
                                 "OpenWA, or restart the computer) and click Connect again.")
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SetupError(f"OpenWA started but its API didn't answer. See {DATA_DIR / 'openwa.log'}.")


# ------------------------------------------------------------------------------ the Connect button
class Setup:
    """One background "get WhatsApp ready" job, polled by the dashboard.

    phase: idle → checking → installing → starting → connecting → qr | connected (or error)."""

    def __init__(self, connect: Callable):
        self._connect = connect  # async () -> channel dict (ChannelManager.connect("whatsapp"))
        self.task: asyncio.Task | None = None
        self.phase, self.detail, self.error = "idle", "", ""
        self.lines: deque[str] = deque(maxlen=60)
        self.started_at: float | None = None

    @property
    def busy(self) -> bool:
        return bool(self.task and not self.task.done())

    def snapshot(self) -> dict:
        return {"phase": self.phase, "detail": self.detail, "error": self.error, "busy": self.busy,
                "log": list(self.lines), "installed": installed(), "running": running(), "started_at": self.started_at}

    def say(self, line: str) -> None:
        self.lines.append(line[:300])

    def _set(self, phase: str, detail: str) -> None:
        self.phase, self.detail = phase, detail
        self.say(f"— {detail}")

    def begin(self) -> dict:
        if not self.busy:
            self.error, self.started_at = "", time.time()
            self.lines.clear()
            self.task = asyncio.create_task(self._run(), name="openwa-setup")
        return self.snapshot()

    async def _run(self) -> None:
        try:
            self._set("checking", "Checking Node.js and git")
            if missing := await asyncio.to_thread(problems):
                raise SetupError(" ".join(missing))
            if not installed():
                self._set("installing", "Installing OpenWA (first time only, a few minutes)")
                await asyncio.to_thread(install, self.say)
            if not running():
                self._set("starting", "Starting the WhatsApp gateway")
                await asyncio.to_thread(start, self.say)
            self._set("connecting", "Opening the WhatsApp session")
            channel = await self._connect()
            status = channel.get("status", "")
            if status == "connected":
                self._set("connected", f"WhatsApp is connected ({channel.get('account') or 'linked'})")
            else:
                self._set("qr", "Scan the QR code with WhatsApp")
        except Exception as e:  # noqa: BLE001 - shown in the dashboard
            self.phase, self.error = "error", str(e) or type(e).__name__
            self.say(f"✕ {self.error}")
            log.warning("WhatsApp setup failed: %s", self.error)

    async def ensure_running(self) -> None:
        """At Donna's startup: bring back the gateway of an already-connected WhatsApp."""
        if installed() and not running() and not self.busy:
            try:
                await asyncio.to_thread(start, self.say)
            except SetupError as e:
                log.warning("Couldn't start OpenWA: %s", e)


def main(argv: list[str]) -> int:
    """`python -m donna openwa-setup` (install) / `openwa-start` (start in the background)."""
    try:
        if argv and argv[0] == "start":
            start()
        else:
            install()
            print("Next: start Donna, open Connections → WhatsApp → Connect, and scan the QR code.")
        return 0
    except SetupError as e:
        print(f"✕ {e}")
        return 1
