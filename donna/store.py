"""SQLite storage for conversations, messages, MCP servers and settings (data/donna.db).

Schema changes after the base SCHEMA are numbered files in migrations/sqlite/, applied once in
order and tracked with PRAGMA user_version. Never edit an applied migration; add a new file."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import DATA_DIR, ROOT

MIGRATIONS = ROOT / "migrations" / "sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  tool_events TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id, created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, message_id UNINDEXED, conversation_id UNINDEXED);
CREATE TABLE IF NOT EXISTS mcp_servers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  transport TEXT NOT NULL,
  command TEXT NOT NULL DEFAULT '',
  args TEXT NOT NULL DEFAULT '[]',
  url TEXT NOT NULL DEFAULT '',
  env_keys TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  disabled_tools TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_SETTINGS = {
    "model": "openai/gpt-oss-120b",  # Groq model
    "llm_provider": "groq",           # groq | claude_code (the Claude Code CLI, signed in with your own subscription)
    "claude_model": "opus",           # Claude Code model: an alias (opus, sonnet, fable) or a full name
    "timezone": "",
    "reasoning_effort": "medium",
    "pixel_grid": True,
    "reduce_motion": False,
    "disabled_tools": [],        # built-in tools the user switched off
    "disabled_connections": [],  # built-in connections switched off (weather, utils)
    "usage": {"date": "", "requests": 0, "tokens": 0},  # Groq usage today (free tier: 1,000 requests/day)
    "onboarded": False,
    "permission_mode": "manual",  # chat mode: plan | manual | auto (composer switch)
    "image_model": "flux-2-klein-4b",  # image generation model, an id from images.MODELS
    # Messaging channels (WhatsApp, Telegram). Secrets live in the vault, never here.
    "messaging_enabled": True,        # global kill switch: off = nothing is answered or sent on any channel
    "require_trigger": True,          # contacts reach Donna only by writing the trigger; everything else is left for you
    "trigger_word": "@donna",
    "automations_enabled": True,      # off = scheduled tasks are skipped
    "owner_name": "",                 # how Donna refers to you when talking to contacts
    "owner_pronouns": "",             # he | she | they | "" (Donna then avoids gendered forms for you)
    "assistant_profile": "",          # what Donna may tell contacts about you (role, how to reach you)
    "default_language": "auto",       # auto = reply in the language the contact writes in
    "default_tone": "friendly and professional",
    "contact_rate_per_hour": 10,      # automatic replies per contact per hour (then drafts for approval)
    "global_rate_per_hour": 60,       # automatic replies across all contacts per hour
    "contact_images_per_day": 5,      # pictures per contact per 24 h (their "Make pictures" permission); 0 = none
    "approval_ttl_hours": 24,
    "unknown_contact_action": "ignore",  # ignore | notify: message from someone not in Contacts
    "openwa_url": "http://127.0.0.1:2785",
    "owner_channel": "whatsapp",      # where alerts, approvals and briefings reach you
    # Availability and the away greeting (donna/availability.py).
    "availability": {"mode": "available", "detail": "", "until": None, "set_at": 0},  # the status you set by hand
    "greeting_enabled": True,         # greet saved contacts (with the quick menu) while you're not available
    "greeting_mention_trigger": True, # the greeting says "or type @donna anytime"
    "urgent_notify_dnd": False,       # an "It's urgent" request also pings your phone during Do not disturb
    "urgent_notify_sleeping": False,  # … and while Sleeping (it always lands in the Inbox)
    "calendar_auto_status": True,     # "In a meeting" while a Google Calendar event is on
    "sleep_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
    "menu_options": [{"id": "message", "enabled": True}, {"id": "meeting", "enabled": True},
                     {"id": "callback", "enabled": True}, {"id": "urgent", "enabled": True}],
    "meeting_default_minutes": 30,
    "meeting_hours": {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]},  # slots Donna offers (Mon=0)
}


class Store:
    def __init__(self, path: Path | None = None):
        path = path or DATA_DIR / "donna.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for path in sorted(MIGRATIONS.glob("*.sql")):
            number = int(path.name.split("_", 1)[0])
            if number <= version:
                continue
            try:
                self.db.executescript(f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\nCOMMIT;")
            except sqlite3.Error:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            version = number

    # Public helpers for the modules that own the newer tables (contacts, channels, approvals…).
    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self._q(sql, params)

    def rows(self, sql: str, params=()) -> list[dict]:
        return self._all(sql, params)

    def row(self, sql: str, params=()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _q(self, sql: str, params=()):
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    # ---------------------------------------------------------------- conversations
    def create_conversation(self, title: str = "New chat", source: str = "web") -> dict:
        now = time.time()
        cid = uuid.uuid4().hex[:12]
        self._q("INSERT INTO conversations (id, title, pinned, created_at, updated_at, source) VALUES (?,?,?,?,?,?)",
                (cid, title, 0, now, now, source))
        return self.get_conversation(cid)

    def get_conversation(self, cid: str) -> dict | None:
        rows = self._all("SELECT * FROM conversations WHERE id=?", (cid,))
        return _conv(rows[0]) if rows else None

    def list_conversations(self) -> list[dict]:
        rows = self._all(
            "SELECT c.*, (SELECT content FROM messages m WHERE m.conversation_id=c.id AND m.role='assistant' "
            "ORDER BY created_at DESC LIMIT 1) AS preview FROM conversations c WHERE c.source='web' "
            "ORDER BY pinned DESC, updated_at DESC")
        return [_conv(r) for r in rows]

    def update_conversation(self, cid: str, title: str | None = None, pinned: bool | None = None) -> dict | None:
        if title is not None:
            self._q("UPDATE conversations SET title=? WHERE id=?", (title.strip()[:120] or "Untitled", cid))
        if pinned is not None:
            self._q("UPDATE conversations SET pinned=? WHERE id=?", (1 if pinned else 0, cid))
        return self.get_conversation(cid)

    def touch(self, cid: str) -> None:
        self._q("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))

    def delete_conversation(self, cid: str) -> None:
        self._q("DELETE FROM messages_fts WHERE conversation_id=?", (cid,))
        self._q("DELETE FROM conversations WHERE id=?", (cid,))

    # ---------------------------------------------------------------- messages
    def add_message(self, cid: str, role: str, content: str, tool_events: list | None = None) -> dict:
        mid = uuid.uuid4().hex[:12]
        now = time.time()
        self._q("INSERT INTO messages (id, conversation_id, role, content, tool_events, created_at) VALUES (?,?,?,?,?,?)",
                (mid, cid, role, content, json.dumps(tool_events or []), now))
        if content:
            self._q("INSERT INTO messages_fts(content, message_id, conversation_id) VALUES (?,?,?)", (content, mid, cid))
        self.touch(cid)
        return {"id": mid, "conversation_id": cid, "role": role, "content": content,
                "tool_events": tool_events or [], "created_at": now}

    def messages(self, cid: str) -> list[dict]:
        rows = self._all("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,))
        for r in rows:
            r["tool_events"] = json.loads(r["tool_events"])
        return rows

    def search(self, q: str) -> list[dict]:
        q = q.strip()
        if not q:
            return self.list_conversations()
        hits: dict[str, dict] = {}
        for c in self._all("SELECT * FROM conversations WHERE source='web' AND title LIKE ?", (f"%{q}%",)):
            hits[c["id"]] = _conv(c)
        terms = " ".join('"' + t.replace('"', '""') + '"*' for t in q.split())
        try:
            rows = self._all(
                "SELECT conversation_id, snippet(messages_fts, 0, '[', ']', '…', 10) AS snip "
                "FROM messages_fts WHERE messages_fts MATCH ? LIMIT 50", (terms,))
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            c = hits.get(r["conversation_id"]) or self.get_conversation(r["conversation_id"])
            if c and c.get("source", "web") == "web":
                c.setdefault("snippet", r["snip"])
                hits[c["id"]] = c
        return sorted(hits.values(), key=lambda c: (-c["pinned"], -c["updated_at"]))

    # ---------------------------------------------------------------- mcp servers
    def list_mcp_servers(self) -> list[dict]:
        return [_srv(r) for r in self._all("SELECT * FROM mcp_servers ORDER BY created_at")]

    def get_mcp_server(self, sid: str) -> dict | None:
        rows = self._all("SELECT * FROM mcp_servers WHERE id=?", (sid,))
        return _srv(rows[0]) if rows else None

    def add_mcp_server(self, name: str, transport: str, command: str = "", args: list | None = None,
                       url: str = "", env_keys: list | None = None, disabled_tools: list | None = None) -> dict:
        sid = uuid.uuid4().hex[:8]
        self._q("INSERT INTO mcp_servers VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, name, transport, command, json.dumps(args or []), url, json.dumps(env_keys or []), 1,
                 json.dumps(disabled_tools or []), time.time()))
        return self.get_mcp_server(sid)

    def update_mcp_server(self, sid: str, enabled: bool | None = None, disabled_tools: list | None = None) -> dict | None:
        if enabled is not None:
            self._q("UPDATE mcp_servers SET enabled=? WHERE id=?", (1 if enabled else 0, sid))
        if disabled_tools is not None:
            self._q("UPDATE mcp_servers SET disabled_tools=? WHERE id=?", (json.dumps(disabled_tools), sid))
        return self.get_mcp_server(sid)

    def delete_mcp_server(self, sid: str) -> None:
        self._q("DELETE FROM mcp_servers WHERE id=?", (sid,))

    # ---------------------------------------------------------------- settings
    def settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for r in self._all("SELECT key, value FROM settings"):
            out[r["key"]] = json.loads(r["value"])
        return out

    def set_settings(self, **values) -> dict:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                self._q("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (k, json.dumps(v)))
        return self.settings()

    def usage(self) -> dict:
        u = self.settings()["usage"]
        today = time.strftime("%Y-%m-%d")
        return u if u.get("date") == today else {"date": today, "requests": 0, "tokens": 0}

    def bump_usage(self, tokens: int) -> None:
        u = self.usage()
        self.set_settings(usage={"date": u["date"], "requests": u["requests"] + 1, "tokens": u["tokens"] + int(tokens or 0)})

    # ---------------------------------------------------------------- export / wipe
    def export_all(self) -> dict:
        convs = self.list_conversations()
        for c in convs:
            c["messages"] = self.messages(c["id"])
        return {"exported_at": time.time(), "conversations": convs}

    def wipe(self) -> int:
        n = len(self._all("SELECT id FROM conversations"))
        self._q("DELETE FROM messages_fts")
        self._q("DELETE FROM messages")
        self._q("DELETE FROM conversations")
        return n


def _conv(r: dict) -> dict:
    r = dict(r)
    r["pinned"] = bool(r["pinned"])
    return r


def _srv(r: dict) -> dict:
    r = dict(r)
    for k in ("args", "env_keys", "disabled_tools"):
        r[k] = json.loads(r[k])
    r["enabled"] = bool(r["enabled"])
    return r
