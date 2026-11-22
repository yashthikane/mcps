# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Donna": a local AI assistant with a web UI. A FastAPI backend (`donna/`) runs a Groq tool-calling loop against MCP tools: the built-in FastMCP server in `tools/` plus user-added MCP servers. The React UI (`web/`) streams replies over Server-Sent Events. The terminal client `client.py` still works against the same tools.

Scheduled tasks (reminders, AI tasks) use a separate Node + TypeScript service in `scheduler/` (BullMQ on Redis). Their state lives in PostgreSQL. Design: `docs/donna_architecture_conversion_bullmq.md`, and `docs/ARCHITECTURE.md` §4.

**Hard constraints** (`docs/ROADMAP.md`):
- free and self-hosted only: every service is bound to 127.0.0.1, no paid or managed cloud services, no CI/CD. It runs on the owner's Windows laptop or on the owner's own Oracle Always Free Ubuntu VM (ARM, `scripts/linux/setup.sh`, systemd). The VM is reached only privately over Tailscale (`tailscale serve` → 127.0.0.1:8765, allowed via `DONNA_ALLOWED_HOSTS`); never expose a port publicly, because the UI has no login;
- Groq free tier only: 8K tokens/min, 30 req/min, 1K req/day per model;
- Windows laptop with 8 GB RAM (the VM has 12 GB, but keep the same budget). There are four processes (FastAPI, the scheduler, Redis in WSL Ubuntu, and PostgreSQL 18 as a Windows service), plus OpenWA (Node + Chromium, ~300–500 MB) when WhatsApp is set up. Keep each one small: worker concurrency 1, small DB pools, no Docker.

Quality checks run locally via `scripts/check.ps1`. Server deployment: README "Self-hosting on a Linux server".

## Commands

```powershell
scripts\setup-scheduler.ps1          # once: Redis in WSL, PostgreSQL db/role (URL → vault), scheduler npm build, migrations
scripts\start.ps1                    # venv + UI build on first run, scheduler (hidden, logs data\scheduler.log), then python -m donna → http://127.0.0.1:8765
scripts\dev.ps1                      # uvicorn --reload on :8765 + Vite dev server on :5173 (proxies /api) + scheduler (tsx watch)
scripts\scheduler.ps1                # start Redis if needed + the scheduler on :8766 (secrets from the vault, process-scoped)
scripts\setup-whatsapp.ps1           # optional: python -m donna openwa-setup (clone + build OpenWA into external\openwa, key → vault)
scripts\openwa.ps1                   # optional: python -m donna openwa-start (OpenWA on 127.0.0.1:2785, log data\openwa.log)
scripts\check.ps1                    # pytest + tsc + vite build + scheduler build/tests
scripts/linux/setup.sh               # on the Ubuntu VM: install/update everything, systemd services, tailscale serve
venv\Scripts\python -m donna migrate # apply migrations/postgres/*.sql (also runs at FastAPI startup)
cd scheduler; npm test               # scheduler unit tests (node:test via tsx)
venv\Scripts\python -m pytest -q     # backend tests (tests/test_*.py)
venv\Scripts\python -m pytest tests/test_agent_api.py::test_confirmation_gate -q   # one test
cd web; npx tsc -b; npx vite build   # typecheck / build the UI (Vite 8 = Rolldown: no object-form manualChunks)
python client.py                     # terminal chat (spawns server.py with sys.executable)
```

UI end-to-end test: see README "Testing the UI end to end". It needs these env vars on the server:
- `DONNA_FAKE_LLM=1` (scripted model in `llm._fake_turn`);
- `DONNA_DATA_DIR` (a throwaway data folder);
- `DONNA_KEYRING_SERVICE=donna-e2e`, so the real Credential Manager entries are never touched.

Clean up with `tests/e2e_reset.py`.

Keep `requirements.txt` UTF-8. PowerShell's `pip freeze >` writes UTF-16.

## Architecture

- `donna/app.py`: all HTTP routes under `/api/v1`. It also serves `web/dist`.
  - The Origin middleware only allows localhost pages.
  - Chat is `POST /conversations/{id}/messages`, which returns an SSE stream. Approvals and Stop are `POST /runs/{id}/confirm|cancel`.
  - The lifespan imports `server.py` (registering the tools) and starts `MCPHub`.
