# Donna: Architecture

How Donna is built today (2026-09-30, including the BullMQ scheduler). For the plan and what is still missing, see `docs/ROADMAP.md` and `progress.md`. The scheduler's design brief is `docs/donna_architecture_conversion_bullmq.md`.

---

## 1. Overview

Donna is a local AI assistant with a web UI. The main Python process (FastAPI):

- serves a React single-page app;
- exposes a REST + Server-Sent Events (SSE) API;
- runs a Groq tool-calling loop against MCP tools. These are 32 built-in tools (Gmail, Calendar, Notion, weather, utilities, scheduling) plus any MCP servers the user adds.

**Scheduled tasks** (reminders and AI tasks) add a small local subsystem (§4):
- a Node + TypeScript **scheduler** service with a BullMQ queue and worker;
- **Redis** (in WSL Ubuntu) for the queue;
- **PostgreSQL** for the durable task and execution state.

The scheduler only decides *when*. At run time it asks FastAPI to execute the task, and FastAPI's agent and MCPHub decide *how*.

| Process | Address | Role |
|---|---|---|
| FastAPI (`python -m donna`) | 127.0.0.1:8765 | UI, API, chat agent, MCPHub, task CRUD, task execution, notifications |
| Scheduler (`scheduler/`, Node) | 127.0.0.1:8766 | BullMQ queue + worker (concurrency 1), recurrence math, reconcile loop |
| Redis 7 (WSL Ubuntu) | 127.0.0.1:6379 | BullMQ job state (AOF on; rebuildable from PostgreSQL) |
| PostgreSQL 18 (Windows service) | 127.0.0.1:5432 | `scheduled_tasks`, `task_executions`, `notifications` |

**Hard constraints** (they drive most design decisions):

| Constraint | Consequence in the code |
|---|---|
| Free and local only | Every service is bound to `127.0.0.1`. Origin checks, SQLite, local PostgreSQL/Redis, OS keyring. No cloud, no CI/CD. |
| Groq free tier: 8K tokens/min, 30 req/min, 1K req/day per model | Keyword tool routing, history and tool-result truncation, `retry-after` handling, daily usage counter. Reminders never call Groq, and AI tasks run one at a time. |
| Windows laptop, 8 GB RAM | No Docker. Worker concurrency 1, DB pools of at most 3–4 connections, and an in-memory MCP transport for the built-in tools. Scheduling is optional: Donna runs without it. |

```
┌──────────────────────────── Browser ─────────────────────────────┐
│  React 18 SPA (web/)  App.tsx state · components · api.ts        │
│     REST (fetch, JSON)                  SSE (POST → event stream) │
└───────────────┬───────────────────────────────┬──────────────────┘
                │  http://127.0.0.1:8765/api/v1 │
┌───────────────▼───────────────────────────────▼──────────────────┐
│ FastAPI (donna/app.py)   local_only Origin middleware · routes    │
│   │                                                              │
│   ├─ agent.py   run_chat: model turn → tools → model turn … ≤ 8  │
│   │     ├─ llm.py     Groq streaming (or the scripted fake model) │
│   │     └─ hub.py     MCPHub: tool catalog + calls                │
│   │           ├─ in-memory fastmcp Client → mcp_instance.mcp      │
│   │           │      (server.py imports tools/*.py → 32 tools)    │
│   │           └─ one Client per user MCP server (stdio | HTTP)    │
│   ├─ store.py   SQLite (WAL + FTS5)        data/donna.db          │
│   ├─ vault.py   keyring → Windows Credential Manager              │
│   ├─ tasks.py   TaskService: CRUD · execute (reminder | AI task)  │
│   ├─ notify.py  Windows toast · in-app notifications              │
│   └─ pg.py      PostgreSQL pool (sync, via to_thread)  ───────────┼──┐
│                                                                  │  │
│ Static: web/dist (built UI) served at /                          │  │
└──────┬─────────────────────────────▲─────────────────────────────┘  │
       │ sync / preview / run        │ POST /internal/…/execute       │
       │ (Bearer token)              │ (Bearer token)                 │
┌──────▼─────────────────────────────┴──────────────┐          ┌──────▼──────┐
│ Scheduler (Node, :8766)  scheduler/src            │─────────▶│ PostgreSQL  │
│  routes → Scheduler.sync/unschedule/runNow        │  tasks,  │ scheduled_  │
│  Worker (BullMQ, concurrency 1) → Executor        │  execs   │ tasks …     │
│  reconcile(): Redis ← PostgreSQL                  │          └─────────────┘
└──────────────────────┬────────────────────────────┘
                       │ ioredis
                ┌──────▼──────┐
                │ Redis (WSL) │  delayed jobs, retries
                └─────────────┘

External HTTPS: Groq API · Google (Gmail/Calendar) · Notion API · Open-Meteo
```

---

## 2. Repository layout

