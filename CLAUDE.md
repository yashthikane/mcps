# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Donna" — a local terminal AI assistant. A Groq-hosted LLM drives an agentic tool-calling loop against a FastMCP server that exposes Notion, Gmail, Google Calendar, weather, and misc tools.

`client.py` uses `openai/gpt-oss-120b`. The previous model, `llama-3.3-70b-versatile`, is no longer available on Groq's free tier. gpt-oss replies include a `reasoning` field, so assistant messages are rebuilt as plain dicts before they're added back to the history. gpt-oss doesn't support parallel tool calls.

**Roadmap:** `docs/ROADMAP.md` is the approved plan. It turns this into a local, free, single-machine app: a FastAPI modular monolith, SQLite + sqlite-vec, a React + Vite UI, Groq free tier only, and parallel agents. Hard constraints: no cloud, hosting, paid services, or CI/CD. Quality checks run locally via `scripts/check.ps1`. Follow its phase order and exit gates.

## Commands

```bash
python -m venv venv && venv\Scripts\activate   # Windows
pip install -r requirements.txt

python client.py            # run the assistant (spawns server.py itself)
python server.py            # run the MCP server alone over stdio (e.g. for another MCP client)
```

There is no test suite, linter, or build step.

Run everything from the repo root. `client.py` launches `server.py` by relative path using its own interpreter (`sys.executable`), so the server always runs in the same venv. The Google auth helpers read/write `credentials.json`, `token.json`, and `gmail_token.json` relative to the current working directory.

Keep `requirements.txt` UTF-8. PowerShell's `pip freeze > requirements.txt` writes UTF-16, so use `pip freeze | Out-File -Encoding utf8 requirements.txt` instead.

## Architecture

- `mcp_instance.py` holds the single shared `FastMCP("Donna")` instance. It exists separately from `server.py` so tool modules can import `mcp` without circular imports.
- `tools/*.py` each import `mcp` from `mcp_instance` and register functions with `@mcp.tool()`. Registration happens as a side effect of import.
- `server.py` imports each tool module to trigger registration, then calls `mcp.run()` (stdio). **A new tool module must be imported in `server.py`**, or its tools won't be exposed.
- `client.py` starts `server.py` as a stdio subprocess, lists its tools, and converts each MCP tool schema into the OpenAI-style `{"type": "function", ...}` format that Groq expects. It then loops: call Groq → run any tool calls through `session.call_tool` → append results as `role: tool` messages → repeat until the model replies without tool calls. The system prompt includes the current date and time so the model can resolve relative dates.

### Conventions for tools

- Tools return a plain `str`. The client reads only `result.content[0].text`. Errors are generally caught and returned as strings, not raised.
- The tool's docstring and type hints become the description and schema the LLM sees, so document argument formats in the docstring (see `create_event` and `list_emails`).
- Use async functions with `httpx.AsyncClient` / Notion `AsyncClient` for network calls. The Google API tools are synchronous.
- Calendar and Gmail each have their own OAuth token file and scopes (`get_calendar_service` / `get_gmail_service`). Both share `credentials.json`. The first call opens a browser for consent.
- `notion_tools.py` reads `NOTION_API_KEY` or `INTERNAL_INTERGRATION_TOKEN` (the misspelling is intentional and matches `.env`). If neither is set, `notion` is a stand-in object that raises on use, so only the Notion tools return errors.
- `create_event` hardcodes the timezone `Asia/Kolkata`.

## Environment

`.env` (gitignored; template in `.env.example`): `GROQ_API_KEY` and `INTERNAL_INTERGRATION_TOKEN` (Notion). Notion pages must be shared with the integration via "Add connections" to be visible.