- `donna/agent.py`: `run_chat` is an async generator yielding UI events (`run.start`, `delta`, `status`, `tool.start`, `confirm.request`, `tool.end`, `done`, `error`).
  - It streams a model turn, then runs each tool call through `_run_tool`. Tools in `hub.CONFIRM`, or external tools matching `WRITE_WORDS`, wait on an `asyncio.Future` resolved by `/confirm`.
  - **Permission modes** (`agent.MODES`; `Run(mode=…)`): `plan` hides every tool with `Tool.writes` and adds a plan-only prompt, `manual` is the approval gate above, and `auto` runs gated tools without asking (`approved: "auto"`).
    - Chat sends `mode` with each message. The composer switch cycles with Shift+Tab and is saved as the `permission_mode` setting.
    - A scheduled AI task uses `payload.permission_mode`; `manual` refuses gated tools because nobody can approve them.
    - `agent.needs_approval` also gates `schedule_task(permission_mode="auto")`, so granting an unattended task Auto permissions is itself approved once.
  - It saves the assistant message with `tool_events`. Earlier tool results are summarized into history, so follow-ups can reuse IDs.
  - `select_tools` sends only keyword-matched tool groups (with a fallback to all tools) to stay under 8K tokens/min.
- `donna/hub.py`: `MCPHub` keeps one in-memory fastmcp `Client` to `mcp_instance.mcp`, plus one per enabled external server.
  - External tools are exposed as `<slug>__<tool>`.
  - `BUILTIN` maps connection IDs to tool names. Add new built-in tools there, to `CONFIRM` if they send, change or delete, and to `WRITES` if they change anything (plan mode hides them).
  - On Windows, `resolve_command` maps `npx` → `npx.cmd` (the bare name fails with WinError 193).
  - stdio servers only get a minimal environment plus their configured env vars (the values live in the vault).
- `donna/llm.py`: the model, chosen by the `llm_provider` setting; callers use `llm.turn(settings, …)` and `llm.problem(settings)`.
  - `claude_code` runs the official `claude -p` CLI, signed in with the owner's own subscription; Donna never stores that sign-in.
  - That call uses `--tools ""`, a `--json-schema` step of `{reply, tool_call}` (one tool per step, which Donna runs with her normal permissions), and a neutral working directory so no project CLAUDE.md loads.
  - Groq streaming is the default provider; the points below are about Groq.
  - Tool-call deltas are accumulated by index.
  - 429s are retried using `retry-after`.
  - `tool_use_failed` is retried once.
  - `reasoning_effort` applies to gpt-oss only. gpt-oss has no parallel tool calls.
- `donna/store.py`: stdlib sqlite3 in WAL mode (`data/donna.db`). It holds conversations, messages, `messages_fts` (FTS5), mcp_servers, and settings as JSON values. Settings keys must exist in `DEFAULT_SETTINGS`.
- `donna/vault.py`: keyring (Windows Credential Manager, service `donna`).
  - Values over 500 characters are chunked, because WinVault rejects long passwords.
  - On startup, `GROQ_API_KEY` and `INTERNAL_INTERGRATION_TOKEN` are imported from `.env`.
