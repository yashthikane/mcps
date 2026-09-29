# tools/google_auth.py — One Google sign-in shared by the Gmail and Calendar tools.
#
# The OAuth client (from credentials.json) and the refresh token live in Windows
# Credential Manager via donna.vault. Only the refresh token is stored (not the
# full token JSON) because Credential Manager rejects long values.

import json
import threading
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from donna import vault

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",   # read, drafts, labels, trash
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"

NOT_CONNECTED = "Google isn't connected yet. Open Donna → Connections → Google to sign in."
EXPIRED = "Your Google sign-in expired. Open Donna → Connections and click Re-authorize."


class GoogleAuthError(Exception):
    """Raised when Google isn't set up or the sign-in has expired."""


_lock = threading.Lock()
_creds: Credentials | None = None
_status_cache: tuple[float, dict] | None = None


def parse_client_file(raw: str | bytes) -> dict:
    """Validate a credentials.json downloaded from Google Cloud and keep only what we need."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ValueError("That file isn't valid JSON. Download the OAuth client JSON again.") from e
    section = data.get("installed") or data.get("web")
    if not section or not section.get("client_id") or not section.get("client_secret"):
        raise ValueError("This isn't an OAuth client file. Create an OAuth client ID of type 'Desktop app' and download its JSON.")
    return {
        "type": "installed" if "installed" in data else "web",
        "client_id": section["client_id"],
        "client_secret": section["client_secret"],
        "project_id": section.get("project_id", ""),
    }


def save_client(raw: str | bytes) -> dict:
    client = parse_client_file(raw)
    vault.set(vault.GOOGLE_CLIENT, json.dumps(client))
    _reset()
    return {"project_id": client["project_id"], "type": client["type"]}


def _client() -> dict | None:
    raw = vault.get(vault.GOOGLE_CLIENT)
    return json.loads(raw) if raw else None


def _token() -> dict | None:
    raw = vault.get(vault.GOOGLE_TOKEN)
    return json.loads(raw) if raw else None


def _reset() -> None:
    global _creds, _status_cache
    _creds = None
    _status_cache = None


def authorize(timeout_seconds: int = 300) -> dict:
    """Open the Google consent screen in the user's browser and store the refresh token. Blocking."""
    client = _client()
    if not client:
        raise GoogleAuthError("Upload credentials.json first.")
    config = {"installed": {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URI,
        "redirect_uris": ["http://localhost"],
    }}
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        timeout_seconds=timeout_seconds,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="",
        success_message="Donna is connected to Google. You can close this tab and return to Donna.",
    )
    if not creds.refresh_token:
        raise GoogleAuthError("Google didn't return a refresh token. Remove Donna's access at myaccount.google.com/permissions and try again.")
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    email = gmail.users().getProfile(userId="me").execute().get("emailAddress", "")
    vault.set(vault.GOOGLE_TOKEN, json.dumps({"refresh_token": creds.refresh_token, "scopes": SCOPES, "email": email}))
    with _lock:
        _reset()
    return {"email": email, "scopes": SCOPES}


def credentials() -> Credentials:
    """Authorized credentials, refreshed when needed. Raises GoogleAuthError with a user-facing message."""
    global _creds
    with _lock:
        if _creds and _creds.valid:
            return _creds
        client, token = _client(), _token()
        if not client or not token:
            raise GoogleAuthError(NOT_CONNECTED)
        creds = _creds or Credentials(
            None,
            refresh_token=token["refresh_token"],
            token_uri=TOKEN_URI,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            scopes=token.get("scopes", SCOPES),
        )
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise GoogleAuthError(EXPIRED) from e
        _creds = creds
        return creds


def service(api: str, version: str):
    """A googleapiclient service for `api` using the shared sign-in."""
    return build(api, version, credentials=credentials(), cache_discovery=False)


def status(max_age: float = 60) -> dict:
    """{"state": not_set_up | needs_auth | needs_reauth | connected, "email": ..., "project_id": ...}"""
    global _status_cache
    if _status_cache and time.time() - _status_cache[0] < max_age:
        return _status_cache[1]
    client, token = _client(), _token()
    if not client:
        result = {"state": "not_set_up"}
    elif not token:
        result = {"state": "needs_auth", "project_id": client.get("project_id", "")}
    else:
        try:
            credentials()
            result = {"state": "connected", "email": token.get("email", ""), "project_id": client.get("project_id", "")}
        except GoogleAuthError:
            result = {"state": "needs_reauth", "email": token.get("email", ""), "project_id": client.get("project_id", "")}
        except Exception as e:  # network down etc. — keep the last known good state visible
            result = {"state": "connected", "email": token.get("email", ""), "warning": str(e)[:200]}
    _status_cache = (time.time(), result)
    return result


def disconnect(forget_client: bool = False) -> None:
    vault.delete(vault.GOOGLE_TOKEN)
    if forget_client:
        vault.delete(vault.GOOGLE_CLIENT)
    with _lock:
        _reset()
