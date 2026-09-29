import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class MemoryKeyring(KeyringBackend):
    """In-memory keyring so tests never touch Windows Credential Manager."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError(username)
        del self.data[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    kr = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(kr)
    for var in ("GROQ_API_KEY", "NOTION_API_KEY", "INTERNAL_INTERGRATION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    yield kr
    keyring.set_keyring(previous)


@pytest.fixture
def store(tmp_path):
    from donna.store import Store
    return Store(tmp_path / "test.db")