- `tools/google_auth.py`: one Google sign-in shared by Gmail and Calendar.
  - Scopes are gmail.modify + gmail.send + calendar.
  - Only `{refresh_token, scopes, email}` and the OAuth client are stored.
  - `authorize()` runs `InstalledAppFlow.run_local_server` (blocking, so it's called from a thread).
  - `RefreshError` → `needs_reauth` (tokens in Testing mode expire after 7 days).
- **Scheduling** splits work across processes. The scheduler decides *when* a task runs; Donna decides *how*.
  - `scheduler/src/` (Node) owns recurrence math (`recurrence.ts`: once / interval `30m` / cron in the task's timezone), `next_run_at`, and one delayed BullMQ job per task (`jobId task-<id>-<epochMs>`).
    - The worker (`worker.ts`, concurrency 1) re-reads the task from PostgreSQL. It skips jobs that are cancelled, paused or stale (`next_run_at ≠ scheduledFor`).
    - It claims the occurrence in `task_executions`, where `UNIQUE(task_id, scheduled_for)` is the idempotency key, and applies the catch-up policy (`policy.ts`).
    - It then calls FastAPI and advances recurring tasks.
    - `reconcile()` rebuilds Redis from PostgreSQL at startup, when Redis reconnects, and every 5 minutes.
    - The worker never touches SQLite, MCP or Groq.
  - `donna/tasks.py` (`TaskService`) does task CRUD in PostgreSQL. Every change ends with the scheduler's `sync`. Schedules are validated through the scheduler's `/preview`, so recurrence isn't duplicated in Python.
    - `execute()` sends reminders through `notify.py` (Windows toast + in-app `notifications`), with no Groq call.
    - AI tasks run `agent.run_chat` with `Run(unattended=True)`. Confirm tools are refused (the execution becomes `approval_required`), and the `schedule` tool group is hidden.
  - `POST /api/v1/internal/scheduled-tasks/{id}/execute` requires `Bearer <internal_api_token>`. The token is generated into the vault. The scheduler gets it (and `postgres_url`) as process env from `scripts/scheduler.ps1`.
  - `donna/pg.py` uses psycopg's **sync** pool through `asyncio.to_thread`: psycopg async can't run on Windows' Proactor loop, which uvicorn and stdio MCP need. Donna starts without PostgreSQL; only the scheduling routes return 503.
  - Schema changes are new files in `migrations/postgres/` (tracked in `schema_migrations`). Never edit an applied migration.
- **Messaging channels** (WhatsApp via OpenWA, Telegram; `docs/ARCHITECTURE.md` §12). There is one agent runtime: `runtime.run_agent_turn` wraps `agent.run_chat` for headless callers (channels, scheduled AI tasks, approval follow-ups).
  - A turn's `permissions.TurnContext` says who it's for. `None` is the owner on the web. `audience="contact"` only sees tools whose capability is on or approval for that contact; `permissions.decide` runs when the tool list is built and again on each call.
    - `hub.CONFIRM` tools are always approval for contacts. Contacts may only use `schedule_task` for manual-mode reminders.
    - Gated calls in channel turns become `approvals` rows (`on_gated="approval"`), not live futures.
  - `channels/manager.py` (`ChannelManager`) runs the pipeline: dedupe (`channel_events` UNIQUE), kill switch and pauses, `contacts.resolve` (unknown sender → disabled, discovered contact), mode (disabled / suggest / auto / human_first), working hours and rate limits. All of this happens before any model call.
    - Owner identities (`contacts.is_owner`) get owner turns; `approve CODE` / `reject CODE` are parsed in code.
    - Approvals are single use (conditional UPDATE), bound to `payload_hash`, expire, and re-check permissions before running.
  - `donna/openwa.py` owns the OpenWA process. The WhatsApp Connect button (`POST /channels/whatsapp/setup`, polled with GET) runs `openwa.Setup`: check Node/git → install the pinned tag (first time) → start it detached on loopback (key as process env, `scripts/openwa-localhost.cjs` preload) → `ChannelManager.connect` → QR. At startup Donna restarts it if WhatsApp is connected. Nothing else starts OpenWA.
  - Adapters (`channels/whatsapp.py`, `channels/telegram.py`) only translate. The WhatsApp webhook `POST /api/v1/webhooks/whatsapp` is HMAC-signed (`X-OpenWA-Signature`), with a 10-minute replay window and a 2 MB cap. Telegram long-polls.
  - **Identity** (`identity.py`). With contacts, Donna is the owner's AI assistant and never the owner.
    - `contact_prompt` is built from each contact's communication profile (relationship defaults overridden by explicit fields). Background and notes are labelled "for understanding only".
    - The introduction is enforced in code (`with_introduction` when `needs_introduction`), `clean()` strips "(User)"/"raha/rahi" placeholders, and `send_message` relays as "<owner> cha message: …" (`identity.relay`).
  - **Trigger word** (`require_trigger`, `trigger_word`, default `@donna`, on by default). The linked WhatsApp is the owner's own number, so contacts chat with the owner as usual and reach Donna only with the trigger.
    - Messages without it are stored and left for the owner, with no model call.
    - Each chat gets one hint, `identity.trigger_hint`, recorded in `channel_conversations.trigger_hint_at` (migration 003).
    - The trigger is stripped before Donna reads the message.
  - **Requests** (`contact_requests.py`): contact turns call `record_request` / `update_request`. Scope comes from the turn; types are limited by `contacts.allowed_requests`.
    - Callbacks, messages and urgent requests go straight to `waiting_for_owner` and notify the owner; their tool result gives the model a safe confirmation phrase.
    - The owner manages requests in Inbox → Requests (`PATCH /requests/{id}`, optional `inform_about_request`).
  - Runtime tools (`runtime_tools.py`: remember, list_memories, forget_memory, list_contacts, read_chat, send_message) run in-process because they need the turn. Memory scope comes from the turn, never from tool arguments.
  - SQLite migrations: `migrations/sqlite/NNN_*.sql`, applied by `Store._migrate` (PRAGMA user_version). Never edit an applied one. `conversations.source` keeps channel chats out of the web sidebar.
  - Automations reuse the scheduler: the `owner_chat` notification channel, `payload.deliver_contact_id` (needs the contact's `initiate` capability) and the `automations_enabled` switch.
- `web/src/App.tsx` holds all app state; the components live in `web/src/components/`. `Tasks.tsx` is the Tasks page, `Inbox.tsx` the channel conversations / approvals / audit log, `Contacts.tsx` contacts and their permissions, and `Channels.tsx` the WhatsApp/Telegram cards on Connections. The app polls `/notifications` every 20 s and shows each one as a toast.
  - Layout (approved mockup `docs/design/donna-ui-redesign.html`): `Sidebar.tsx` holds navigation (the six views), recent chats and the status/usage card; the main panel has a `TopBar` (in `App.tsx`) and the views. There is no activity panel: tool arguments, results and timings live in the expandable tool cards in `Chat.tsx`.
  - `components/ambient/` is the wireframe + ASCII mountain on a 2D canvas (`terrain.ts`, tuned only in `ambient.config.ts`). It animates only on the idle welcome screen (paused on composer focus, in conversations, hidden tabs and reduced motion) and never depends on run events.
  - Activity uses the `thinking-orbs` package. `components/orbState.ts` maps real run data to orb states (Plan → weaving, read tools → searching, other tools → working, streamed text → composing, approval → breathing). `listening`/`shaping` stay unused: there is no voice input or structured-output event. `DonnaOrb` (icons.tsx) is the logo only.
  - Icons: `Icon` in `icons.tsx` maps short names to Hugeicons (`@hugeicons/react` + `@hugeicons/core-free-icons`, MIT). Add new names to its `ICONS` map.
  - Styling: `neon-dusk.css` (primitives and pages, tokens remapped to the redesign palette) + `app.css` + `donna.css` (shell, sidebar, chat, composer; the `--donna-*` tokens).
  - Fonts are local: @fontsource Geist Sans, Inter and Geist Mono (Latin subsets).

### Conventions for tools

- Tools return a plain `str`. Errors are returned as strings starting with "Error", which `hub.call` treats as failures. Don't raise.
- The docstring and type hints become the schema the LLM sees. Document formats and IDs in the docstring.
- Google tools are `async` and run the blocking googleapiclient calls in `asyncio.to_thread`. They call `tools.google_auth.service()`.
- Calendar times are local times without an offset, interpreted in the calendar's own timezone (`settings.get timezone`). All-day events use dates, with an exclusive end date.
- Notion uses notion-client 3.x on API 2025-09-03:
  - `data_sources.query` (not `databases.query`);
  - search filter `page` / `data_source`;
  - `in_trash=True` to delete.
  
  The client is built per call from the vault, so a missing token only fails the Notion tools.
- A new tool module must be imported in `server.py`, and its tool names added to `hub.BUILTIN`.

## Environment

Keys live in Windows Credential Manager (on Linux: the owner-only file `~/.config/donna/secrets.json`, `vault.FileKeyring`, override with `DONNA_SECRETS_FILE`), including `postgres_url`, `internal_api_token`, `openwa_api_key`, `openwa_webhook_secret` and `telegram_bot_token`. `DONNA_POSTGRES_URL` / `DONNA_INTERNAL_API_TOKEN` override them in tests. `.env` (gitignored; template in `.env.example`) is only read to import `GROQ_API_KEY` / `INTERNAL_INTERGRATION_TOKEN` on first start. Runtime data (`data/`: DB, logs) is gitignored.

PostgreSQL tests in `tests/test_tasks.py` run only when `DONNA_TEST_POSTGRES_URL` is set. They create and drop a throwaway schema.
