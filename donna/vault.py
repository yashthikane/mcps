"""Secrets in the OS credential store (Windows Credential Manager via keyring).

WinVault rejects long values (~700-1280 chars), so anything longer than CHUNK is
split into `name#0..n` parts plus a `name#count` index written last.
"""
import os

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = os.getenv("DONNA_KEYRING_SERVICE", "donna")  # tests use a separate service name
CHUNK = 500

GROQ_KEY = "groq_api_key"
NOTION_TOKEN = "notion_token"
GOOGLE_CLIENT = "google_client"   # {"client_id", "client_secret", ...} from credentials.json
GOOGLE_TOKEN = "google_token"     # {"refresh_token", "scopes", "email"}


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