```
mcps/
├─ donna/                 FastAPI backend (the web app)
│  ├─ __init__.py         ROOT, DATA_DIR (env DONNA_DATA_DIR), sys.path setup
│  ├─ __main__.py         `python -m donna` → uvicorn on 127.0.0.1:8765, opens browser
│  ├─ app.py              all HTTP routes, lifespan, Origin middleware, SPA serving
│  ├─ agent.py            chat loop, tool routing, approval gate, history
│  ├─ hub.py              MCPHub: built-in + external MCP clients, tool catalog
│  ├─ llm.py              Groq streaming, retries, key validation, fake model
│  ├─ store.py            SQLite storage
│  ├─ vault.py            secrets in the OS credential store
│  ├─ pg.py               PostgreSQL pool + migration runner (scheduling data)
│  ├─ tasks.py            TaskService: task CRUD, scheduler sync, execution
│  ├─ scheduler_client.py HTTP client for the Node scheduler
│  └─ notify.py           notification channels (Windows toast, in-app)
├─ scheduler/             Node + TypeScript scheduler service (BullMQ)
│  └─ src/                server, config, db, queue, scheduler, worker, executor,
│                         recurrence, policy, routes/tasks (+ *.test.ts)
├─ migrations/postgres/   001_scheduled_tasks, 002_task_executions, 003_notifications
├─ mcp_instance.py        the shared FastMCP("Donna") instance
├─ server.py              imports tools/* so their @mcp.tool() register; runnable as stdio server
├─ tools/                 built-in tool modules
│  ├─ google_auth.py      one OAuth sign-in for Gmail + Calendar
│  ├─ gmail_tools.py      12 tools
│  ├─ calendar_tools.py   5 tools
│  ├─ notion_tools.py     8 tools
│  ├─ weather_tools.py    1 tool (Open-Meteo)
│  ├─ misc_tools.py       2 tools (square, get_jokes)
│  └─ schedule_tools.py   4 tools (schedule, list, cancel, snooze tasks)
├─ client.py              legacy terminal chat (spawns server.py over stdio)
├─ web/                   React + Vite UI
│  ├─ src/main.tsx        entry: fonts, CSS, ToastProvider, <App/>
│  ├─ src/App.tsx         all app state and the chat event handling
│  ├─ src/api.ts          typed REST client + SSE reader
│  ├─ src/components/     Chat, Sidebar, RunPanel, Connections (+ wizards), Settings,
│  │                      Onboarding, Palette, Markdown, ui (Dialog/Switch/Toast…)
│  ├─ src/icons.tsx       inline SVG icons
│  ├─ src/neon-dusk.css   design system (ported from docs/design/donna-neon-dusk-demo.html)
│  ├─ src/app.css         app-specific styles
│  └─ public/fonts/       GeistPixel-Circle.woff2
├─ tests/                 pytest (unit + API), e2e_ui.py (browser), e2e_reset.py
├─ scripts/               start.ps1, dev.ps1, check.ps1, setup-scheduler.ps1, scheduler.ps1
├─ docs/                  ROADMAP.md, ARCHITECTURE.md, donna_architecture_conversion_bullmq.md, design/
└─ data/                  (gitignored) donna.db, donna.log, scheduler.log
```

---

## 3. Backend

### 3.1 Startup (`donna/__main__.py`, `app.py` lifespan)

1. `python -m donna` starts uvicorn on `127.0.0.1:8765` (`--port`, `--no-browser`, `--reload`). It opens the browser after 1.5 s.
2. The lifespan then:
   - sets up rotating logging to `data/donna.log` (1 MB × 3);
   - runs `load_dotenv(.env)` and `vault.import_from_env()`, a one-time copy of `GROQ_API_KEY` and `INTERNAL_INTERGRATION_TOKEN`/`NOTION_API_KEY` into the keyring;
   - runs `import server`. Each `tools/*.py` registers its functions on `mcp_instance.mcp`;
   - creates the `Store` (SQLite);
   - creates `MCPHub(store, mcp)` and calls `hub.start()`. This opens the in-memory client, lists the built-in tools, and connects every enabled external server;
   - makes sure `internal_api_token` exists in the vault, opens PostgreSQL if it's configured and reachable, and applies pending migrations. Failures are logged and Donna keeps running;
   - creates `Notifier` and `TaskService` and sets `tasks.service` (the chat scheduling tools use it).
3. On shutdown, `hub.stop()` closes all MCP clients (and so stops the stdio subprocesses).

`app.state.store` and `app.state.hub` are the two singletons that routes use (the `S(request)` and `H(request)` helpers).

### 3.2 HTTP API (`donna/app.py`)

All routes are under `/api/v1`. OpenAPI docs are at `/api/docs`.

**Security:** the `local_only` middleware rejects any request whose `Origin` host isn't `127.0.0.1` or `localhost` (403). With the loopback bind, this stops other websites from driving the API.

