"""FastAPI app: REST + Server-Sent Events API under /api/v1, and the built React UI at /."""
import asyncio
import hmac
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import DATA_DIR, ROOT, approvals, audit, availability, contact_requests, contacts, identity, inbox, llm, memory, permissions, pg, runtime_tools, tasks, vault
from .agent import MODES, RUNS, Run, run_chat
from .channels.base import ChannelError, WebhookRejected
from .channels.manager import ChannelManager
from .hub import BUILTIN, CONFIRM, MCPHub, env_key, split_args
from .notify import Notifier, OwnerChat
from .scheduler_client import SchedulerClient, ScheduleInvalid, SchedulerUnavailable
from .store import Store

log = logging.getLogger("donna")
WEB_DIST = ROOT / "web" / "dist"
# Browsers send Origin on cross-site requests; only pages served from this machine may call the API.
# On the self-hosted server, DONNA_ALLOWED_HOSTS adds the private Tailscale name (e.g. donna.tailXXXX.ts.net)
# that `tailscale serve` proxies to 127.0.0.1.
ALLOWED_HOSTS = {"127.0.0.1", "localhost",
                 *(h.strip().lower() for h in os.getenv("DONNA_ALLOWED_HOSTS", "").split(",") if h.strip())}


def _setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA_DIR / "donna.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("donna")
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    load_dotenv(ROOT / ".env")
    imported = vault.import_from_env()
    if imported:
        log.info("Imported %s from .env into Credential Manager", imported)
    import server  # noqa: F401 - registers every built-in tool on mcp_instance.mcp
    from mcp_instance import mcp

    app.state.store = Store(app.state.db_path) if getattr(app.state, "db_path", None) else Store()
    app.state.hub = MCPHub(app.state.store, mcp)
    await app.state.hub.start()

    # Scheduling (PostgreSQL + the Node scheduler). Donna works without it; only /scheduled-tasks fails.
    app.state.internal_token = vault.internal_token()
    app.state.pg = getattr(app.state, "pg_override", None) or pg.from_vault()
    if await app.state.pg.open():
        try:
            await app.state.pg.migrate()
        except Exception as e:  # noqa: BLE001
            log.warning("PostgreSQL migrations failed: %s", e)
    app.state.notifier = Notifier(app.state.pg)
    app.state.tasks = tasks.TaskService(app.state.pg, getattr(app.state, "scheduler_override", None) or SchedulerClient(),
                                        app.state.notifier, app.state.store, app.state.hub)
    tasks.service = app.state.tasks

    # Messaging channels (WhatsApp, Telegram) share the same store, hub and agent runtime.
    app.state.channels = ChannelManager(app.state.store, app.state.hub, app.state.notifier,
                                        getattr(app.state, "channel_adapter_kwargs", None))
    app.state.notifier.channels["owner_chat"] = OwnerChat(app.state.channels)
    app.state.tasks.channels = app.state.channels
    runtime_tools.services.store, runtime_tools.services.channels = app.state.store, app.state.channels
    await app.state.channels.start()
    log.info("Donna started with %d tools", len(app.state.hub.all_tools()))
    yield
    await app.state.channels.stop()
    runtime_tools.services.store = runtime_tools.services.channels = None
    tasks.service = None
    await app.state.hub.stop()
    await app.state.pg.close()


