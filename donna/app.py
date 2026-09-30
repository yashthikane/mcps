"""FastAPI app: REST + Server-Sent Events API under /api/v1, and the built React UI at /."""
import asyncio
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
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

from . import DATA_DIR, ROOT, llm, pg, tasks, vault
from .agent import MODES, RUNS, Run, run_chat
from .hub import BUILTIN, MCPHub, env_key, split_args
from .notify import Notifier
from .scheduler_client import SchedulerClient, ScheduleInvalid, SchedulerUnavailable
from .store import Store

log = logging.getLogger("donna")
WEB_DIST = ROOT / "web" / "dist"
# Browsers send Origin on cross-site requests; only pages served from this machine may call the API.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


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
    log.info("Donna started with %d tools", len(app.state.hub.all_tools()))
    yield
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
    value = {"groq": groq_state, "online": groq_state != "unreachable", "usage": S(request).usage()}
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


# =============================================================================== settings
class SettingsIn(BaseModel):
    model: str | None = None
    timezone: str | None = None
    reasoning_effort: str | None = None
    pixel_grid: bool | None = None
    reduce_motion: bool | None = None
    onboarded: bool | None = None
    permission_mode: str | None = None


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
    if "timezone" in values:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(values["timezone"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Unknown timezone: {values['timezone']}") from e
    S(request).set_settings(**values)
    return _public_settings(S(request))


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