| Area | Method & path | Purpose |
|---|---|---|
| Health | `GET /health` | Groq key state (`ok`, `no_key`, `invalid_key`, `unreachable`, `error`) plus today's usage. Cached for 20 s. |
| Conversations | `GET /conversations?q=` | List, or full-text search when `q` is given |
| | `POST /conversations` | Create ("New chat") |
| | `PATCH /conversations/{id}` | Rename or pin |
| | `DELETE /conversations/{id}` | Delete (cancels any run in it) |
| | `GET /conversations/{id}/messages` | Message history |
| Chat | `POST /conversations/{id}/messages` | Body `{text, connections[]}`. Returns an **SSE stream**. 409 if a run is already active in that conversation. |
| Runs | `POST /runs/{id}/confirm` | Body `{call_id, approved}`. Resolves a pending approval. |
| | `POST /runs/{id}/cancel` | Stop |
| Connections | `GET /connections` | Built-in and external connections with state and per-tool info |
| | `PATCH /connections/{id}` | `enabled`, `disabled_tools` |
| Google | `POST /connections/google/credentials` | Upload `credentials.json` (multipart) |
| | `POST /connections/google/authorize` | Runs the local OAuth flow (blocks up to 300 s, in a thread) |
| | `POST /connections/google/test` | Next 3 events + 3 inbox subjects |
| | `DELETE /connections/google?forget_client=` | Sign out |
| Notion | `POST /connections/notion` | Validate the token (`users.me` + `search`), save it, return visible pages |
| | `DELETE /connections/notion` | Forget the token |
| MCP servers | `POST /mcp-servers/test` | Start once, list tools, stop |
| | `POST /mcp-servers` | Save the config (env values go to the vault), then connect |
| | `DELETE /mcp-servers/{id}` | Stop, delete the env secrets and the row |
| | `POST /mcp-servers/{id}/restart` | Reconnect |
| Settings | `GET/PUT /settings` | model, timezone (validated with `ZoneInfo`), reasoning_effort, pixel_grid, reduce_motion, onboarded |
| | `PUT /settings/groq-key` | Validate with `models.list`, store the key, return the chat models |
| Data | `GET /export` | JSON download of all conversations and messages |
| | `POST /wipe` | Delete all conversations |
| | `POST /open-data-folder` | Open `data/` in Explorer |
| Scheduling | `/scheduled-tasks…`, `/scheduler/health`, `/notifications…`, `/internal/scheduled-tasks/{id}/execute` | See §4.6 |
| UI | `GET /assets/*`, `GET /{path}` | Built SPA. Unknown paths return `index.html` (no-cache). Fonts are cached as immutable. |

**SSE framing:** each event is `event: <type>\ndata: <json>\n\n`. The response carries `Cache-Control: no-cache` and `X-Accel-Buffering: no`. If the client disconnects, the run is cancelled. The run is always removed from `RUNS` in a `finally`.

### 3.3 Agent loop (`donna/agent.py`)

`run_chat(store, hub, run, text, forced_groups)` is an async generator of UI events.

```
load settings, conversation, prior history, recent tool groups
save user message; auto-title "New chat" from the first ~7 words
yield run.start
no Groq key? → save ⚠ message, yield error, return
tools = select_tools(enabled tools, text, recent groups, forced @connections)
messages = [system_prompt, *history, user]
repeat up to MAX_STEPS (8):
    stream one model turn → yield delta / status; record usage
    no tool calls or cancelled → break
    append assistant message with tool_calls
    for each call: _run_tool → yields tool.start / confirm.request / tool.end
                   append {"role":"tool", content: result[:4000]}
else: append "(Stopped after 8 tool steps.)"
save assistant message with tool_events (+ "[stopped]" / "⚠ error")
yield error? and done
```

**Events sent to the UI:** `run.start`, `delta`, `status` (for example "rate limit, retrying in 12s…"), `tool.start`, `confirm.request`, `tool.end`, `done`, `error`.

**Run registry:** `RUNS: dict[run_id, Run]` lives in process memory. A `Run` holds a `cancelled` flag and `pending: {call_id: asyncio.Future}` for approvals.

**Approval gate (`_run_tool`):**
1. Parse the JSON arguments. An unknown or disabled tool, or bad JSON, returns an `Error…` string to the model (no exception).
2. If `tool.confirm` is set, it creates a Future, yields `confirm.request` and awaits it for up to **600 s**. A timeout, rejection or Stop counts as rejected. The model is then told: "The user rejected this action. Do not retry it."
3. It yields `tool.start`, calls `hub.call`, times the call, and yields `tool.end`. The result preview is capped at 1,500 characters in the saved event.

**Permission modes** (`agent.MODES`, per run):

| Mode | Tools offered | Gated tools (`hub.CONFIRM`, write-like external tools, `schedule_task` with `permission_mode="auto"`) |
|---|---|---|
| `plan` | Read-only only (tools with `Tool.writes` are hidden), plus a plan-only system prompt | Never run ("Not run (plan mode)") |
| `manual` (default) | All | Wait for Approve / Reject. In a scheduled run they're refused and the execution becomes `approval_required`. |
| `auto` | All | Run immediately. The tool event is saved with `approved: "auto"` and the UI shows an **AUTO** tag. |

- **Chat:** the UI sends `mode` with every message. The composer's Plan / Manual / Auto switch (Shift+Tab to cycle) is saved as the `permission_mode` setting.
- **Scheduled AI tasks:** each task has its own `payload.permission_mode`, set in the task editor or with `schedule_task(permission_mode=…)`. Creating an Auto task from chat is itself approval-gated in Manual mode, so text like "don't ask me" can't grant unattended permissions without a click.

**Token-saving measures** (to fit 8K tokens/min):
- `select_tools`: regex `KEYWORDS` per built-in group (gmail, calendar, notion, weather, utils). External tools are matched by the words in their names. Groups used in the last 4 messages are kept for follow-ups. If nothing matches, all tools are sent.
- `history`: newest-first, capped at about 9,000 characters. Earlier `tool_events` are folded into the assistant text as `[Tool results: name → preview…]`, so follow-ups ("delete that page") can reuse IDs without re-sending tool messages.
- Tool results sent back to the model are capped at 4,000 characters. Tool descriptions are capped at 600 characters.

**System prompt:** current date and time in the user's timezone, plus rules: never invent IDs, use local calendar times without an offset, don't ask for confirmation in text (the gate handles it), point to Connections on auth errors, don't retry rejected actions, one tool at a time.

### 3.4 LLM gateway (`donna/llm.py`)