app = FastAPI(title="Donna", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    return await call_next(request)


def S(request: Request) -> Store:
    return request.app.state.store


def H(request: Request) -> MCPHub:
    return request.app.state.hub


# =============================================================================== health
_health_cache: dict = {"t": 0.0, "value": None}


@app.get("/api/v1/health")
async def health(request: Request):
    if time.time() - _health_cache["t"] < 20 and _health_cache["value"]:
        return _health_cache["value"]
    key = vault.get(vault.GROQ_KEY)
    provider = llm.provider(S(request).settings())
    if provider == "claude_code" and not llm.FAKE:
        # The CLI signs itself in; whether that works shows on the first message ("isn't signed in").
        state = "ok" if llm.claude_cli() else "no_key"
        value = {"groq": state, "provider": provider, "online": True, "usage": S(request).usage()}
        _health_cache.update(t=time.time(), value=value)
        return value
    if llm.FAKE:
        groq_state = "ok" if key or os.getenv("DONNA_FAKE_ONLINE", "1") == "1" else "no_key"
    elif not key:
        groq_state = "no_key"
    else:
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"})
            groq_state = "ok" if r.status_code == 200 else "invalid_key" if r.status_code in (401, 403) else "error"
        except httpx.HTTPError:
            groq_state = "unreachable"
    value = {"groq": groq_state, "provider": provider, "online": groq_state != "unreachable", "usage": S(request).usage()}
    _health_cache.update(t=time.time(), value=value)
    return value


# =============================================================================== conversations
class ConvPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None


@app.get("/api/v1/conversations")
def list_conversations(request: Request, q: str = ""):
    return S(request).search(q) if q else S(request).list_conversations()


@app.post("/api/v1/conversations")
def create_conversation(request: Request):
    return S(request).create_conversation()


@app.patch("/api/v1/conversations/{cid}")
def patch_conversation(cid: str, body: ConvPatch, request: Request):
    conv = S(request).update_conversation(cid, body.title, body.pinned)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@app.delete("/api/v1/conversations/{cid}")
def delete_conversation(cid: str, request: Request):
    for run in list(RUNS.values()):
        if run.conversation_id == cid:
            run.cancel()
    S(request).delete_conversation(cid)
    return {"ok": True}


@app.get("/api/v1/conversations/{cid}/messages")
def conversation_messages(cid: str, request: Request):
    if not S(request).get_conversation(cid):
        raise HTTPException(404, "Conversation not found")
    return S(request).messages(cid)


# =============================================================================== chat (SSE)
class ChatIn(BaseModel):
    text: str
    connections: list[str] = []
    mode: str | None = None  # plan | manual | auto; defaults to the saved setting


@app.post("/api/v1/conversations/{cid}/messages")
async def send_message(cid: str, body: ChatIn, request: Request):
    store, hub = S(request), H(request)
    if not store.get_conversation(cid):
        raise HTTPException(404, "Conversation not found")
    text = body.text.strip()[:16000]
    if not text:
        raise HTTPException(400, "Message is empty")
    if any(r.conversation_id == cid for r in RUNS.values()):
        raise HTTPException(409, "Donna is still answering in this conversation.")
    mode = body.mode or store.settings().get("permission_mode", "manual")
    if mode not in MODES:
        raise HTTPException(400, "mode must be plan, manual or auto")
    run = Run(cid, mode=mode)
    RUNS[run.id] = run

    async def events():
        try:
            async for ev in run_chat(store, hub, run, text, set(body.connections)):
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                if await request.is_disconnected():
                    run.cancel()
        finally:
            RUNS.pop(run.id, None)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Decision(BaseModel):
    call_id: str
    approved: bool


@app.post("/api/v1/runs/{run_id}/confirm")
def confirm(run_id: str, body: Decision):
    run = RUNS.get(run_id)
    if not run or not run.decide(body.call_id, body.approved):
        raise HTTPException(404, "That action is no longer waiting for approval.")
    return {"ok": True}


@app.post("/api/v1/runs/{run_id}/cancel")
def cancel(run_id: str):
    run = RUNS.get(run_id)
    if run:
        run.cancel()
    return {"ok": True}


# =============================================================================== connections
_notion_cache: dict = {"t": 0.0, "token": None, "value": None}


async def _notion_state() -> dict:
    token = vault.get(vault.NOTION_TOKEN)
    if not token:
        return {"state": "not_set_up"}
    if _notion_cache["token"] == token and time.time() - _notion_cache["t"] < 120:
        return _notion_cache["value"]
    if llm.FAKE:
        value = {"state": "connected", "detail": "Test workspace"}
    else:
        from notion_client import APIResponseError, AsyncClient
        try:
            me = await AsyncClient(auth=token).users.me()
            name = (me.get("bot") or {}).get("workspace_name") or me.get("name") or "Notion"
            value = {"state": "connected", "detail": f"Workspace: {name}"}
        except APIResponseError as e:
            value = {"state": "error", "detail": "Notion rejected the secret. Paste a new one."} if e.code == "unauthorized" \
                else {"state": "connected", "detail": str(e)[:120]}
        except Exception as e:  # noqa: BLE001 - offline: assume the saved token still works
            value = {"state": "connected", "detail": f"Couldn't reach Notion ({type(e).__name__})"}
    _notion_cache.update(t=time.time(), token=token, value=value)
    return value


async def _google_state() -> dict:
    from tools import google_auth
    if llm.FAKE and os.getenv("DONNA_FAKE_GOOGLE"):
        return {"state": os.getenv("DONNA_FAKE_GOOGLE"), "email": "test@example.com"}
    return await asyncio.to_thread(google_auth.status)


@app.get("/api/v1/connections")
async def connections(request: Request):
    store, hub = S(request), H(request)
    settings = store.settings()
    off_conn, off_tools = set(settings["disabled_connections"]), set(settings["disabled_tools"])
    google, notion = await _google_state(), await _notion_state()
    tools_by_group: dict[str, list] = {}
    for t in hub.builtin_tools:
        tools_by_group.setdefault(t.group, []).append(
            {"name": t.name, "description": t.description.strip().split("\n")[0][:160], "confirm": t.confirm,
             "enabled": t.name not in off_tools})
    out = []
    for c in BUILTIN:
        auth = google if c["setup"] == "google" else notion if c["setup"] == "notion" else {"state": "connected"}
        state = "off" if c["id"] in off_conn else auth["state"]
        detail = {"gmail": f"Signed in as {auth.get('email')}" if auth.get("email") else "",
                  "calendar": "Primary calendar" if auth["state"] == "connected" else "",
                  "weather": "No setup needed. Current weather for any city from Open-Meteo.",
                  "utils": "square and get_jokes. Handy for checking that Donna works.",
                  "schedule": "Reminders and AI tasks from chat. Manage them on the Tasks page."}.get(c["id"], auth.get("detail", ""))
        out.append({"id": c["id"], "kind": "builtin", "name": c["name"], "icon": c["icon"], "source": c["source"],
                    "setup": c["setup"], "state": state, "auth_state": auth["state"], "detail": detail or "",
                    "tools": tools_by_group.get(c["id"], [])})
    for sid, ext in hub.external.items():
        cfg = ext.config
        src = f"HTTP · {cfg['url']}" if cfg["transport"] == "http" else f"stdio · {cfg['command']} {' '.join(split_args(cfg['args']))}"
        off = set(cfg.get("disabled_tools", []))
        out.append({"id": sid, "kind": "mcp", "name": cfg["name"], "icon": "terminal", "source": src.strip(),
                    "setup": "mcp", "state": "off" if not cfg.get("enabled", True) else ext.state,
                    "detail": ext.error, "transport": cfg["transport"],
                    "tools": [{"name": t.remote, "description": t.description.strip().split("\n")[0][:160],
                               "confirm": t.confirm, "enabled": t.remote not in off} for t in ext.tools]})
    return out


class ConnPatch(BaseModel):
    enabled: bool | None = None
    disabled_tools: list[str] | None = None


@app.patch("/api/v1/connections/{cid}")
async def patch_connection(cid: str, body: ConnPatch, request: Request):
    store, hub = S(request), H(request)
    if cid in hub.external:
        if body.disabled_tools is not None:
            cfg = store.update_mcp_server(cid, disabled_tools=body.disabled_tools)
            hub.external[cid].config["disabled_tools"] = cfg["disabled_tools"]
        if body.enabled is not None:
            store.update_mcp_server(cid, enabled=body.enabled)
            await hub.set_enabled(cid, body.enabled)
        return {"ok": True}
    if cid not in {c["id"] for c in BUILTIN}:
        raise HTTPException(404, "Unknown connection")
    s = store.settings()
    if body.enabled is not None:
        off = set(s["disabled_connections"])
        off.discard(cid) if body.enabled else off.add(cid)
        store.set_settings(disabled_connections=sorted(off))
    if body.disabled_tools is not None:
        group_tools = next(c["tools"] for c in BUILTIN if c["id"] == cid)
        keep = [t for t in s["disabled_tools"] if t not in group_tools]
        store.set_settings(disabled_tools=sorted(set(keep) | (set(body.disabled_tools) & set(group_tools))))
    return {"ok": True}


# ----------------------------------------------------------------------------- Google
@app.post("/api/v1/connections/google/credentials")
async def google_credentials(file: UploadFile):
    from tools import google_auth
    raw = await file.read()
    try:
        info = google_auth.save_client(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **info, "filename": file.filename}


@app.post("/api/v1/connections/google/authorize")
async def google_authorize():
    from tools import google_auth
    try:
        info = await asyncio.to_thread(google_auth.authorize, 300)
    except google_auth.GoogleAuthError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001 - timeouts, user closed the tab, network
        raise HTTPException(400, f"Google sign-in didn't finish: {str(e)[:200] or type(e).__name__}. Try again.") from e
    return {"ok": True, **info}


@app.post("/api/v1/connections/google/test")
async def google_test():
    from tools import google_auth

    def work():
        cal = google_auth.service("calendar", "v3")
        from datetime import datetime
        events = cal.events().list(calendarId="primary", timeMin=datetime.now().astimezone().isoformat(), maxResults=3,
                                   singleEvents=True, orderBy="startTime").execute().get("items", [])
        gmail = google_auth.service("gmail", "v1")
        ids = gmail.users().messages().list(userId="me", maxResults=3, q="in:inbox").execute().get("messages", [])
        subjects = []
        for m in ids:
            meta = gmail.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject", "From"]).execute()
            h = {x["name"]: x["value"] for x in meta["payload"]["headers"]}
            subjects.append(f"{h.get('From', '').split('<')[0].strip()} · {h.get('Subject', '(no subject)')}")
        return {"events": [f"{e['start'].get('dateTime', e['start'].get('date'))[:16].replace('T', ' ')} · {e.get('summary', '(no title)')}"
                           for e in events], "emails": subjects}
    try:
        return await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)[:300]) from e


