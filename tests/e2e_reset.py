"""Delete the e2e test instance's data folder and keyring entries (service "donna-e2e")."""
import os
import shutil
import sys

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "donna-e2e"
data = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "donna-e2e-data")
shutil.rmtree(data, ignore_errors=True)
for name in ("groq_api_key", "notion_token", "google_client", "google_token", "postgres_url", "internal_api_token"):
    for key in [name, f"{name}#count"] + [f"{name}#{i}" for i in range(10)]:
        try:
            keyring.delete_password(SERVICE, key)
        except PasswordDeleteError:
            pass
print("reset", data)