- `stream_turn(api_key, model, messages, tools, effort)` yields `("delta", str)`, `("status", str)`, `("tool_calls", [...])` and `("usage", dict)`.
- It uses `groq.AsyncGroq` with its own retry policy (`max_retries=0`) and a 90 s timeout. Settings: `temperature=1`, `max_completion_tokens=2048`, `tool_choice="auto"`, `parallel_tool_calls=False`.
- `reasoning_effort` is only sent for `openai/gpt-oss*` models. The default model is `openai/gpt-oss-120b`.
- Streamed tool-call fragments are accumulated **by index** (the id, name and argument pieces arrive separately).
- Error handling:

| Error | Behaviour |
|---|---|
| `AuthenticationError` | `LLMError`: "Groq rejected the API key" |
| `RateLimitError` (429) | Waits for `retry-after`. Up to 3 retries. Gives up if the wait is over 60 s. |
| `BadRequestError` `tool_use_failed` | Retries with a "call the tool again with valid JSON" nudge (2 attempts) |
| 5xx | Linear backoff, up to 3 retries |
| Connection error | `LLMError`: "Can't reach Groq" |

- `validate_key` lists the models the key can use.
- **Fake model** (`DONNA_FAKE_LLM=1`): `_fake_turn` scripts responses (weather, square, create event, filesystem listing, a slow stream, echo) for tests without network access.

### 3.5 MCP hub (`donna/hub.py`)

- **Built-in:** one long-lived `fastmcp.Client(mcp_instance.mcp)`, using the in-memory transport (no subprocess). Each tool is wrapped in a `Tool` dataclass: `name`, `remote`, `server="donna"`, `group`, `description`, `schema`, `confirm`.
- **`BUILTIN`** maps connection id → metadata (name, icon, setup kind) and tool names. `TOOL_GROUP` is its inverse.
- **`CONFIRM`** (built-in tools that always need approval): `send_email`, `reply_email`, `forward_email`, `send_draft`, `delete_email`, `create_event`, `update_event`, `delete_event`, `delete_page`.
- **External servers** (`External`: config, client, tools, state `connected|error|stopped|connecting`, error):
  - Transport is `StdioTransport(command, args, env)` or `StreamableHttpTransport(url)`. Connecting times out after 60 s (init 45 s).
  - Tools are exposed as `<slug(server name)>__<tool>`, truncated to 64 characters.
  - `confirm` is set when the tool name matches `WRITE_WORDS` (write, edit, delete, create, send, run, …) or the tool declares `destructiveHint`.
  - stdio servers get **only** their configured env vars. The values come from the vault under `mcp_env::<server_id>::<KEY>`.
  - On Windows, `resolve_command` turns `npx`/`uvx` into `npx.cmd`/`uvx.exe`. The bare name fails with WinError 193.
- `enabled_tools(settings)` filters out disabled built-in connections and tools, and disconnected or disabled external servers and tools.
- `call(tool, args)` uses `call_tool(..., raise_on_error=False)` with a 90 s timeout. It joins the text content. **ok** = not `is_error` and the text doesn't start with `"Error"`.

### 3.6 Storage (`donna/store.py`)

stdlib `sqlite3` with one connection (`check_same_thread=False`) guarded by a `threading.Lock`, WAL mode, and foreign keys on. The file is `DATA_DIR/donna.db`.

| Table | Columns / notes |
|---|---|
| `conversations` | `id`, `title`, `pinned`, `created_at`, `updated_at`. Listed pinned first, then by recency, with the last assistant message as a preview. |
| `messages` | `id`, `conversation_id` (FK, cascade), `role`, `content`, `tool_events` (JSON), `created_at`. Indexed on `(conversation_id, created_at)`. |
| `messages_fts` | FTS5 over `content` (`message_id`, `conversation_id` unindexed). Search is prefix-quoted terms plus a title `LIKE`, with `snippet()` highlights. |
| `mcp_servers` | `id`, `name`, `transport`, `command`, `args` (JSON), `url`, `env_keys` (JSON, names only), `enabled`, `disabled_tools` (JSON), `created_at` |
| `settings` | `key` → JSON `value`. Only keys in `DEFAULT_SETTINGS` are accepted. |

`DEFAULT_SETTINGS`: `model`, `timezone`, `reasoning_effort`, `pixel_grid`, `reduce_motion`, `disabled_tools`, `disabled_connections`, `usage` (`{date, requests, tokens}`, reset daily; `bump_usage` runs on every Groq usage chunk), `onboarded`.

### 3.7 Secrets (`donna/vault.py`)

- `keyring` → Windows Credential Manager, service `donna` (`DONNA_KEYRING_SERVICE` overrides it, for tests).
- Values over 500 characters are split into `name#0..n`, with `name#count` written last. WinVault rejects long passwords.
- Stored secrets:

| Key | Contents |
|---|---|
| `groq_api_key` | Groq API key |
| `notion_token` | Notion integration secret |
| `google_client` | `{type, client_id, client_secret, project_id}` |
| `google_token` | `{refresh_token, scopes, email}` |
| `mcp_env::<id>::<KEY>` | Env values for external MCP servers |
| `postgres_url` | Scheduling database URL (written by `setup-scheduler.ps1`) |
| `internal_api_token` | Shared by FastAPI and the scheduler for `/internal/*` calls |

No secret is ever written to SQLite, logs or the UI. The UI only sees a `…abcd` hint of the Groq key.

---

## 4. Scheduled tasks (BullMQ scheduler)

### 4.1 Responsibilities