@app.delete("/api/v1/connections/google")
def google_disconnect(forget_client: bool = False):
    from tools import google_auth
    google_auth.disconnect(forget_client)
    return {"ok": True}


# ----------------------------------------------------------------------------- Notion
class NotionIn(BaseModel):
    token: str


@app.post("/api/v1/connections/notion")
async def notion_connect(body: NotionIn):
    token = body.token.strip()
    if len(token) < 20:
        raise HTTPException(400, "That secret looks too short. Copy the full Internal Integration Secret (it starts with ntn_ or secret_).")
    if llm.FAKE:
        if not token.startswith(("ntn_", "secret_")):
            raise HTTPException(400, "Notion rejected the secret. Copy it again from your integration's Configuration tab.")
        pages = ["Daily Notes", "Project Donna", "Reading list"]
    else:
        from notion_client import APIResponseError, AsyncClient
        client = AsyncClient(auth=token)
        try:
            await client.users.me()
            res = await client.search(page_size=10)
        except APIResponseError as e:
            raise HTTPException(400, "Notion rejected the secret. Copy it again from your integration's Configuration tab."
                                if e.code == "unauthorized" else f"Notion error: {e}") from e
        except httpx.HTTPError as e:
            raise HTTPException(400, "Can't reach Notion. Check your internet connection.") from e
        from tools.notion_tools import _title
        pages = [_title(p) for p in res.get("results", [])]
    vault.set(vault.NOTION_TOKEN, token)
    _notion_cache.update(t=0.0)
    return {"ok": True, "pages": pages}


@app.delete("/api/v1/connections/notion")
def notion_disconnect():
    vault.delete(vault.NOTION_TOKEN)
    _notion_cache.update(t=0.0)
    return {"ok": True}


# ----------------------------------------------------------------------------- MCP servers
class ServerIn(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: str | list[str] = ""
    url: str = ""
    env: dict[str, str] = {}
    disabled_tools: list[str] = []


def _cfg(body: ServerIn) -> dict:
    if body.transport not in ("stdio", "http"):
        raise HTTPException(400, "Transport must be stdio or http.")
    if body.transport == "stdio" and not body.command.strip():
        raise HTTPException(400, "Enter the command that starts the server, for example npx or uvx.")
    if body.transport == "http" and not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Enter the server URL, for example http://127.0.0.1:3001/mcp.")
    return {"name": body.name.strip() or "MCP server", "transport": body.transport, "command": body.command.strip(),
            "args": split_args(body.args), "url": body.url.strip()}


@app.post("/api/v1/mcp-servers/test")
async def test_server(body: ServerIn, request: Request):
    try:
        tools = await H(request).test(_cfg(body), {k: v for k, v in body.env.items() if k})
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "tools": tools}


@app.post("/api/v1/mcp-servers")
async def add_server(body: ServerIn, request: Request):
    store, hub = S(request), H(request)
    cfg = _cfg(body)
    env = {k: v for k, v in body.env.items() if k}
    saved = store.add_mcp_server(cfg["name"], cfg["transport"], cfg["command"], cfg["args"], cfg["url"],
                                 list(env), body.disabled_tools)
    for k, v in env.items():
        vault.set(env_key(saved["id"], k), v)
    ext = await hub.add(saved)
    return {"id": saved["id"], "state": ext.state, "error": ext.error}


@app.delete("/api/v1/mcp-servers/{sid}")
async def remove_server(sid: str, request: Request):
    store, hub = S(request), H(request)
    cfg = store.get_mcp_server(sid)
    if not cfg:
        raise HTTPException(404, "Unknown server")
    for k in cfg["env_keys"]:
        vault.delete(env_key(sid, k))
    await hub.remove(sid)
    store.delete_mcp_server(sid)
    return {"ok": True}


@app.post("/api/v1/mcp-servers/{sid}/restart")
async def restart_server(sid: str, request: Request):
    hub = H(request)
    if sid not in hub.external:
        raise HTTPException(404, "Unknown server")
    ext = await hub.connect(sid)
    return {"state": ext.state, "error": ext.error, "tools": len(ext.tools)}


# =============================================================================== scheduled tasks
def T(request: Request) -> tasks.TaskService:
    return request.app.state.tasks


async def _tasks_call(coro):
    """Map task-service errors to HTTP: bad input 400, unknown task 404, a service down 503."""
    try:
        return await coro
    except (tasks.TaskError, ScheduleInvalid) as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e) or "Task not found.") from e
    except SchedulerUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except pg.Unavailable as e:
        raise HTTPException(503, f"Scheduling isn't available: {e}") from e
    except psycopg.OperationalError as e:
        raise HTTPException(503, "Scheduling isn't available: PostgreSQL isn't reachable.") from e


class TaskIn(BaseModel):
    title: str
    type: str = "reminder"
    description: str | None = None
    message: str = ""
    prompt: str = ""
    run_at: str | None = None
    timezone: str | None = None
    recurrence_type: str = "once"
    recurrence_rule: str | None = None
    notification_channels: list[str] | None = None
    permission_mode: str | None = None
    deliver_contact_id: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    message: str | None = None
    prompt: str | None = None
    run_at: str | None = None
    timezone: str | None = None
    recurrence_type: str | None = None
    recurrence_rule: str | None = None
    notification_channels: list[str] | None = None
    permission_mode: str | None = None
    deliver_contact_id: str | None = None


