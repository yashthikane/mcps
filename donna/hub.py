"""MCP hub: one long-lived client to Donna's built-in FastMCP server (in-memory) plus
one per user-added MCP server (stdio command or streamable HTTP URL)."""
import asyncio
import logging
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from mcp.types import TextContent

from . import vault

log = logging.getLogger("donna.hub")

# Built-in connections and the tools that belong to each (tool names from tools/*.py).
BUILTIN = [
    {"id": "gmail", "name": "Gmail", "icon": "mail", "source": "Google · built-in", "setup": "google",
     "tools": ["list_emails", "read_email", "search_emails", "get_unread_emails", "send_email", "reply_email",
               "forward_email", "mark_email", "delete_email", "create_draft", "list_drafts", "send_draft"]},
    {"id": "calendar", "name": "Google Calendar", "icon": "cal", "source": "Google · built-in", "setup": "google",
     "tools": ["get_events", "list_events", "create_event", "update_event", "delete_event"]},
    {"id": "notion", "name": "Notion", "icon": "doc", "source": "Notion · built-in", "setup": "notion",
     "tools": ["search_notion", "list_pages", "read_page_content", "create_page", "update_page_title",
               "append_text_to_page", "delete_page", "query_database"]},
    {"id": "weather", "name": "Weather", "icon": "sun", "source": "Open-Meteo · built-in", "setup": None,
     "tools": ["get_weather"]},
    {"id": "utils", "name": "Utilities", "icon": "smile", "source": "Built-in", "setup": None,
     "tools": ["square", "get_jokes"]},
    {"id": "schedule", "name": "Scheduled tasks", "icon": "clock", "source": "Scheduler · built-in", "setup": None,
     "tools": ["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "snooze_scheduled_task"]},
]
TOOL_GROUP = {t: c["id"] for c in BUILTIN for t in c["tools"]}

# Built-in tools that send, change or delete something always wait for the user's approval.
CONFIRM = {"send_email", "reply_email", "forward_email", "send_draft", "delete_email",
           "create_event", "update_event", "delete_event", "delete_page", "cancel_scheduled_task"}
# Built-in tools that change something but don't need approval in manual mode. Plan mode hides
# these and CONFIRM: it may only read.
WRITES = CONFIRM | {"mark_email", "create_draft", "create_page", "update_page_title", "append_text_to_page",
                    "schedule_task", "snooze_scheduled_task"}
# External tools whose names look like writes get the same treatment.
WRITE_WORDS = re.compile(r"(write|edit|delete|remove|move|rename|commit|push|drop|insert|update|create|send|post|put|exec|run)", re.I)


@dataclass
class Tool:
    name: str            # name exposed to the model (external: "<slug>__<tool>")
    remote: str          # name on the MCP server
    server: str          # "donna" or an MCP server id
    group: str           # connection id
    description: str
    schema: dict
    confirm: bool
    writes: bool = False  # changes something (hidden in plan mode); every confirm tool writes

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": (self.description or "")[:600], "parameters": self.schema}}


@dataclass
class External:
    config: dict
    client: Client | None = None
    tools: list[Tool] = field(default_factory=list)
    state: str = "stopped"   # connected | error | stopped | connecting
    error: str = ""
    since: float = 0.0


def slug(name: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "mcp")[:20]


def split_args(args) -> list[str]:
    if isinstance(args, list):
        return [str(a) for a in args]
    parts = shlex.split(args or "", posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def env_key(server_id: str, key: str) -> str:
    return f"mcp_env::{server_id}::{key}"


def text_of(result) -> str:
    return "\n".join(c.text for c in (result.content or []) if isinstance(c, TextContent)) or "(no text output)"


def resolve_command(command: str) -> str:
    """On Windows, `npx`/`uvx` must resolve to npx.cmd / uvx.exe. The bare name can match
    npm's extension-less shell script, which fails with "not a valid Win32 application"."""
    if os.name != "nt" or os.path.splitext(command)[1]:
        return command
    for candidate in (command + ".exe", command + ".cmd", command + ".bat", command):
        path = shutil.which(candidate)
        if path and path.lower().endswith((".exe", ".cmd", ".bat", ".com")):
            return path
    return command


def _transport(cfg: dict, env: dict | None = None):
    if cfg["transport"] == "http":
        return StreamableHttpTransport(cfg["url"])
    return StdioTransport(command=resolve_command(cfg["command"]), args=split_args(cfg.get("args")), env=env or None)


class MCPHub:
    def __init__(self, store, builtin_server):
        self.store = store
        self.builtin_server = builtin_server
        self.builtin: Client | None = None
        self.builtin_tools: list[Tool] = []
        self.external: dict[str, External] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.builtin = Client(self.builtin_server)
        await self.builtin.__aenter__()
        for t in await self.builtin.list_tools():
            self.builtin_tools.append(Tool(
                name=t.name, remote=t.name, server="donna", group=TOOL_GROUP.get(t.name, "utils"),
                description=t.description or "", schema=t.inputSchema or {"type": "object", "properties": {}},
                confirm=t.name in CONFIRM, writes=t.name in WRITES))
        for cfg in self.store.list_mcp_servers():
            self.external[cfg["id"]] = External(cfg)
            if cfg["enabled"]:
                await self.connect(cfg["id"])

    async def stop(self) -> None:
        for sid in list(self.external):
            await self._close(sid)
        if self.builtin:
            await self.builtin.__aexit__(None, None, None)

    # ------------------------------------------------------------------ external servers
    def _env(self, cfg: dict) -> dict:
        return {k: vault.get(env_key(cfg["id"], k)) or "" for k in cfg.get("env_keys", [])}

    async def connect(self, sid: str) -> External:
        ext = self.external[sid]
        await self._close(sid)
        ext.state, ext.error = "connecting", ""
        client = Client(_transport(ext.config, self._env(ext.config)), timeout=60, init_timeout=45)
        try:
            await asyncio.wait_for(client.__aenter__(), timeout=60)
            remote = await client.list_tools()
        except Exception as e:  # noqa: BLE001 - surface any startup failure in the UI
            ext.state, ext.error = "error", _short(e)
            log.warning("MCP server %s failed to start: %s", ext.config["name"], ext.error)
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            return ext
        prefix = slug(ext.config["name"])
        ext.client = client
        ext.tools = []
        for t in remote:
            confirm = bool(WRITE_WORDS.search(t.name)) or bool(getattr(t.annotations, "destructiveHint", False))
            ext.tools.append(Tool(name=f"{prefix}__{t.name}"[:64], remote=t.name, server=sid, group=sid,
                                  description=t.description or "", schema=t.inputSchema or {"type": "object", "properties": {}},
                                  confirm=confirm, writes=confirm))  # name-based: write-like ⇒ both
        ext.state, ext.since = "connected", time.time()
        return ext

    async def _close(self, sid: str) -> None:
        ext = self.external.get(sid)
        if ext and ext.client:
            try:
                await ext.client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if ext:
            ext.client, ext.tools, ext.state = None, [], "stopped"

    async def test(self, cfg: dict, env: dict) -> list[dict]:
        """Start a server once, list its tools and stop it. Raises with a readable message on failure."""
        client = Client(_transport(cfg, env), timeout=60, init_timeout=45)
        try:
            await asyncio.wait_for(client.__aenter__(), timeout=60)
            tools = await client.list_tools()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(_short(e)) from e
        finally:
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        return [{"name": t.name, "description": (t.description or "")[:200],
                 "confirm": bool(WRITE_WORDS.search(t.name))} for t in tools]

    async def add(self, cfg: dict) -> External:
        self.external[cfg["id"]] = External(cfg)
        return await self.connect(cfg["id"])

    async def remove(self, sid: str) -> None:
        await self._close(sid)
        self.external.pop(sid, None)

    async def set_enabled(self, sid: str, enabled: bool) -> None:
        ext = self.external[sid]
        ext.config["enabled"] = enabled
        if enabled:
            await self.connect(sid)
        else:
            await self._close(sid)

    # ------------------------------------------------------------------ catalog / calls
    def all_tools(self) -> list[Tool]:
        out = list(self.builtin_tools)
        for ext in self.external.values():
            out.extend(ext.tools)
        return out

    def enabled_tools(self, settings: dict) -> list[Tool]:
        off_conn = set(settings.get("disabled_connections", []))
        off_tools = set(settings.get("disabled_tools", []))
        out = []
        for t in self.builtin_tools:
            if t.group not in off_conn and t.name not in off_tools:
                out.append(t)
        for ext in self.external.values():
            if ext.state == "connected" and ext.config.get("enabled", True):
                off = set(ext.config.get("disabled_tools", []))
                out.extend(t for t in ext.tools if t.remote not in off)
        return out

    def find(self, name: str, settings: dict) -> Tool | None:
        return next((t for t in self.enabled_tools(settings) if t.name == name), None)

    async def call(self, tool: Tool, args: dict, timeout: float = 90) -> tuple[bool, str]:
        client = self.builtin if tool.server == "donna" else (self.external.get(tool.server) or External({})).client
        if client is None:
            return False, "This MCP server isn't running. Restart it in Connections."
        try:
            res = await client.call_tool(tool.remote, args, timeout=timeout, raise_on_error=False)
        except Exception as e:  # noqa: BLE001
            return False, f"Tool failed: {_short(e)}"
        text = text_of(res)
        ok = not res.is_error and not text.startswith("Error")
        return ok, text


def _short(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    if isinstance(e, (FileNotFoundError,)) or "No such file" in msg or "cannot find the file" in msg.lower():
        msg = "Command not found. Check the command and that it's installed (for example Node.js for npx, or uv for uvx)."
    elif isinstance(e, asyncio.TimeoutError):
        msg = "The server didn't respond within 60 seconds."
    return msg.splitlines()[0][:300]