| Component | Owns | Never does |
|---|---|---|
| React (`Tasks.tsx`) | Task list, create/edit, run now, snooze, pause, cancel, history | Talk to Redis, BullMQ or the scheduler directly |
| FastAPI (`donna/tasks.py`) | Task CRUD in PostgreSQL, execution (reminder → notify; AI task → agent), notifications | Recurrence math or queue state |
| Scheduler (`scheduler/`) | Recurrence (`recurrence.ts`), `next_run_at`, BullMQ jobs, worker, retries, catch-up, reconcile | Touch SQLite, MCP or Groq; store credentials |
| Redis | BullMQ's operational state | Hold anything that can't be rebuilt from PostgreSQL |
| PostgreSQL | The source of truth: `scheduled_tasks`, `task_executions`, `notifications` | Hold conversations, messages or settings (those stay in SQLite) |

### 4.2 Data model (`migrations/postgres/`)

- **`scheduled_tasks`**:
  - `type` (`reminder` | `ai_task`);
  - `status` (`scheduled` | `running` | `completed` | `cancelled` | `failed` | `paused`);
  - `run_at` and `timezone`;
  - `recurrence_type` (`once` | `interval` | `cron`) and `recurrence_rule`;
  - `payload` (`message`, `prompt`, `conversation_id`) and `notification_channels`;
  - fields the scheduler writes: `next_run_at`, `bullmq_job_id`, `last_run_at`.
- **`task_executions`**: one row per occurrence.
  - **`UNIQUE (task_id, scheduled_for)`** is the idempotency key.
  - `status` is one of `pending`, `running`, `succeeded`, `failed`, `skipped`, `missed`, `approval_required`.
  - Other columns: `trigger` (`schedule` | `manual`), `attempts`, `result` / `error`, and `conversation_id` (AI runs).
- **`notifications`**: the in-app channel. Unread rows are polled by the UI and then marked read.
- **Migrations** are applied in order by `donna/pg.py` (`schema_migrations` table + advisory lock). They run at FastAPI startup or with `python -m donna migrate`.

### 4.3 Recurrence and timezones

- **`once`**: runs at `run_at`.
- **`interval`**: `30m`, `2h` or `1d`, anchored at `run_at`. The spacing is fixed; for "every day at 9" use cron.
- **`cron`**: a 5-field rule evaluated with cron-parser in the task's IANA **timezone**, so 09:00 stays 09:00 across daylight-saving changes.
- **Input times:** a local time without an offset (`2026-10-01T09:00`) is read in the task's timezone. An explicit offset is kept, and "in 30 minutes" is also accepted. Nothing is silently treated as UTC.
- **Single source of the math:** only `recurrence.ts` computes occurrences. FastAPI validates rules through the scheduler's `POST /preview`.

### 4.4 Lifecycle

```
create/update/cancel/pause/resume/snooze (UI, API or chat tool)
  → FastAPI writes PostgreSQL (schedule change ⇒ next_run_at = NULL; snooze sets it directly)
  → POST scheduler /tasks/{id}/sync
      status ≠ scheduled → remove job, clear next_run_at
      else next_run_at ??= firstOccurrence(); queue.add(jobId = task-<id>-<epochMs>, delay)

job due → worker (concurrency 1)
  1. load task; skip if deleted / cancelled / paused / done, or next_run_at ≠ job.scheduledFor (stale)
  2. claimExecution (INSERT … ON CONFLICT (task_id, scheduled_for)): already finished → skip + advance
  3. catch-up policy: reminder ≤5 min late → run; ≤12 h → "missed reminder" notice; older → missed (silent)
                      AI task ≤60 min late → run; older → missed          (env-configurable)
  4. POST FastAPI /api/v1/internal/scheduled-tasks/{id}/execute {execution_id, scheduled_for, mode}
  5. success → record result → advance: once ⇒ completed; recurring ⇒ next occurrence + new job
     retryable failure (network, 5xx, 409, rate limit) → BullMQ exponential backoff (3 attempts, 5 s base)
     permanent / last attempt → record failed → advance (a recurring series keeps going)
```

- **Manual run** (`POST /scheduled-tasks/{id}/run`) also goes through BullMQ and the worker. It uses job ID `manual-<id>-<ms>`, ignores status and lateness, and doesn't move the schedule.
- **Reconcile** runs at scheduler startup, every time Redis (re)connects, and every `RECONCILE_SEC` (300). For every `scheduled`/`running` task whose job is missing, finished or pointing at the wrong occurrence, it re-syncs from PostgreSQL. This covers a Redis restart, a crash between the database write and `queue.add`, and lost queue state.
- **Crash during a run:** the job stalls, and BullMQ hands it out again. The execution row still says `running`, so the occurrence is retried. That is safe because unattended runs can't perform write actions, and a reminder at worst shows twice.

### 4.5 Execution (FastAPI)

- **Reminder:** `notify.send()` goes to the task's channels. There is **no Groq call**.
- **Missed reminder:** a "Missed reminder: …" notice.
- **AI task:**
  - uses one SQLite conversation per task ("⏰ title", ID kept in `payload.conversation_id`);
  - runs `agent.run_chat` with `Run(unattended=True)`, registered in `RUNS`, so the UI and scheduled runs never overlap in one conversation (409 → retry);
  - approval gate follows the task's permission mode: **manual** refuses gated tools (the execution becomes `approval_required`), **auto** runs them, and **plan** only reads and writes a plan;
  - the `schedule` tool group is hidden, so a task can't schedule more tasks;
  - the first 200 characters of the reply are sent as a notification with an "Open chat" link.
