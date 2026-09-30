# Donna: progress

What is built, what works, and what is still left, with the roadmap phase each item belongs to (`docs/ROADMAP.md`).
Update this file whenever a feature lands or its status changes.

**Last updated:** 2026-09-30 · **Latest commit:** `dba8274` + uncommitted scheduler work · **Branch:** `main`

**Status key:**
- ✅ Working: implemented and verified (automated test, live run, or confirmed by the user in the app)
- 🟡 Partial: works, but with limits noted
- ⬜ Not started

## Summary

| Area | Status |
|---|---|
| Web app (chat, conversations, connections, settings) | ✅ Working, confirmed by the user in the app on 2026-09-29 |
| Built-in tools (32) over MCP | ✅ Working |
| Scheduled tasks: BullMQ scheduler (Node) + Redis (WSL) + PostgreSQL, Tasks page, chat tools | 🟡 Built and unit-tested; live run needs `scripts\setup-scheduler.ps1` (PostgreSQL password) |
| Google (Gmail + Calendar), Notion, custom MCP servers | ✅ Connect from the UI and use from chat |
| Groq model (`openai/gpt-oss-120b`, free tier) | ✅ Live streaming with tool calls |
| Parallel agents (planner, workers, verifier) | ⬜ Roadmap Phase 4, not started |
| Semantic memory, backups, eval harness | ⬜ Not started |

## How to run

```powershell
scripts\start.ps1        # http://127.0.0.1:8765
scripts\check.ps1        # tests + typecheck + build
```

## Implemented and working

### Chat (Phase 2 / 4 / 5)
| Feature | Status | Notes |
|---|---|---|
| Streamed replies (Server-Sent Events) | ✅ | Text updates once per animation frame; Markdown rendering (lazy-loaded, no raw HTML) |
| Permission modes: Plan (read-only, writes a plan) · Manual (approve actions) · Auto (no approvals), in chat and per scheduled AI task | ✅ | Composer switch + Shift+Tab + palette; auto-mode tasks created from chat need one approval |
| Tool calling loop | ✅ | Up to 8 steps per message; one tool call at a time (gpt-oss has no parallel tool calls) |
| Tool-call cards + activity panel | ✅ | Arguments, result, timing, status for every call |
| Approval before side effects | ✅ | Approve / Reject for send, reply, forward, send draft, delete email, create/update/delete event, delete page, and write-like tools on custom MCP servers |
| Stop a reply | ✅ | Cancels the run; the partial answer is saved as "[stopped]" |
| Groq rate-limit handling | 🟡 | Retries 429 using `retry-after` and shows a status line; no per-model token budgeting yet |
| Keyword tool routing (to fit 8K tokens/min) | ✅ | Sends only relevant tool groups; falls back to all tools when nothing matches |
| Follow-ups reuse IDs from earlier tool results | ✅ | Earlier tool results are summarized into the history |
| Composer: Enter/Shift+Enter, `/` commands, `@connection` chips | ✅ | |
| Command palette (Ctrl+K) | ✅ | |
| Offline / missing-key banner | ✅ | Sending is disabled while offline; history and search still work |
| First-time setup (key → timezone → connect → try it) | ✅ | |
| Mobile layout (sidebar and activity panel as drawers) | ✅ | Checked at 390px |

### Conversations (Phase 3)
| Feature | Status | Notes |
|---|---|---|
| Saved conversations (SQLite `data/donna.db`) | ✅ | stdlib sqlite3 in WAL mode |
| Full-text search (FTS5) over titles and messages | ✅ | |
| Pin, rename, delete | ✅ | |
| Export (JSON download) and Wipe | ✅ | |

### Connections (Phase 2 / 5)
| Feature | Status | Notes |
|---|---|---|
| Google wizard (project → OAuth client → upload → authorize → test) | ✅ | One sign-in for Gmail + Calendar |
| One-click Google re-authorize | ✅ | Testing-mode tokens expire after 7 days |
| Notion wizard (integration → share → secret, validated) | ✅ | |
| Add any MCP server (stdio command or HTTP URL) | ✅ | Presets: Filesystem, Memory (npx), Fetch, Git (need `uv`); test → pick tools → save |
| Per-tool on/off, connection on/off, restart, remove | ✅ | |
| Windows `npx` → `npx.cmd` resolution | ✅ | Fixed "not a valid Win32 application" |