class PreviewIn(BaseModel):
    run_at: str | None = None
    timezone: str | None = None
    recurrence_type: str = "once"
    recurrence_rule: str | None = None


class SnoozeIn(BaseModel):
    minutes: int = 15


@app.get("/api/v1/scheduler/health")
async def scheduler_health(request: Request):
    h = await T(request).scheduler.health()
    pg_ok = await request.app.state.pg.available()
    out = {"scheduler": "down" if h.get("status") == "down" else "ok", "redis": h.get("redis", "unknown"),
           "worker": h.get("worker", "stopped"), "postgres": "ok" if pg_ok else "down"}
    out["ok"] = out["scheduler"] == "ok" and out["redis"] == "ok" and out["worker"] == "running" and pg_ok
    if not pg_ok:
        out["detail"] = request.app.state.pg.error or "PostgreSQL isn't reachable."
    elif out["scheduler"] != "ok":
        out["detail"] = "The scheduler service isn't running. Start Donna with scripts\\start.ps1."
    elif out["redis"] != "ok":
        out["detail"] = "Redis (WSL Ubuntu) isn't running. Start Donna with scripts\\start.ps1."
    return out


@app.get("/api/v1/scheduled-tasks")
async def list_tasks(request: Request, status: str | None = None):
    return await _tasks_call(T(request).list(status))


@app.post("/api/v1/scheduled-tasks")
async def create_task(body: TaskIn, request: Request):
    return await _tasks_call(T(request).create(body.model_dump(exclude_none=True)))


@app.post("/api/v1/scheduled-tasks/preview")
async def preview_task(body: PreviewIn, request: Request):
    return {"runs": await _tasks_call(T(request).preview(body.model_dump(exclude_none=True)))}


@app.get("/api/v1/scheduled-tasks/{tid}")
async def get_task(tid: str, request: Request):
    return await _tasks_call(T(request).get(tid))


@app.patch("/api/v1/scheduled-tasks/{tid}")
async def patch_task(tid: str, body: TaskPatch, request: Request):
    return await _tasks_call(T(request).update(tid, body.model_dump(exclude_unset=True)))


@app.delete("/api/v1/scheduled-tasks/{tid}")
async def delete_task(tid: str, request: Request):
    await _tasks_call(T(request).delete(tid))
    return {"ok": True}


@app.post("/api/v1/scheduled-tasks/{tid}/cancel")
async def cancel_task(tid: str, request: Request):
    return await _tasks_call(T(request).cancel(tid))


@app.post("/api/v1/scheduled-tasks/{tid}/pause")
async def pause_task(tid: str, request: Request):
    return await _tasks_call(T(request).pause(tid))


@app.post("/api/v1/scheduled-tasks/{tid}/resume")
async def resume_task(tid: str, request: Request):
    return await _tasks_call(T(request).resume(tid))


@app.post("/api/v1/scheduled-tasks/{tid}/snooze")
async def snooze_task(tid: str, body: SnoozeIn, request: Request):
    return await _tasks_call(T(request).snooze(tid, body.minutes))


@app.post("/api/v1/scheduled-tasks/{tid}/run")
async def run_task(tid: str, request: Request):
    return await _tasks_call(T(request).run_now(tid))


# ----------------------------------------------------------------------------- notifications
class ReadIn(BaseModel):
    ids: list[str] | None = None


@app.get("/api/v1/notifications")
async def notifications(request: Request):
    if not await request.app.state.pg.available():
        return []  # polled by the UI; stay quiet while scheduling is down
    return await _tasks_call(request.app.state.notifier.unread())


@app.post("/api/v1/notifications/read")
async def notifications_read(body: ReadIn, request: Request):
    return {"updated": await _tasks_call(request.app.state.notifier.mark_read(body.ids))}


# ----------------------------------------------------------------------------- internal (scheduler → Donna)
def require_internal(request: Request) -> None:
    """Only the local scheduler service holds this token (it comes from the vault)."""
    got = request.headers.get("authorization", "").encode()
    if not hmac.compare_digest(got, f"Bearer {request.app.state.internal_token}".encode()):
        raise HTTPException(401, "Unauthorized.")


class ExecuteIn(BaseModel):
    execution_id: str
    scheduled_for: datetime
    mode: str = "run"


@app.post("/api/v1/internal/scheduled-tasks/{tid}/execute", dependencies=[Depends(require_internal)], include_in_schema=False)
async def execute_task(tid: str, body: ExecuteIn, request: Request):
    if body.mode not in ("run", "missed"):
        raise HTTPException(400, "mode must be run or missed")
    return await _tasks_call(T(request).execute(tid, body.execution_id, body.scheduled_for, body.mode))


# =============================================================================== messaging channels
def CH(request: Request) -> ChannelManager:
    return request.app.state.channels