- **Notifications** (`notify.py`):
  - `windows`: a WinRT toast through PowerShell. The XML arrives in an environment variable and is never interpolated into script;
  - `in_app`: a `notifications` row;
  - adding a channel means adding a class with `send(note)`.

### 4.6 APIs

| Caller → callee | Endpoint | Notes |
|---|---|---|
| UI → FastAPI | `GET/POST /scheduled-tasks`, `GET/PATCH/DELETE /scheduled-tasks/{id}` | 400 validation, 404 unknown, 503 when PostgreSQL or the scheduler is down |
| | `POST /scheduled-tasks/{id}/cancel`, `pause`, `resume`, `snooze {minutes}`, `run` | |
| | `POST /scheduled-tasks/preview` | Next 3 runs (through the scheduler) |
| | `GET /scheduler/health` | Scheduler, Redis, worker and PostgreSQL state, with a hint |
| | `GET /notifications`, `POST /notifications/read {ids}` | In-app feed |
| Scheduler → FastAPI | `POST /internal/scheduled-tasks/{id}/execute` | `Bearer internal_api_token`, not in OpenAPI |
| FastAPI → scheduler | `POST /preview`, `/tasks/{id}/sync`, `/tasks/{id}/unschedule`, `/tasks/{id}/run`; `GET /health` | `Bearer` (except `/health`). Requests with an `Origin` header are refused. |

**Chat tools** (`tools/schedule_tools.py`, the `schedule` connection):
- `schedule_task`;
- `list_scheduled_tasks`;
- `cancel_scheduled_task` (asks for approval);
- `snooze_scheduled_task`.

### 4.7 Security and failure behaviour

- **Localhost only:** all four services are bound to 127.0.0.1. The scheduler refuses any other host.
- **Internal token:** generated into the vault. The scheduler receives it, together with `postgres_url`, as process-scoped env from `scripts/scheduler.ps1`. Tokens, payloads and credentials are never logged. Task payloads hold no credentials.
- **PostgreSQL down:**
  - Donna chat still works; the scheduling routes return 503, and the notification poll returns `[]`;
  - reconnection is retried at most every 15 s;
  - the worker fails its jobs (retry), then reconcile re-queues them. It never acts without the authoritative row.
- **Redis down:** tasks stay safe in PostgreSQL. Scheduler routes answer 503 within 5 s, and a create returns the task with a `warning`. When Redis comes back, reconcile re-queues everything.
- **FastAPI down:** the executor's call fails as a network error and BullMQ retries it. An AI task past its catch-up window becomes `missed`.

### 4.8 Running it

- **One-time setup:** `scripts\setup-scheduler.ps1`:
  1. `apt install redis-server` in WSL Ubuntu;
  2. create the `donna` role and database (with a generated password, saved to the vault);
  3. `npm install` + build in `scheduler/`;
  4. migrate.
- **Every start:** `scripts\start.ps1` launches `scripts\scheduler.ps1 -Log` hidden, before Donna.
  - `scheduler.ps1` starts `redis-server` in the foreground of a hidden `wsl.exe` (AOF, `/var/lib/redis-donna`), which keeps WSL alive;
  - it then runs `node scheduler/dist/server.js`, logging to `data/scheduler.log`.
  - Restarting FastAPI doesn't stop the scheduler.

---

## 5. Built-in tools (`tools/`)

**Conventions:**
- Tools return a plain `str`. Failures are strings starting with `"Error"`. Tools never raise.
- The docstring and type hints become the JSON schema the model sees.
- Google tools are `async` and run googleapiclient in `asyncio.to_thread`.

| Group | Tools | Backend |
|---|---|---|
| Gmail (12) | `list_emails`, `read_email`, `search_emails`, `get_unread_emails`, `send_email`★, `reply_email`★, `forward_email`★, `mark_email`, `delete_email`★ (trash), `create_draft`, `list_drafts`, `send_draft`★ | Gmail API v1 |
| Calendar (5) | `get_events`, `list_events`, `create_event`★, `update_event`★, `delete_event`★ | Calendar API v3, primary calendar |
| Notion (8) | `search_notion`, `list_pages`, `read_page_content`, `create_page`, `update_page_title`, `append_text_to_page`, `delete_page`★, `query_database` | notion-client 3.x, API 2025-09-03 |
| Weather (1) | `get_weather` | Open-Meteo geocoding + forecast (no key) |
| Utilities (2) | `square`, `get_jokes` | local / public joke API |
| Scheduled tasks (4) | `schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task`★, `snooze_scheduled_task` | `donna.tasks.service` (§4) |

★ = needs approval (`hub.CONFIRM`).

**Google auth (`tools/google_auth.py`):**
- One sign-in covers both services, with scopes `gmail.modify`, `gmail.send` and `calendar`.
- Setup:
  1. Upload `credentials.json` (a Desktop OAuth client). It is validated, and only the id/secret/project are kept.
  2. `authorize()` runs `InstalledAppFlow.run_local_server(port=0)` with `access_type=offline`, `prompt=consent`. This is blocking, so it is called from a thread.
  3. Only the refresh token, scopes and email are stored.
- `credentials()` caches a refreshed `Credentials` behind a lock. A `RefreshError` becomes the `needs_reauth` state (Testing-mode tokens expire after 7 days).
- `status()` returns `not_set_up`, `needs_auth`, `needs_reauth` or `connected`, cached for 60 s.

