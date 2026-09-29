# Donna — Local, Free Implementation Roadmap

## Context

Donna (repo `mcps`) is a terminal prototype: `client.py` uses the Groq chat API, and `server.py` is a FastMCP stdio server with 17 tools (Notion ×6, Gmail ×6, Calendar ×2, weather, square, jokes). The goal is a full conversational system with a rich web UI, easy MCP connection, persistent memory, and parallel agents for decomposition, verification, and synthesis.

**User constraints (binding):**
- **Completely free.** No paid services, no cloud or hosting, no CI/CD.
- **Runs on this laptop.** i5-1135G7, 7.7 GB RAM, integrated GPU, Windows 11. Node, Python 3.12 and Docker are installed (Docker is not needed).
- **LLM: Groq API only** (free tier). Chat needs internet; UI and history load offline.
- **Frontend: React + Vite.** It must be rich and polished, with exact spacing and alignment, simple guided MCP connection, and loading optimized to the maximum.

**Facts found during planning that shape the design:**
- **The current model is dead.** `llama-3.3-70b-versatile` (used in `client.py`) was shut down by Groq on **2026-08-16**, so the prototype is currently broken. The replacements are `openai/gpt-oss-120b` and `openai/gpt-oss-20b`; `qwen/qwen3.8-27b` is another option. `llama-3.1-8b-instant` is also shut down.
- **Free-tier limits (per model, per org):** 30 RPM · 1K RPD · **8K TPM** · 200K TPD. Cached tokens don't count. Consequences:
  - roles are spread across models (each has its own bucket);
  - workers get tool subsets, not all 17+ tool schemas;
  - stable prompt prefixes help caching;
  - a client-side token bucket per model plus `retry-after` handling.
- Groq has no embeddings API, so semantic memory uses **fastembed** (ONNX, CPU, free, ~130 MB model), which runs locally.
- The free Groq classifier `meta-llama/llama-prompt-guard-2-86m` (30 RPM, 15K TPM) screens untrusted tool content for prompt injection.
- Google OAuth for a personal project stays in **Testing** mode (free, no verification). Refresh tokens then expire after **7 days**, so the UI needs a one-click re-authorize.

---

## Architecture (single machine, one command to start)

```
Browser (React + Vite SPA, served by FastAPI in prod mode)
   │  REST (OpenAPI → generated TS types)     WebSocket /ws (streaming run events)
   ▼
FastAPI app on 127.0.0.1:8765 — modular monolith (one Python process)
 ├─ api/           REST routes + WS hub, origin check, request limits
 ├─ sessions/      conversations, messages, settings
 ├─ context/       token-budgeted context builder, rolling summaries, memory search
 ├─ nlp/           router (direct | single_tool | multi_agent), date/entity resolution
 ├─ orchestrator/  planner → parallel workers → verifier → synthesizer; confirmation gate
 ├─ llm/           Groq gateway: per-model token buckets, 429 retry-after, streaming
 └─ mcp_hub/       manages MCP connections: built-in Donna server (in-process) +
                   user-added servers (stdio command or HTTP URL); tool catalog & toggles
Storage: SQLite `donna.db` (WAL) + sqlite-vec · Secrets: Windows Credential Manager via `keyring`
Cache: in-process TTL caches (cachetools) · Embeddings: fastembed bge-small-en-v1.5 (384-d)
```

Module boundaries are the "services". Each exposes a typed Python interface defined in `donna/contracts.py` (Pydantic), and modules never import each other's internals. That keeps the option to split them into processes later without paying for it now.