async def _call(fn, *args):
    """Run fn(*args) (sync or async), mapping channel/contact/approval errors to HTTP."""
    try:
        result = fn(*args)
        return (await result) if asyncio.iscoroutine(result) else result
    except ChannelError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 400, str(e)) from e
    except approvals.ApprovalError as e:
        raise HTTPException(409, str(e)) from e
    except (contacts.ContactError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from e


@app.get("/api/v1/channels")
def list_channels(request: Request):
    return CH(request).list()


@app.get("/api/v1/channels/{cid}/status")
async def channel_status(cid: str, request: Request):
    if not CH(request).row(cid):
        return next((c for c in CH(request).list() if c["id"] == cid), None) or {}
    return await CH(request).refresh(cid)


@app.post("/api/v1/channels/whatsapp/setup")
async def whatsapp_setup(request: Request):  # async: the job is a task on the server's event loop
    """The WhatsApp Connect button: install OpenWA if needed, start it, open the session (→ QR). Poll the GET."""
    audit.record(S(request), "owner", "channel.setup", "whatsapp")
    return CH(request).whatsapp_setup.begin()


@app.get("/api/v1/channels/whatsapp/setup")
def whatsapp_setup_state(request: Request):
    return CH(request).whatsapp_setup.snapshot()


@app.post("/api/v1/channels/{ctype}/connect")
async def channel_connect(ctype: str, request: Request):
    return await _call(CH(request).connect, ctype)


@app.post("/api/v1/channels/{cid}/disconnect")
async def channel_disconnect(cid: str, request: Request):
    return await _call(CH(request).disconnect, cid)


@app.post("/api/v1/channels/{cid}/pause")
def channel_pause(cid: str, request: Request):
    try:
        return CH(request).pause(cid, True)
    except ChannelError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/v1/channels/{cid}/resume")
def channel_resume(cid: str, request: Request):
    try:
        return CH(request).pause(cid, False)
    except ChannelError as e:
        raise HTTPException(e.status, str(e)) from e


@app.get("/api/v1/channels/{cid}/qr")
async def channel_qr(cid: str, request: Request):
    return await _call(CH(request).login_code, cid)


class TokenIn(BaseModel):
    token: str


@app.put("/api/v1/channels/telegram/token")
async def telegram_token(body: TokenIn, request: Request):
    import re
    token = body.token.strip()
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
        raise HTTPException(400, "That doesn't look like a bot token. Copy it from @BotFather, e.g. 123456789:AAH…")
    previous = vault.get(vault.TELEGRAM_TOKEN)
    vault.set(vault.TELEGRAM_TOKEN, token)
    try:
        return await _call(CH(request).connect, "telegram")
    except HTTPException:
        if previous:
            vault.set(vault.TELEGRAM_TOKEN, previous)
        else:
            vault.delete(vault.TELEGRAM_TOKEN)
        raise


@app.delete("/api/v1/channels/{cid}")
async def channel_forget(cid: str, request: Request):
    """Disconnect and forget the channel's credentials (Telegram token, webhook secret)."""
    out = await _call(CH(request).disconnect, cid)
    if cid == "telegram":
        vault.delete(vault.TELEGRAM_TOKEN)
    elif cid == "whatsapp":
        vault.delete(vault.OPENWA_SECRET)
    audit.record(S(request), "owner", "channel.forget", cid)
    return out


# ----------------------------------------------------------------------------- webhooks
WEBHOOK_MAX_BYTES = 2 * 1024 * 1024


@app.post("/api/v1/webhooks/whatsapp", include_in_schema=False)
async def whatsapp_webhook(request: Request):
    """OpenWA → Donna. Authenticated by HMAC signature, not by the Origin check (it has no Origin)."""
    if int(request.headers.get("content-length") or 0) > WEBHOOK_MAX_BYTES:
        raise HTTPException(413, "Webhook body too large.")
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        return await CH(request).handle_webhook("whatsapp", raw, headers)
    except WebhookRejected as e:
        audit.record(S(request), "channel:whatsapp", "webhook.rejected", outcome="denied", reason=str(e))
        raise HTTPException(e.status, str(e)) from e


# ----------------------------------------------------------------------------- contacts
class ContactIn(BaseModel):
    display_name: str
    mode: str = "disabled"
    channel_id: str | None = None  # with address: the first identity
    address: str | None = None     # phone number / chat ID on that channel
    language: str | None = None
    tone: str | None = None
    timezone: str | None = None
    working_hours: dict | None = None
    notes: str | None = None
    share_memory: bool | None = None
    is_owner: bool = False
    relationship: str | None = None
    relationship_label: str | None = None
    address_as: str | None = None
    script: str | None = None
    formality: str | None = None
    greeting: str | None = None
    intro_message: str | None = None
    background: str | None = None
    special_instructions: str | None = None
    allowed_requests: list[str] | None = None


class ContactPatch(BaseModel):
    display_name: str | None = None
    mode: str | None = None
    paused: bool | None = None
    language: str | None = None
    tone: str | None = None
    timezone: str | None = None
    working_hours: dict | None = None
    notes: str | None = None
    share_memory: bool | None = None
    relationship: str | None = None
    relationship_label: str | None = None
    address_as: str | None = None
    script: str | None = None
    formality: str | None = None
    greeting: str | None = None
    intro_message: str | None = None
    background: str | None = None
    special_instructions: str | None = None
    allowed_requests: list[str] | None = None


class IdentityIn(BaseModel):
    channel_id: str
    address: str


class PolicyIn(BaseModel):
    channel_id: str = ""
    capabilities: dict[str, str]


class MemoryIn(BaseModel):
    content: str


def _add_identity(request: Request, contact_id: str, channel_id: str, address: str) -> dict:
    from .channels.registry import REGISTRY
    if channel_id not in REGISTRY:
        raise HTTPException(400, f"Unknown channel: {channel_id}")
    try:
        external_id, phone = CH(request).adapter(channel_id).identity_for(address)
        return contacts.add_identity(S(request), contact_id, channel_id, external_id, phone, verified=True)
    except (ChannelError, contacts.ContactError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/v1/capabilities")
def capabilities(request: Request):
    """The permission catalogue the Contacts page shows."""
    out = [{"id": cap, "label": spec["label"], "help": spec["help"],
            "forced_approval": permissions.forced_approval(cap, CONFIRM)} for cap, spec in permissions.CAPABILITIES.items()]
    for sid, ext in H(request).external.items():
        out.append({"id": f"{permissions.EXTERNAL_PREFIX}{sid}", "label": ext.config["name"],
                    "help": "Every tool of this MCP server. Tools that change something always need your approval.",
                    "forced_approval": False})
    return out


@app.get("/api/v1/contacts")
def list_contacts(request: Request, q: str = "", discovered: bool | None = None):
    return contacts.list_all(S(request), q, discovered)


@app.post("/api/v1/contacts")
def create_contact(body: ContactIn, request: Request):
    store = S(request)
    fields = body.model_dump(exclude_none=True, exclude={"display_name", "mode", "channel_id", "address", "is_owner"})
    try:
        c = contacts.create(store, body.display_name, mode=body.mode, **fields)
    except contacts.ContactError as e:
        raise HTTPException(400, str(e)) from e
    if body.channel_id and body.address:
        try:
            _add_identity(request, c["id"], body.channel_id, body.address)
        except HTTPException:
            contacts.delete(store, c["id"])
            raise
    if body.is_owner:
        contacts.set_owner(store, c["id"])
    audit.record(store, "owner", "contact.create", c["id"], mode=body.mode)
    return contacts.get(store, c["id"])


@app.get("/api/v1/contacts/{contact_id}")
def get_contact(contact_id: str, request: Request):
    c = contacts.get(S(request), contact_id)
    if not c:
        raise HTTPException(404, "Contact not found.")
    c["policies"] = contacts.policies(S(request), contact_id)
    # What Donna says first when intro_message is empty (shown as the field's placeholder).
    c["default_intro"] = identity.introduction({**c, "intro_message": ""}, S(request).settings(), S(request))
    return c


@app.patch("/api/v1/contacts/{contact_id}")
async def patch_contact(contact_id: str, body: ContactPatch, request: Request):
    return await _call(contacts.update, S(request), contact_id, body.model_dump(exclude_none=True))


@app.delete("/api/v1/contacts/{contact_id}")
def delete_contact(contact_id: str, request: Request):
    if not contacts.get(S(request), contact_id):
        raise HTTPException(404, "Contact not found.")
    contacts.delete(S(request), contact_id)
    return {"ok": True}


@app.post("/api/v1/contacts/{contact_id}/owner")
async def make_owner(contact_id: str, request: Request):
    return await _call(contacts.set_owner, S(request), contact_id)


@app.get("/api/v1/contacts/{contact_id}/policies")
def get_policies(contact_id: str, request: Request):
    if not contacts.get(S(request), contact_id):
        raise HTTPException(404, "Contact not found.")
    return contacts.policies(S(request), contact_id)


@app.put("/api/v1/contacts/{contact_id}/policies")
async def put_policies(contact_id: str, body: PolicyIn, request: Request):
    return await _call(contacts.set_policy, S(request), contact_id, body.channel_id, body.capabilities)


@app.delete("/api/v1/contacts/{contact_id}/policies/{channel_id}")
def delete_policy(contact_id: str, channel_id: str, request: Request):
    contacts.clear_policy(S(request), contact_id, channel_id)
    return contacts.policies(S(request), contact_id)


@app.post("/api/v1/contacts/{contact_id}/identities")
def add_identity(contact_id: str, body: IdentityIn, request: Request):
    if not contacts.get(S(request), contact_id):
        raise HTTPException(404, "Contact not found.")
    _add_identity(request, contact_id, body.channel_id, body.address)
    return contacts.get(S(request), contact_id)


@app.delete("/api/v1/contacts/{contact_id}/identities/{identity_id}")
def remove_identity(contact_id: str, identity_id: str, request: Request):
    contacts.remove_identity(S(request), contact_id, identity_id)
    return contacts.get(S(request), contact_id)


@app.get("/api/v1/contacts/{contact_id}/memories")
def contact_memories(contact_id: str, request: Request):
    return memory.list_contact(S(request), contact_id)


@app.post("/api/v1/contacts/{contact_id}/memories")
async def add_contact_memory(contact_id: str, body: MemoryIn, request: Request):
    if not contacts.get(S(request), contact_id):
        raise HTTPException(404, "Contact not found.")
    return await _call(memory.add, S(request), body.content, "contact", contact_id)


@app.delete("/api/v1/contacts/{contact_id}/memories/{memory_id}")
def delete_contact_memory(contact_id: str, memory_id: str, request: Request):
    if not memory.delete(S(request), memory_id, contact_id):
        raise HTTPException(404, "Memory not found.")
    return {"ok": True}


# ----------------------------------------------------------------------------- inbox
class SendIn(BaseModel):
    text: str


class ArchiveIn(BaseModel):
    archived: bool = True


@app.get("/api/v1/inbox")
def list_inbox(request: Request, channel_id: str = "", contact_id: str = "", status: str = "active", q: str = ""):
    return inbox.conversations(S(request), channel_id, contact_id, status, q)


@app.get("/api/v1/inbox/counts")
def inbox_counts(request: Request):
    return {**inbox.counts(S(request)), "approvals": approvals.pending_count(S(request)),
            "requests": contact_requests.counts(S(request))}


def _inbox_conv(request: Request, ccid: str) -> dict:
    conv = inbox.conversation(S(request), ccid)
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    return conv


@app.get("/api/v1/inbox/{ccid}")
def get_inbox_conversation(ccid: str, request: Request):
    return _inbox_conv(request, ccid)


@app.get("/api/v1/inbox/{ccid}/messages")
def inbox_messages(ccid: str, request: Request):
    _inbox_conv(request, ccid)
    inbox.mark_read(S(request), ccid)
    return inbox.messages(S(request), ccid)


@app.post("/api/v1/inbox/{ccid}/send")
async def inbox_send(ccid: str, body: SendIn, request: Request):
    """The owner replying by hand from the dashboard."""
    _inbox_conv(request, ccid)
    text = body.text.strip()[:4000]
    if not text:
        raise HTTPException(400, "Message is empty.")
    conv = CH(request).conversation(ccid)
    S(request).add_message(conv["conversation_id"], "assistant", text)
    sent = await CH(request).send_text(conv, text, author="owner")
    audit.record(S(request), "owner", "message.manual", ccid, outcome="ok" if sent.ok else "failed")
    if not sent.ok and not sent.uncertain:
        raise HTTPException(502, f"Not sent: {sent.error}")
    return {"ok": True, "unconfirmed": sent.uncertain}


@app.post("/api/v1/inbox/{ccid}/archive")
def inbox_archive(ccid: str, body: ArchiveIn, request: Request):
    _inbox_conv(request, ccid)
    inbox.set_status(S(request), ccid, "archived" if body.archived else "active")
    return _inbox_conv(request, ccid)


@app.post("/api/v1/inbox/{ccid}/summarize")
async def inbox_summarize(ccid: str, request: Request):
    _inbox_conv(request, ccid)
    try:
        summary = await CH(request)._summarize(ccid, force=True)
    except llm.LLMError as e:
        raise HTTPException(400, str(e)) from e
    return {"summary": summary or _inbox_conv(request, ccid)["summary"]}


@app.delete("/api/v1/inbox/{ccid}")
def inbox_delete(ccid: str, request: Request):
    _inbox_conv(request, ccid)
    inbox.delete(S(request), ccid)
    audit.record(S(request), "owner", "conversation.delete", ccid)
    return {"ok": True}


@app.post("/api/v1/inbox/events/{event_id}/retry")
async def inbox_retry(event_id: str, request: Request):
    await _call(CH(request).retry, event_id)
    return {"ok": True}


# ----------------------------------------------------------------------------- approvals
class ApproveIn(BaseModel):
    text: str | None = None  # an edited reply draft


@app.get("/api/v1/approvals")
def list_approvals(request: Request, status: str | None = None):
    return approvals.list_all(S(request), status)


@app.get("/api/v1/approvals/{code}")
def get_approval(code: str, request: Request):
    a = approvals.get(S(request), code)
    if not a:
        raise HTTPException(404, "Approval not found.")
    return a


@app.post("/api/v1/approvals/{code}/approve")
async def approve(code: str, body: ApproveIn, request: Request):
    return await _call(CH(request).approve, code, "owner:dashboard", body.text)


@app.post("/api/v1/approvals/{code}/reject")
async def reject(code: str, request: Request):
    return await _call(CH(request).reject, code, "owner:dashboard")


# ----------------------------------------------------------------------------- requests
class BookIn(BaseModel):
    start: str               # local time without offset, e.g. 2026-11-21T10:00 (calendar's timezone)
    end: str
    title: str = ""
    add_meet: bool = True


class RequestPatch(BaseModel):
    status: str | None = None
    outcome: str | None = None
    notify_contact: bool = False  # Donna tells them the outcome (using only the outcome note)
    book: BookIn | None = None    # approve a meeting request by booking it on the calendar


@app.get("/api/v1/requests/types")
def request_types():
    return {"types": contact_requests.TYPES, "statuses": contact_requests.STATUSES}


@app.get("/api/v1/requests")
def list_requests(request: Request, contact_id: str = "", channel_id: str = "", type: str = "", status: str = ""):  # noqa: A002
    return contact_requests.list_all(S(request), contact_id, channel_id, type, status)


@app.get("/api/v1/requests/{rid}")
def get_request(rid: str, request: Request):
    try:
        r = contact_requests.get(S(request), rid)
    except contact_requests.RequestError:
        r = None
    if not r:
        raise HTTPException(404, "Request not found.")
    return r


@app.patch("/api/v1/requests/{rid}")
async def patch_request(rid: str, body: RequestPatch, request: Request):
    current = get_request(rid, request)  # 404 when unknown
    status, outcome, link = body.status or current["status"], body.outcome, ""
    confirmation = ""
    if body.book:
        booked = await CH(request).book_for_request(current, body.book.start, body.book.end, body.book.title, body.book.add_meet)
        if not booked["ok"]:  # nothing changes and nobody is told when the calendar refused
            raise HTTPException(400, f"Couldn't {'move' if current['event_id'] else 'create'} the event: {booked['result'][:300]}")
        link = booked["meet"]
        when = f"{contact_requests.fmt_local(booked['start'])} to {contact_requests.fmt_local(booked['end']).split(', ')[-1]}"
        # What the contact must see exactly as the calendar saved it, never paraphrased by the model.
        confirmation = f"📅 {when}" + (f"\nGoogle Meet: {link}" if link else "")
        status = "completed"
        outcome = (f"{outcome.strip()} " if outcome and outcome.strip() else "") \
            + f"{'Moved to' if booked['moved'] else 'Booked for'} {when}." + (f" Google Meet link: {link}" if link else "")
    r = await _call(contact_requests.set_status, S(request), rid, status, outcome, "owner")
    if body.book:
        r["booked"] = {"meet": link, "start": booked["start"], "end": booked["end"], "moved": booked["moved"]}
    if body.notify_contact:
        r["contact_informed"] = await CH(request).inform_about_request(r, must_include=confirmation)
    return r


# ----------------------------------------------------------------------------- memory, audit
@app.get("/api/v1/memories")
def list_memories(request: Request):
    return memory.list_global(S(request))


@app.post("/api/v1/memories")
async def add_memory(body: MemoryIn, request: Request):
    return await _call(memory.add, S(request), body.content, "global")


@app.delete("/api/v1/memories/{memory_id}")
def delete_memory(memory_id: str, request: Request):
    if not memory.delete(S(request), memory_id):
        raise HTTPException(404, "Memory not found.")
    return {"ok": True}


@app.get("/api/v1/audit")
def audit_log(request: Request, outcome: str | None = None, limit: int = 200):
    return audit.recent(S(request), max(1, min(limit, 1000)), outcome)


# =============================================================================== settings
class SettingsIn(BaseModel):
    model: str | None = None
    llm_provider: str | None = None
    claude_model: str | None = None
    timezone: str | None = None
    reasoning_effort: str | None = None
    pixel_grid: bool | None = None
    reduce_motion: bool | None = None
    onboarded: bool | None = None
    permission_mode: str | None = None
    messaging_enabled: bool | None = None
    require_trigger: bool | None = None
    trigger_word: str | None = None
    automations_enabled: bool | None = None
    owner_name: str | None = None
    owner_pronouns: str | None = None
    assistant_profile: str | None = None
    default_language: str | None = None
    default_tone: str | None = None
    contact_rate_per_hour: int | None = None
    global_rate_per_hour: int | None = None
    approval_ttl_hours: float | None = None
    unknown_contact_action: str | None = None
    openwa_url: str | None = None
    owner_channel: str | None = None
    greeting_enabled: bool | None = None
    greeting_mention_trigger: bool | None = None
    urgent_notify_dnd: bool | None = None
    urgent_notify_sleeping: bool | None = None
    calendar_auto_status: bool | None = None
    sleep_hours: dict | None = None
    menu_options: list[dict] | None = None
    meeting_default_minutes: int | None = None
    meeting_hours: dict | None = None


def _check_away_settings(values: dict) -> None:
    """Validate the availability / greeting settings (donna/availability.py)."""
    if "sleep_hours" in values:
        sh = values["sleep_hours"]
        try:
            availability._hhmm(sh.get("start", "")), availability._hhmm(sh.get("end", ""))
        except availability.AvailabilityError as e:
            raise HTTPException(400, f"Sleeping hours: {e}") from e
        values["sleep_hours"] = {"enabled": bool(sh.get("enabled")), "start": sh["start"].strip(), "end": sh["end"].strip()}
    if "meeting_hours" in values:
        mh = values["meeting_hours"]
        try:
            s, e = availability._hhmm(mh.get("start", "")), availability._hhmm(mh.get("end", ""))
        except availability.AvailabilityError as err:
            raise HTTPException(400, f"Meeting hours: {err}") from err
        days = sorted({int(d) for d in mh.get("days", []) if str(d).isdigit() and 0 <= int(d) <= 6})
        if e <= s or not days:
            raise HTTPException(400, "Meeting hours need an end after the start and at least one day.")
        values["meeting_hours"] = {"start": mh["start"].strip(), "end": mh["end"].strip(), "days": days}
    if "menu_options" in values:
        seen, items = set(), []
        for item in values["menu_options"]:
            oid = item.get("id")
            if oid in availability.MENU and oid not in seen:
                seen.add(oid)
                items.append({"id": oid, "enabled": bool(item.get("enabled", True))})
        items += [{"id": k, "enabled": False} for k in availability.MENU if k not in seen]
        values["menu_options"] = items
    if "meeting_default_minutes" in values and not 5 <= values["meeting_default_minutes"] <= 480:
        raise HTTPException(400, "meeting_default_minutes must be between 5 and 480")


def _public_settings(store: Store) -> dict:
    s = store.settings()
    key = vault.get(vault.GROQ_KEY) or ""
    s.pop("usage", None)
    return {**s, "groq_key_set": bool(key), "groq_key_hint": f"…{key[-4:]}" if key else "", "usage": store.usage(),
            "data_dir": str(DATA_DIR), "home": str(Path.home())}


@app.get("/api/v1/settings")
def get_settings(request: Request):
    return _public_settings(S(request))


@app.put("/api/v1/settings")
def put_settings(body: SettingsIn, request: Request):
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    if values.get("permission_mode", "manual") not in MODES:
        raise HTTPException(400, "permission_mode must be plan, manual or auto")
    if values.get("llm_provider", "groq") not in llm.PROVIDERS:
        raise HTTPException(400, "llm_provider must be groq or claude_code")
    if "claude_model" in values and not re.fullmatch(r"[a-z0-9.\-]{2,60}", values["claude_model"]):
        raise HTTPException(400, "Claude model must be an alias like opus or sonnet, or a model ID like claude-opus-5-5")
    if "trigger_word" in values:
        values["trigger_word"] = values["trigger_word"].strip()
        if not re.fullmatch(r"@?\w{2,30}", values["trigger_word"]):
            raise HTTPException(400, "The trigger must be one word, like @donna.")
    if values.get("owner_pronouns", "") not in ("", "he", "she", "they"):
        raise HTTPException(400, "owner_pronouns must be he, she or they")
    if values.get("unknown_contact_action", "ignore") not in ("ignore", "notify"):
        raise HTTPException(400, "unknown_contact_action must be ignore or notify")
    for k, lo, hi in (("contact_rate_per_hour", 1, 500), ("global_rate_per_hour", 1, 2000), ("approval_ttl_hours", 0.1, 168)):
        if k in values and not lo <= values[k] <= hi:
            raise HTTPException(400, f"{k} must be between {lo} and {hi}")
    _check_away_settings(values)
    if "openwa_url" in values and urlsplit(values["openwa_url"]).hostname not in ("127.0.0.1", "localhost"):
        raise HTTPException(400, "OpenWA must run on this computer (http://127.0.0.1:…).")
    for k in ("owner_name", "assistant_profile", "default_language", "default_tone"):
        if k in values:
            values[k] = values[k].strip()[:1000]
    if "messaging_enabled" in values or "automations_enabled" in values:
        audit.record(S(request), "owner", "settings.switches", "",
                     **{k: values[k] for k in ("messaging_enabled", "automations_enabled") if k in values})
    if "timezone" in values:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(values["timezone"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Unknown timezone: {values['timezone']}") from e
    S(request).set_settings(**values)
    if "llm_provider" in values:
        _health_cache.update(t=0.0)  # the status pill follows the provider at once
    return _public_settings(S(request))


# ----------------------------------------------------------------------------- availability (your status)
class AvailabilityIn(BaseModel):
    mode: str
    detail: str = ""
    until: float | None = None  # epoch seconds; None = until you change it


async def _availability(store: Store) -> dict:
    settings = store.settings()
    status = await availability.effective(store)
    return {"status": {**status, "phrase": availability.phrase(status, settings, store),
                       "until_text": availability.until_text(status["until"], settings).strip()},
            "manual": settings.get("availability"),
            "modes": [{"id": k, **v} for k, v in availability.MODES.items() if k in availability.MANUAL_MODES],
            "menu": [{"id": k, **v} for k, v in availability.MENU.items()]}


@app.get("/api/v1/availability")
async def get_availability(request: Request):
    return await _availability(S(request))


@app.put("/api/v1/availability")
async def put_availability(body: AvailabilityIn, request: Request):
    try:
        availability.set_status(S(request), body.mode, body.detail, body.until)
    except availability.AvailabilityError as e:
        raise HTTPException(400, str(e)) from e
    audit.record(S(request), "owner", "availability.set", body.mode)
    return await _availability(S(request))


@app.get("/api/v1/requests/{rid}/clash")
async def request_clash(rid: str, request: Request):
    """Whether the owner's calendar is free at the time a meeting request asks for (free/busy only)."""
    r = get_request(rid, request)
    start = (r["details"].get("start") or "")[:16]
    if not start:
        return {"checked": False, "free": None, "detail": "No time yet."}
    settings = S(request).settings()
    minutes = int(r["details"].get("duration_minutes") or settings.get("meeting_default_minutes") or 30)
    try:
        tz = ZoneInfo(settings.get("timezone") or "UTC")
        s = datetime.fromisoformat(start).replace(tzinfo=tz)
        e = s + timedelta(minutes=minutes)
        busy = await asyncio.to_thread(availability._busy, s, e)
    except Exception as err:  # noqa: BLE001 - Google not connected: the card just doesn't show a check
        return {"checked": False, "free": None, "detail": str(err)[:200]}
    clash = [b for b in busy if b[0] < e and b[1] > s]
    return {"checked": True, "free": not clash, "start": start, "minutes": minutes,
            "end": (s + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M"),
            "detail": "Your calendar is free at that time." if not clash else "You already have something at that time."}


class KeyIn(BaseModel):
    key: str | None = None


@app.put("/api/v1/settings/groq-key")
async def put_groq_key(body: KeyIn, request: Request):
    key = (body.key or vault.get(vault.GROQ_KEY) or "").strip()
    if not key:
        raise HTTPException(400, "Paste your Groq API key first.")
    try:
        models = await llm.validate_key(key)
    except llm.LLMError as e:
        raise HTTPException(400, str(e)) from e
    if body.key:
        vault.set(vault.GROQ_KEY, key)
    _health_cache.update(t=0.0)
    chat = [m for m in models if "gpt-oss" in m or "qwen" in m or "llama" in m]
    return {"ok": True, "models": chat or models, "hint": f"…{key[-4:]}"}


@app.get("/api/v1/export")
def export(request: Request):
    data = json.dumps(S(request).export_all(), indent=2, default=str)
    name = time.strftime("donna-export-%Y-%m-%d.json")
    return Response(data, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/v1/wipe")
def wipe(request: Request):
    for run in list(RUNS.values()):
        run.cancel()
    return {"ok": True, "deleted": S(request).wipe()}


@app.post("/api/v1/open-data-folder")
def open_data_folder():
    if os.name == "nt":
        os.startfile(DATA_DIR)  # noqa: S606 - opens the local data folder in Explorer
    return {"ok": True, "path": str(DATA_DIR)}


# =============================================================================== UI
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = (WEB_DIST / path).resolve()
        if path and target.is_file() and WEB_DIST.resolve() in target.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("fonts/") else {}
            return FileResponse(target, headers=headers)
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})