**Calendar time model:**
- Tools take local times without an offset (`2026-10-01T09:00`), read in the calendar's own timezone.
- All-day events use dates, with an exclusive end date.

**Notion:**
- The client is built per call from the vault, so a missing token only fails Notion tools.
- `_id()` accepts raw IDs, dashed UUIDs or full URLs.
- Uses `data_sources.query`, the search filter `page`/`data_source`, and `in_trash=True` to delete.
- `blocks_from_text` turns simple Markdown (headings, bullets, to-dos) into blocks.

**Adding a tool:**
1. Write an `@mcp.tool()` function in a `tools/*.py` module.
2. Import the module in `server.py`.
3. Add the tool name to a group in `hub.BUILTIN`.
4. Add it to `hub.CONFIRM` if it sends, changes or deletes something.
5. Add a keyword to `agent.KEYWORDS` if it's a new group.

---

## 6. Frontend (`web/`)

**Stack:** React 18, TypeScript, Vite 8 (Rolldown), `react-markdown` + `remark-gfm` (lazy-loaded chunk; raw HTML is never rendered). Fonts are bundled locally: `@fontsource` Inter / Geist Mono (Latin subsets) and Geist Pixel, so the UI loads offline.

**State:** `App.tsx` owns all app state. There is no state library.

| State | Meaning |
|---|---|
| `view` | `chat`, `connections` or `settings` |
| `settings`, `health`, `online` | Health is polled every 20 s. `online` follows browser online/offline events. |
| `conversations`, `query`, `activeId`, `messages` | Search is debounced by 200 ms |
| `live` | The in-flight run: `{conversationId, runId, text, tools[], status}` |
| `connections`, `wizard`, `toolsDlg`, `onboarding`, `palette`, `drawer`, `runOpen` | UI chrome |

**Chat flow (`send`):**
1. Create a conversation if needed.
2. Add an optimistic user message and start `live`.
3. `streamChat()` POSTs and reads the SSE body with `TextDecoderStream`, splitting on `\n\n`.
4. Event handling:
   - `delta` is buffered and flushed **once per animation frame**;
   - `confirm.request`, `tool.start` and `tool.end` upsert tool cards by `call_id`;
   - `done` updates the conversation in the sidebar.
5. In `finally`, reload the saved messages from the server, clear `live` and refresh health.

**Approve/Reject** calls `POST /runs/{id}/confirm`. **Stop** calls `/runs/{id}/cancel`.

**Components:**

| Component | Role |
|---|---|
| `Sidebar` | Conversation list, search, pin/rename/delete, usage meter |
| `Chat` | Message list, tool-call cards with Approve/Reject, composer (Enter / Shift+Enter, `/` commands, `@connection` chips that force tool groups), offline/no-key banner |
| `RunPanel` | Activity panel: per-call arguments, result, timing, status, token usage |
| `Connections` | Connection cards, `ToolsDialog` (per-tool toggles), `GoogleWizard`, `NotionWizard`, `McpWizard` (presets: Filesystem, Memory, Fetch, Git; test → pick tools → save) |
| `Settings` | Groq key, model (`gpt-oss-120b`, `gpt-oss-20b`, `qwen3.8-27b`), reasoning effort, timezone, appearance, export/wipe/data folder |
| `Onboarding` | First run: key → timezone → connect → try it |
| `Palette` | Ctrl+K command palette |
| `ui` | Toasts, Dialog, Switch, StatePill, Result, time formatting |

**Styling:** `neon-dusk.css` (design tokens and components, ported from `docs/design/donna-neon-dusk-demo.html`) plus `app.css`. Pixel grid and reduce-motion are toggled with body classes. The layout is responsive: below 1180 px the sidebar and activity panel become drawers (checked at 390 px).

**Dev vs prod:**
- Prod: FastAPI serves `web/dist` on :8765.
- Dev: Vite runs on :5173 and proxies `/api` to :8765.

---

## 7. End-to-end sequences

### 7.1 A chat turn with an approved tool

```
UI                     app.py                 agent.run_chat          llm / hub
│ POST /conv/c1/messages ─▶ Run r1 → RUNS
│◀─ event: run.start ──────────────────────── save user msg
│                                              select_tools, build messages
│◀─ event: delta … ─────────────────────────── stream_turn ────────▶ Groq
│                                              tool_calls [create_event]
│◀─ event: confirm.request ─────────────────── Future pending (≤600 s)
│ POST /runs/r1/confirm {approved:true} ──▶ run.decide → Future = True
│◀─ event: tool.start ──────────────────────── hub.call ───────────▶ Calendar API
│◀─ event: tool.end ────────────────────────── result → messages
│◀─ event: delta … ─────────────────────────── stream_turn ────────▶ Groq
│◀─ event: done {message, conversation} ────── save assistant msg + tool_events
│ GET /conv/c1/messages (refresh)              RUNS.pop(r1)
```

### 7.2 Adding an MCP server

`McpWizard` → `POST /mcp-servers/test` (start, list tools, stop) → the user picks tools → `POST /mcp-servers` → row saved to `mcp_servers`, env values saved to the vault → `hub.add` connects → tools appear as `<slug>__<tool>` in `GET /connections` and in `select_tools`.

### 7.3 Google connect

`GoogleWizard` → upload `credentials.json` → `POST /google/authorize`. The browser opens Google consent on a random localhost port, and the refresh token and email go to the vault. `POST /google/test` then shows events and emails. Later, a `RefreshError` → `needs_reauth` → the one-click Re-authorize reopens the wizard at step 3.

