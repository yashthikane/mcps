# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Donna": a local AI assistant with a web UI. A FastAPI backend (`donna/`) runs a Groq tool-calling loop against MCP tools: the built-in FastMCP server in `tools/` plus user-added MCP servers. The React UI (`web/`) streams replies over Server-Sent Events. The terminal client `client.py` still works against the same tools.

**Hard constraints** (`docs/ROADMAP.md`):
- free and local only: bound to 127.0.0.1, no cloud, no CI/CD;
- Groq free tier only: 8K tokens/min, 30 req/min, 1K req/day per model;
- Windows laptop with 8 GB RAM.

Quality checks run locally via `scripts/check.ps1`.

## Commands

```powershell
scripts\start.ps1                    # venv + UI build on first run, then python -m donna → http://127.0.0.1:8765
scripts\dev.ps1                      # uvicorn --reload on :8765 + Vite dev server on :5173 (proxies /api)
scripts\check.ps1                    # pytest + tsc + vite build
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
  - It saves the assistant message with `tool_events`. Earlier tool results are summarized into history, so follow-ups can reuse IDs.
  - `select_tools` sends only keyword-matched tool groups (with a fallback to all tools) to stay under 8K tokens/min.
- `donna/hub.py`: `MCPHub` keeps one in-memory fastmcp `Client` to `mcp_instance.mcp`, plus one per enabled external server.
  - External tools are exposed as `<slug>__<tool>`.
  - `BUILTIN` maps connection IDs to tool names. Add new built-in tools there and to `CONFIRM` if they send, change or delete.
  - On Windows, `resolve_command` maps `npx` → `npx.cmd` (the bare name fails with WinError 193).
  - stdio servers only get a minimal environment plus their configured env vars (the values live in the vault).
- `donna/llm.py`: Groq streaming.
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
- `web/src/App.tsx` holds all app state; the components live in `web/src/components/`.
  - Styling is `neon-dusk.css` (ported from the accepted demo `docs/design/donna-neon-dusk-demo.html`) plus `app.css`.
  - Fonts are local: @fontsource Inter/Geist Mono (Latin subsets) and `public/fonts/GeistPixel-Circle.woff2`.

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

Keys live in Windows Credential Manager. `.env` (gitignored; template in `.env.example`) is only read to import `GROQ_API_KEY` / `INTERNAL_INTERGRATION_TOKEN` on first start. Runtime data (`data/`: DB, logs) is gitignored.