### Settings and security (Phase 3 / 6)
| Feature | Status | Notes |
|---|---|---|
| Keys in Windows Credential Manager | ✅ | Groq key, Notion secret, Google client + refresh token, MCP env vars; long values are chunked |
| One-time import of `.env` keys | ✅ | |
| Groq key test, model, reasoning effort, timezone | ✅ | |
| Appearance (pixel grid, reduce motion) | ✅ | |
| Localhost only (127.0.0.1 + Origin check) | ✅ | |
| Logs | ✅ | `data/donna.log` (rotating) |

### Tools (28)
| Connection | Tools | Status |
|---|---|---|
| Gmail (12) | list, read, search, unread, send, **reply**, **forward**, **mark read/unread**, delete (trash), **create draft**, **list drafts**, **send draft** | ✅ |
| Calendar (5) | upcoming events, **events in a date range**, create, **update**, **delete**, all in the calendar's own timezone | ✅ |
| Notion (8) | **search**, list pages, read page (plain text), create page **with content**, rename, append (headings/bullets/to-dos), delete (trash), **query database** | ✅ |
| Weather (1), Utilities (2) | get_weather, square, get_jokes | ✅ |
| Scheduled tasks (4) | schedule_task, list_scheduled_tasks, cancel_scheduled_task (asks first), snooze_scheduled_task | 🟡 Needs the scheduler running |

Bold = added in the web-app release. The terminal client (`python client.py`) still works with all 32 tools (the scheduling tools report that scheduling is unavailable there).

### Tests (Phase 6)
| Suite | Status |
|---|---|
| Backend unit + API tests (`pytest`, 24 tests + 1 PostgreSQL test) | ✅ Passing (the PostgreSQL test is skipped until `DONNA_TEST_POSTGRES_URL` is set) |
| Scheduler unit tests (`cd scheduler; npm test`, 17 tests) | ✅ Passing |
| Live scheduler scenarios (reminder, recurring, cancel, snooze, scheduler/Redis/FastAPI restart, AI task) | ⬜ Run after `setup-scheduler.ps1` (see docs/ARCHITECTURE.md §4) |
| Browser end-to-end test (`tests/e2e_ui.py`, 14 scenarios, scripted model) | ✅ Passing |
| Live check with the real Groq model | ✅ Weather, multi-tool chaining, and the Notion-not-connected path |
| Live checklist on real Google/Notion accounts (create/update/delete test items) | 🟡 Confirmed working by the user; the scripted checklist hasn't been run yet |

## Not started / planned

| Item | Roadmap phase |
|---|---|
| Parallel agents: planner → workers → verifier → synthesizer | 4 |
| Per-model token buckets, daily budget warnings, fallback model on rate limit | 4 |
| Prompt-injection screening (Groq prompt-guard) for email/Notion content | 4 |
| Eval harness (golden prompts) | 0 / 4 |
| Semantic memory (sqlite-vec + fastembed) | 3 |
| Nightly database backups, schema migrations | 3 |
| Linting (ruff, mypy, eslint) and a pre-commit hook | 1 |
| Stream resume after a reconnect | 2 |
| Visual regression baselines, Lighthouse and axe checks in `check.ps1` | 5 |

## Known limitations
- Groq free tier: 30 requests/min, 1,000/day and 8K tokens/min per model. Long multi-tool tasks can hit the rate limit and wait.
- Google in Testing mode needs re-authorizing every 7 days (one click in Connections).
- Fetch and Git MCP presets need [uv](https://docs.astral.sh/uv/) installed; npx presets need Node.js.
- Chat needs internet (Groq); the UI, history and search work offline.
- Scheduled tasks need PostgreSQL and Redis (WSL Ubuntu) running; Donna works without them, and saved tasks run once they are back.
- Scheduled AI tasks only read: approval-gated actions are flagged (`approval_required`) instead of run.

## Changelog
| Date | Commit | Change |
|---|---|---|
| 2026-09-30 | — | Permission modes (Plan / Manual / Auto) for chat and scheduled AI tasks |
| 2026-09-30 | — | Scheduler subsystem: `scheduler/` (Node, BullMQ, Redis), PostgreSQL migrations, task API + internal execute API, reminders (no Groq) and unattended AI tasks, notifications (Windows toast, in-app), Tasks page, chat scheduling tools, setup/start scripts, docs |
| 2026-09-30 | — | Added this progress file |
| 2026-09-29 | `7ce67b3` | Web app: chat UI, conversations, connections and setup wizards, settings, 11 new tools, tests |
| 2026-09-28 | `1537507` | Switched to `openai/gpt-oss-120b` (old model retired), startup fixes, roadmap, CLAUDE.md |
| earlier | `07af512`…`79aaa17` | Terminal prototype: weather, jokes, Calendar, Notion and Gmail MCP tools |