---

## 8. Cross-cutting concerns

| Concern | How it's handled |
|---|---|
| Network exposure | uvicorn is bound to `127.0.0.1`. The Origin middleware allows only localhost pages. |
| Destructive actions | Approval gate (`CONFIRM` plus `WRITE_WORDS`/`destructiveHint` for external tools), 10-minute timeout = reject |
| Secrets | OS keyring only, chunked. External server env vars are isolated per server. |
| Rate limits | Tool routing and truncation, `retry-after` waits shown as `status` events, daily usage meter |
| Failure isolation | Tools return error strings. External server start failures show up as `state=error` with a readable message. One missing integration doesn't break the others. |
| Concurrency | One active run per conversation (409), shared by chat and scheduled AI runs. The SQLite lock serializes writes. Blocking Google/OAuth and PostgreSQL calls run in threads. The scheduler worker processes one job at a time. |
| Offline | The UI, history and search work without internet. Sending is disabled. Health reports `unreachable`. |
| Logging | `data/donna.log`, rotating. uvicorn log level `warning`. The scheduler logs to `data/scheduler.log` (task IDs and statuses only). |

---

## 9. Testing and tooling

| What | Where |
|---|---|
| Unit tests: vault chunking and `.env` import; store conversations/search/pin/export/wipe/settings/usage/MCP servers; Notion ID and blocks; calendar time helpers; Gmail reply threading; Google client file validation | `tests/test_core.py` |
| Agent and API tests: tool routing, streamed tool call saved, confirmation gate (approve and reject), cancel, missing key, full HTTP API | `tests/test_agent_api.py` (fixtures in `conftest.py`: temp store, in-memory keyring, fake LLM) |
| Browser end-to-end (14 scenarios, scripted model) | `tests/e2e_ui.py`. Needs `DONNA_FAKE_LLM=1`, `DONNA_DATA_DIR`, `DONNA_KEYRING_SERVICE=donna-e2e`. Clean up with `tests/e2e_reset.py`. |
| Scheduled tasks (Python): time parsing and timezones, validation, reminder executor never calls Groq, unattended AI task refuses approval tools, busy conversation → retryable, schedule tools hidden in unattended runs, internal API token guard, 503 degradation. PostgreSQL CRUD/snooze/cancel/notifications with `DONNA_TEST_POSTGRES_URL` | `tests/test_tasks.py` |
| Scheduler (Node): recurrence (once/interval/cron, IST, US DST), catch-up policy, retry classification, worker skip/idempotency/retry rules with fakes | `scheduler/src/*.test.ts` (`npm test`) |
| Quality gate | `scripts/check.ps1`: pytest + `tsc -b` + `vite build` + scheduler build and tests |
| Run | `scripts/start.ps1` (creates the venv and builds the UI on first run) / `scripts/dev.ps1` (reload + Vite HMR) |

**Environment variables:**

| Variable | Effect |
|---|---|
| `DONNA_DATA_DIR` | Data folder |
| `DONNA_FAKE_LLM=1` | Scripted model |
| `DONNA_FAKE_ONLINE` | Fake Groq health state |
| `DONNA_FAKE_GOOGLE` | Fake Google state |
| `DONNA_KEYRING_SERVICE` | Keyring namespace |
| `DONNA_POSTGRES_URL` / `DONNA_INTERNAL_API_TOKEN` | Override the vault values (tests) |
| `DONNA_SCHEDULER_URL` | Scheduler address (default `http://127.0.0.1:8766`) |
| `REDIS_URL`, `SCHEDULER_PORT`, `REMINDER_GRACE_MIN`, `REMINDER_MISSED_NOTIFY_H`, `AI_CATCHUP_MIN`, `RECONCILE_SEC`, `EXECUTE_TIMEOUT_SEC` | Scheduler settings (see `.env.example`) |

---

## 10. Legacy terminal client

`client.py` spawns `server.py` as a **stdio** MCP server (with `sys.executable`), converts the tools to Groq function specs and runs a simple blocking loop on `openai/gpt-oss-120b`. It has none of the web app's routing, approval gate or persistence, and reads `GROQ_API_KEY` from `.env`.

---

## 11. Known limits and planned architecture

**Limits today:**
- `RUNS` is in memory: a server restart drops in-flight runs, and a stream can't resume after a reconnect.
- There are no per-model token buckets yet; only reactive 429 handling.
- One tool call per model step (gpt-oss has no parallel tool calls). The cap is 8 steps per message.
- Google Testing mode means re-authorizing every 7 days.
- Scheduled AI tasks can't perform approval-gated actions; they flag them for the user instead.
- Redis runs inside WSL: WSL must be available, and the first start after boot takes a few seconds longer.

**Planned** (see `docs/ROADMAP.md`; ⬜ in `progress.md`):
- planner → parallel workers → verifier → synthesizer orchestration, with roles spread across models (each has its own rate-limit bucket);
- per-model token buckets, budget warnings, fallback model;
- prompt-injection screening of email/Notion content (Groq prompt-guard);
- semantic memory (sqlite-vec + fastembed, local CPU embeddings);
- backups, SQLite schema migrations, linting, eval harness;
- scheduler follow-ups: conditional tasks, event triggers, multi-step automations, queue-level Groq throttling, Windows autostart.

The roadmap also sketches a WebSocket transport and a `contracts.py` module split. The shipped app uses SSE and the flat `donna/` modules described above.