**Communication:**
- Direct async calls between modules.
- An in-process event bus (`asyncio` pub/sub) carries run events to the WS hub.
- Cross-cutting background jobs (memory indexing, summaries) run from an `asyncio` task queue.
- MCP runs over the in-memory transport for the built-in server (the `fastmcp` Client connects straight to `mcp_instance.mcp`, so there's no subprocess), and over stdio or HTTP for external servers.

### Contracts

**REST** (all under `/api/v1`):
- `GET/POST /conversations`, `PATCH/DELETE /conversations/{id}`, `GET /conversations/{id}/messages?cursor=`, `GET /search?q=`.
- `GET /connections` (built-in integrations + custom MCP servers, with status and tools).
- `POST /connections/google/credentials` (upload `credentials.json`), `POST /connections/google/authorize` → returns an auth URL, `GET /oauth/google/callback`.
- `POST /connections/notion` {token} → returns pages visible.
- `POST /mcp-servers` {name, transport: stdio|http, command, args, env, url}, `POST /mcp-servers/{id}/test` → tools[], `PATCH /mcp-servers/{id}` (enable/disable, per-tool toggles), `DELETE /mcp-servers/{id}`.
- `GET/PUT /settings` (Groq key via keyring, model roles, timezone, theme), `POST /settings/groq/test`.
- `GET /health` → {groq_reachable, internet, connections}.

**WebSocket events.** Server → client:
- `message.delta {text}`
- `run.plan {tasks:[{id,title,deps,tools}]}`
- `task.status {id, state: queued|running|verifying|done|failed|waiting_rate_limit, summary}`
- `tool.call {task_id, server, name, args_redacted, ms, ok}`
- `confirm.request {task_id, action, preview}`
- `rate_limit {model, wait_ms}`
- `message.complete {message_id, usage, latency_ms}`
- `error {code, message, retryable}`

Client → server: `user.message {conversation_id, text, client_msg_id}`, `confirm.response {task_id, approved}`, `run.cancel {run_id}`.

### Data model (SQLite via SQLAlchemy 2 + Alembic)
- `conversations` (id, title, summary, summary_upto_msg_id, pinned, created_at, updated_at)
- `messages` (id, conversation_id, role, content json, run_id, tokens_in/out, created_at)
- `agent_runs` (id, conversation_id, route, status, usage json, latency_ms, started_at, ended_at)
- `agent_tasks` (id, run_id, kind plan|worker|verify|synthesize, deps json, input, output, state, attempts, started_at, ended_at)
- `tool_calls` (id, task_id, server, tool, args_redacted, ok, latency_ms, requires_confirmation, confirmed)
- `mcp_servers` (id, name, transport, command, args, url, enabled, disabled_tools json). The env and secrets live in keyring.
- `memories` (id, source_message_id, text, embedding in a sqlite-vec `vec0` virtual table)
- `audit_log` (id, action, target, created_at): sends, deletes, connection changes.
- FTS5 index over `messages.content` for instant conversation search.

Google and Notion tokens move from `token.json` / `gmail_token.json` / `.env` into **keyring**, and are migrated automatically on first run.

**Caching:**
- In-process TTL caches: weather 10 min, geocode 24 h, Notion `list_pages` 60 s, tool catalog until a connection changes, conversation context until a new message.
- Groq prompt caching through stable prefixes.
- No reply caching.

---

## Phases

Effort is in **solo full-time days**. The order is strict; a phase starts only when the previous exit gate passes, unless a noted overlap applies.

### Phase 0: Revive and stabilize the prototype · 3 days
Deliverables
- Switch `client.py` to `openai/gpt-oss-120b`, and add `groq` to requirements, re-saved as UTF-8.
- Make the missing Notion key a lazy per-tool error. Guard `result.content` parsing and Groq errors in `client.py`. Fix `get_events` → `event.get("summary", "(no title)")`. Take the timezone from settings instead of the hardcoded `Asia/Kolkata`.
- Add `pyproject.toml`, ruff, and pytest with mocked Google/Notion/httpx unit tests for all 17 tools.
- **Golden set v0:** 60 real prompts with expected tool calls, in `evals/golden.yaml`.

**Exit gate:** a clean venv installs, `python client.py` chats again, tests pass, and the server starts with no Notion key.

### Phase 1: Local foundation (replaces cloud, CI/CD, containers) · 3 days
Deliverables
- Repo layout: `backend/donna/…` (modules above), `backend/tests/`, `web/` (Vite React TS), `evals/`, `scripts/`, `docs/`.
- **One-command run:**
  - `scripts/dev.ps1` starts uvicorn with reload and Vite (proxy `/api` and `/ws`);
  - `scripts/start.ps1` builds `web/` once and serves the static build from FastAPI, so only one process and one port are used day to day.
- **Local quality gate (instead of CI):** `scripts/check.ps1` runs ruff, mypy, pytest, `tsc --noEmit`, eslint, vitest, and the vite build size check. A `pre-commit` hook runs the fast subset on commit. All free and local.
- Structured logs to `logs/donna.log` (rotating). A per-run trace id shows in the UI run panel.

**Exit gate:** after `git clone` + `scripts/start.ps1` on a clean checkout, the app shell shows at `http://127.0.0.1:8765`. `check.ps1` is green.

### Phase 2: Backend modules · 12 days · depends on Phase 0 and Phase 1
Deliverables
- Contracts in `donna/contracts.py`. OpenAPI is exported to `web/src/api/schema.d.ts` via `openapi-typescript` (a script, run by `check.ps1`).
- `api`: REST routes, WS hub with resume (`last_event_id`, 5-min replay buffer), localhost-only binding, Origin check, 16 KB message limit.
- `sessions`: CRUD, cursor pagination, FTS5 search.
- `context`: token-budget builder that takes the system prompt, a rolling summary of old turns (gpt-oss-20b), the last N turns, and the top-k memories. The default budget keeps each call **≤ 3K tokens** to fit 8K TPM.
- `nlp`: router on `openai/gpt-oss-20b` with a heuristic pre-check (greetings/math → `direct`, one obvious intent → `single_tool`), and datetime resolution in the user's timezone.
- `mcp_hub`:
  - the built-in Donna tools connect in-process (reusing `tools/*.py` and `mcp_instance.py` as-is);
  - the Google service helpers read tokens from keyring;
  - external MCP servers can be added over stdio or HTTP, with a tool catalog, per-tool enable toggles, a health check, and auto-restart for stdio servers;
  - `send_email`, `delete_email`, `delete_page`, and the new calendar delete are tagged `requires_confirmation`.
- New tools to fill gaps:
  - Calendar: list range, update, delete, with timezone from settings.
  - Gmail: reply, mark read/unread, create draft.
  - Notion: create page with content blocks, query database, and `read_page_content` returning plain text instead of raw dicts.

**Exit gate:**
- Through the REST/WS API (tested with pytest + httpx), you can create a conversation and get a streamed single-tool answer.
- Adding an external MCP server (e.g. `npx @modelcontextprotocol/server-filesystem`) lists its tools.
- API overhead is p95 ≤ 20 ms.

### Phase 3: Data layer · 6 days · depends on Phase 1 (schema v1 needed by day 3 of Phase 2, so build it first)
Deliverables
- Alembic migrations, SQLite WAL mode, a nightly backup copy to `data/backups/` (last 7 kept).
- sqlite-vec plus a fastembed indexer. It runs as a background task after each message and is idempotent. The model downloads once and runs offline after that.
- One-time migration of `token.json`, `gmail_token.json`, and `.env` Notion/Groq keys into keyring. Old files are renamed `*.migrated`.
- TTL cache helpers with an invalidation hook on connection changes.
- "Delete conversation" cascades to tasks, tool calls, and memories. "Wipe all data" is available in Settings.

**Exit gate:**
- Migrations go up and down cleanly.
- Memory search is p95 ≤ 50 ms at 50K memories.
- FTS search is ≤ 30 ms at 10K messages.
- No secrets are left in plain files (automated scan test).

### Phase 4: AI integration and parallel agents · 14 days · depends on Phase 2 and Phase 3
Deliverables
- **`llm` gateway:**
  - streaming Groq client;
  - model roles in settings: planner/synthesizer `openai/gpt-oss-120b`, router/worker/verifier `openai/gpt-oss-20b`, fallback `qwen/qwen3.8-27b`;
  - a per-model token bucket that tracks RPM and TPM from response headers, queues calls, and emits `rate_limit` events;
  - a daily-budget meter shown in the UI.
- **Prompt framework:** versioned templates in `backend/donna/prompts/*.yaml` (id, version, Jinja). Untrusted content (emails, Notion, external MCP output) is always wrapped as labeled data. The prompt-guard model screens untrusted content before it reaches a tool-capable agent.
- **Orchestrator:**
  1. **Fast path:** `direct` and `single_tool` routes use one streaming tool loop. This covers most requests.
  2. **Planner:** turns the request into a JSON task DAG with ≤ 5 tasks. Each task has a tool allowlist and a success criterion.
  3. **Parallel workers:** `asyncio.TaskGroup`, 20 s per-task timeout. Independent tasks run concurrently, and dependents start when their inputs resolve. Each worker sees only its allowed tools, which saves tokens. Concurrency is limited by the model token buckets, not a fixed number.
  4. **Verifier:** deterministic checks first (tool ok, IDs and times present in the tool output), then an LLM check for semantic criteria. It retries once with feedback.
  5. **Confirmation gate:** pauses the task and emits `confirm.request`. Other tasks continue.
  6. **Synthesizer:** streams the answer and names failed or skipped tasks plainly.
  - Supports cancel, and is idempotent on `client_msg_id`.
- **Local eval harness:** `python -m evals.run` replays the golden set (grown to 150, with 40 multi-agent cases). It scores deterministic checks and an LLM-judge rubric on gpt-oss-120b. Results go to `evals/results/*.json`, compared with the previous run. It's run manually before merging prompt or orchestrator changes, since there is no CI.

**Exit gate:**
- Eval pass rate is ≥ 90% overall and ≥ 80% on multi-agent cases.
- A 3-task independent run's wall-clock is ≤ 1.4× the slowest task, which proves the tasks run in parallel.
- A simulated 429 recovers without a user-visible error.
- There are 0 destructive calls without confirmation in a 30-case prompt-injection pack.

### Phase 5: Rich frontend (React + Vite) · 20 days · UI shell and design system can start after Phase 1, against a mock WS server generated from the contracts; real wiring needs Phase 2, and the run panel needs Phase 4
**Stack (all free):**
- React 18 + TypeScript + Vite, Tailwind CSS, and shadcn/ui components (Radix primitives, copied into the repo, tree-shaken).
- TanStack Query (server state) and Zustand (chat/run stream state).
- react-virtuoso (virtualized messages), react-markdown + rehype-sanitize.
- Code highlighting lazy-loaded (shiki, fetched only when a code block appears).
- lucide-react icons (per-icon imports), @fontsource self-hosted fonts.

**Design system first (days 1–3). This is the answer to "correct spacing and alignment":**
- **Tokens:** 4 px base spacing scale (4/8/12/16/20/24/32/40/48/64), one type scale (12/13/14/16/18/22/28), line-height tokens, radius tokens (6/10/16), elevation tokens (2 levels), and color tokens for light and dark themes. Only tokens are allowed: an eslint rule plus a Tailwind config that removes arbitrary values.
- **Layout grid:**
  - app shell = sidebar 280 px, chat column capped at 760 px text width, and a run panel of 360 px (collapsible);
  - breakpoints 640/1024/1280;
  - below 1024 the run panel becomes a drawer, and below 640 the sidebar becomes a sheet.
- **Component inventory:** Button, IconButton, Input, Textarea (autosize), Card, Badge/StatusPill, Tabs, Dialog, Sheet, Tooltip, Toast, Skeleton, Stepper, EmptyState, CodeBlock, ToolCallRow, TaskNode.
- A dev-only `/ui` gallery route renders every component in every state and both themes. It's the visual reference for alignment reviews.

**Screens:**
1. **First-run onboarding** (a 4-step stepper, skippable, resumable):
   1. paste the Groq API key → "Test" shows ✓ and the model list;
   2. set timezone (auto-detected);
   3. connect integrations;
   4. try a sample prompt.
2. **Chat:**
   - conversation sidebar with search (FTS), pin, rename, delete, grouped by Today / Last 7 days / Older;
   - message thread with streaming;
   - composer with Enter to send, Shift+Enter for a newline, Stop, and slash-commands (`/tools`, `/new`);
   - a tool-mention chip picker for forcing a specific tool;
   - suggested prompts that change with the connected integrations.
3. **Run panel:**
   - the plan as a live DAG: nodes with state pills, and the elapsed time on each;
   - each task expands to show its tool calls (args redacted, duration, ok/fail), the verifier verdict, and a "waiting for rate limit N s" state;
   - token usage and today's Groq budget meter.
4. **Confirmation cards:** shown inline in the thread, with an email preview (to, subject, body), an event diff, or the page being deleted, plus Approve and Reject buttons.
5. **Connections** (the "connect MCP easily" hub). Every connection is a card with its status (Connected / Needs re-auth / Error / Off), tool count, and Test and Disconnect buttons. Adding one opens a **step-by-step wizard with the exact instructions inline**:
   - **Google (Gmail + Calendar):**
     1. open Google Cloud Console (link), create a project, and enable the Gmail and Calendar APIs (a checklist);
     2. create an OAuth client of type "Desktop app" and download JSON;
     3. drag-drop `credentials.json`, which is validated immediately;
     4. click Authorize → the browser consent screen opens → the callback returns and the card turns green;
     5. a test step lists the next 3 events and the last 3 email subjects.
     - When the 7-day testing-mode expiry hits, the card shows "Re-authorize" (one click).
   - **Notion:**
     1. create an integration (link);
     2. paste the secret;
     3. share pages via "Add connections" (screenshot hint);
     4. "Test" shows the pages found.
   - **Custom MCP server:**
     - choose Command (stdio) or URL (HTTP);
     - fields for command, args, env (masked), or URL;
     - presets for popular free servers (filesystem, fetch, git, sqlite) prefill the fields;
     - "Test" lists the discovered tools, with per-tool toggles, then Save.
   - Built-in utilities (weather, jokes, square) have on/off toggles.
6. **Settings:** Groq key, model roles, timezone, theme (system/light/dark), data export and wipe, and log viewer links.
- **Status handling:** an offline or Groq-unreachable banner (history stays browsable), and a per-connection error with the fix action inline.

**Performance budget (enforced by `check.ps1`):**
- Initial JS ≤ **120 KB gzip**. Routes are code-split (Chat eager; Connections, Settings, and Onboarding lazy). Markdown, highlighting, and the DAG renderer are lazy.
- Build output is precompressed (brotli + gzip). FastAPI serves hashed assets with `Cache-Control: immutable, max-age=1y`, and `index.html` with no-cache.
- Fonts: 2 families, latin subset, woff2, preloaded, `font-display: swap`, with size-adjusted fallbacks so CLS stays 0.
- Streaming: deltas are batched per `requestAnimationFrame`, message rows are memoized, and only the streaming row re-renders.
- Virtualized lists handle 10K messages and 1K conversations with no jank.
- Conversation data is prefetched on sidebar hover (TanStack Query). The message list comes from cache on revisit.
- Targets:
  - cold load LCP ≤ 800 ms and warm ≤ 300 ms on this laptop;
  - Lighthouse Performance, Accessibility and Best Practices ≥ 95;
  - CLS ≤ 0.02;
  - INP ≤ 100 ms;
  - switching conversation ≤ 100 ms;
  - the first streamed token painted ≤ 50 ms after the WS frame arrives.

**Exit gate:**
- Playwright visual snapshots of every screen at 390/1024/1440 px in both themes are approved and stored as baselines, so later layout drift fails `check.ps1`.
- Lighthouse (`@lhci/cli`, local) meets the targets.
- axe scan shows 0 serious issues.
- Keyboard-only use completes onboarding, chat, a confirmation, and adding an MCP server.
- A new user connects Google in ≤ 5 min and Notion in ≤ 2 min by following only the wizard.

### Phase 6: Integration, QA, performance, security · 8 days (tests are also written inside each phase) · depends on Phases 2–5
Deliverables
- **Tests:**
  - pytest (≥ 80% backend coverage);
  - Vitest + React Testing Library for components and stores;
  - Playwright E2E against the real backend with a **recorded Groq fixture server** (replays captured responses), so E2E runs are free, offline, and deterministic;
  - a small live smoke suite against real Groq, run by hand.
- **Performance:**
  - a Locust script with 3 concurrent conversations × 20 min to check WS stability, memory growth (< 300 MB backend RSS), and rate-limit queuing;
  - Lighthouse runs;
  - a latency breakdown per run in the run panel.
- **Security:**
  - bind to 127.0.0.1 only, with Origin and Host checks on REST and WS, and a CSRF token on state-changing REST;
  - input sanitization: size caps, NFKC normalization, control-char strip, Pydantic validation;
  - output filtering: rehype-sanitize (no raw HTML), links show the full domain, secret and PII redaction in logs and `tool_calls.args_redacted`;
  - external MCP servers run only after an explicit user add, with their command shown;
  - the prompt-guard screen plus the confirmation gate;
  - `pip-audit` and `npm audit` in `check.ps1`.
- `docs/USER_GUIDE.md` covers start, connect, troubleshoot (re-auth, rate limits, offline).

**Exit gate:** everything under *Final acceptance* passes.

---

## Service levels (on this laptop, Groq free tier, within rate limits)

| Metric | Target |
|---|---|
| App shell load, cold / warm | ≤ 800 ms / ≤ 300 ms |
| First streamed token, fast path | p95 ≤ 1.5 s |
| Plan shown for multi-agent | p95 ≤ 2.5 s |
| Full answer, fast path | p95 ≤ 6 s |
| Full answer, multi-agent (≤ 4 tasks, excl. confirm wait and rate-limit waits, which are shown in UI) | p95 ≤ 15 s |
| Conversation switch | ≤ 100 ms |
| Memory search / message search | p95 ≤ 50 ms / ≤ 30 ms |
| Backend memory (RSS) | ≤ 300 MB |
| Failed runs (unrecovered) | < 2% |

## Final acceptance (project exit criterion)

All phase gates pass. Then one validated integration session through the chat UI must show all of the following:
1. **Setup:** from a fresh clone, `scripts/start.ps1` → onboarding → Groq key → Google and Notion connected via the wizards → one external MCP server (filesystem) added and tested.
2. **Scripted acceptance:** a 12-scenario script runs in the UI. It includes the parallel case *"Summarize my unread emails, tell me tomorrow's weather in Pune, add a 30-min 'Prep' block before my first meeting tomorrow, and append the summary to my Daily Notes page."*
   - The run panel shows ≥ 3 tasks, and the independent ones overlap in time (checked against `agent_tasks` timestamps).
   - The verifier passes each task, and the final answer is coherent and correctly references every result.
   - The append step waits for the email summary.
3. **Concurrency:** 3 conversations run multi-agent requests at the same time in separate tabs. There's no cross-conversation leakage, and rate-limit waits are shown and recovered.
4. **SLOs:** all SLOs in the table above are met, measured from `agent_runs` and Lighthouse.
5. **Safety:** 0 destructive actions happen without confirmation, including under the injection pack.
6. **Quality:** eval ≥ 90%, and a self-review of 30 transcripts rates ≥ 4/5 for coherence and context.
7. **Offline:** with the network off, the app loads, history and search work, and a clear offline banner is shown.

## Timeline (solo, full-time; roughly double for part-time)

The phases run one after another, with 5 working days per week.

| Working days | Weeks | Phase | Milestone at end |
|---|---|---|---|
| 1–3 | 1 | Phase 0: Revive and stabilize | M0: prototype chats again |
| 4–6 | 1–2 | Phase 1: Local foundation | M1: one-command local app, `check.ps1` green |
| 7–12 | 2–3 | Phase 3: Data layer (before Phase 2, which needs the schema) | M3: data layer gate |
| 13–24 | 3–5 | Phase 2: Backend modules | M2: API streams single-tool answers |
| 25–38 | 5–8 | Phase 4: AI and parallel agents | M4: agents pass evals |
| 39–58 | 8–12 | Phase 5: Rich frontend | M5: UI gate |
| 59–66 | 12–14 | Phase 6: Integration and QA | M6: final acceptance passed |

Phase 5's first 3 days (design tokens and the `/ui` gallery) can move earlier, into waiting time during Phases 2–4 (for example while an eval run finishes). That doesn't change the total.

**Total is about 66 working days (~13 weeks full-time).**

**Resource guidance (solo):**
- Alternate backend and frontend weeks to keep momentum.
- Reserve Fridays for the eval run plus the visual snapshot review.
- Keep ~15% buffer for Groq model changes.

## Risks and mitigations

| Risk | Phase | Mitigation |
|---|---|---|
| Groq retires models again (it just did) | 0, 4 | Model roles live in settings, not code. Eval run before switching. Check the deprecation page monthly |
| 8K TPM free limit throttles multi-agent runs | 4 | Per-worker tool subsets, ≤ 3K-token calls, roles spread over models, stable prefixes for caching, visible queueing, fast path for simple requests |
| 1K requests/day cap | 4 | Heuristic router pre-check avoids LLM calls; daily meter + warning at 80% |
| Google testing-mode tokens expire every 7 days | 2, 5 | Status detection + one-click re-authorize on the Connections card |
| Prompt injection from emails, pages, or external MCP output | 4, 6 | Data wrapping, prompt-guard screen, per-task tool allowlists, confirmation gate, injection pack |
| 8 GB RAM pressure (browser + backend + embeddings) | 3, 6 | fastembed small model loaded lazily, RSS budget test, no Docker |
| UI polish drifts over time | 5 | Token-only styling lint, `/ui` gallery, Playwright visual baselines in `check.ps1` |
| Scope creep in the frontend | 5 | Screens fixed to the 6 above; extras go to a backlog |
| External stdio MCP servers misbehave | 2 | Per-server process supervision, timeouts, disable toggle, logs in UI |

## First week tasks (start immediately)

1. Switch the model to `openai/gpt-oss-120b` and fix the 5 known defects (Phase 0).
2. Add `pyproject.toml`, ruff, pytest, tool tests, and golden set v0.
3. Create the repo layout, `scripts/dev.ps1`, `start.ps1`, `check.ps1`, and the pre-commit hook.
4. Scaffold Vite React TS + Tailwind + shadcn/ui. Write the design tokens and the `/ui` gallery skeleton.
5. Write `donna/contracts.py` v0 (WS events + conversations API) and generate the TS types.
