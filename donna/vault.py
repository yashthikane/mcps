"""Secrets in the OS credential store (Windows Credential Manager via keyring).

WinVault rejects long values (~700-1280 chars), so anything longer than CHUNK is
split into `name#0..n` parts plus a `name#count` index written last.

On Linux (the self-hosted server) there is no desktop keyring, so secrets live in a JSON file only the
owner can read (mode 0600): DONNA_SECRETS_FILE, default ~/.config/donna/secrets.json.
"""
import json
import os
import tempfile
import threading
from pathlib import Path

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

SERVICE = os.getenv("DONNA_KEYRING_SERVICE", "donna")  # tests use a separate service name
CHUNK = 500


class FileKeyring(KeyringBackend):
    """keyring backend over one owner-only JSON file: {service: {name: value}}."""
    priority = 1  # type: ignore[assignment]

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".secrets-")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get_password(self, service: str, username: str) -> str | None:
        return self._read().get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        with self._lock:
            data = self._read()
            data.setdefault(service, {})[username] = password
            self._write(data)

    def delete_password(self, service: str, username: str) -> None:
        with self._lock:
            data = self._read()
            if username not in data.get(service, {}):
                raise PasswordDeleteError(username)
            del data[service][username]
            self._write(data)


if os.name != "nt":
    keyring.set_keyring(FileKeyring(Path(os.getenv("DONNA_SECRETS_FILE")
                                         or Path.home() / ".config" / "donna" / "secrets.json")))

GROQ_KEY = "groq_api_key"
NOTION_TOKEN = "notion_token"
GOOGLE_CLIENT = "google_client"   # {"client_id", "client_secret", ...} from credentials.json
GOOGLE_TOKEN = "google_token"     # {"refresh_token", "scopes", "email"}
POSTGRES_URL = "postgres_url"     # scheduling database, written by scripts/setup-scheduler.ps1
INTERNAL_TOKEN = "internal_api_token"  # shared by FastAPI and the scheduler service
OPENWA_KEY = "openwa_api_key"          # OpenWA's API_MASTER_KEY, written by scripts/setup-whatsapp.ps1
OPENWA_SECRET = "openwa_webhook_secret"  # HMAC secret OpenWA signs webhooks with (generated on connect)
TELEGRAM_TOKEN = "telegram_bot_token"   # from @BotFather
CLOUDFLARE_ACCOUNT = "cloudflare_account_id"  # image generation (Workers AI), donna/images.py
CLOUDFLARE_TOKEN = "cloudflare_api_token"
JIRA_SITE = "jira_site"
JIRA_EMAIL = "jira_email"
JIRA_TOKEN = "jira_api_token"     # a Workers AI API token (Read + Edit)


def _del(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def get(name: str) -> str | None:
    count = keyring.get_password(SERVICE, f"{name}#count")
    if count is None:
        return keyring.get_password(SERVICE, name)
    parts = [keyring.get_password(SERVICE, f"{name}#{i}") for i in range(int(count))]
    if any(p is None for p in parts):
        return None
    return "".join(parts)


def delete(name: str) -> None:
    count = keyring.get_password(SERVICE, f"{name}#count")
    if count is not None:
        for i in range(int(count)):
            _del(f"{name}#{i}")
        _del(f"{name}#count")
    _del(name)


def set(name: str, value: str) -> None:  # noqa: A001 - mirrors keyring's naming
    delete(name)
    if len(value) <= CHUNK:
        keyring.set_password(SERVICE, name, value)
        return
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    for i, part in enumerate(parts):
        keyring.set_password(SERVICE, f"{name}#{i}", part)
    keyring.set_password(SERVICE, f"{name}#count", str(len(parts)))


def has(name: str) -> bool:
    return bool(get(name))


def internal_token() -> str:
    """Token the scheduler uses to call /api/v1/internal/*. Generated once; env overrides (tests)."""
    token = os.getenv("DONNA_INTERNAL_API_TOKEN") or get(INTERNAL_TOKEN)
    if not token:
        import secrets
        token = secrets.token_urlsafe(32)
        set(INTERNAL_TOKEN, token)
    return token


def secret(name: str, length: int = 32) -> str:
    """A random secret stored under `name`, generated the first time it's asked for."""
    value = get(name)
    if not value:
        import secrets
        value = secrets.token_urlsafe(length)
        set(name, value)
    return value


def postgres_url() -> str | None:
    return os.getenv("DONNA_POSTGRES_URL") or get(POSTGRES_URL)


def import_from_env() -> list[str]:
    """One-time import of keys that used to live in .env. Returns what was imported."""
    imported = []
    groq = os.getenv("GROQ_API_KEY")
    if groq and not has(GROQ_KEY):
        set(GROQ_KEY, groq)
        imported.append(GROQ_KEY)
    notion = os.getenv("NOTION_API_KEY") or os.getenv("INTERNAL_INTERGRATION_TOKEN")
    if notion and not has(NOTION_TOKEN):
        set(NOTION_TOKEN, notion)
        imported.append(NOTION_TOKEN)
    return imported
