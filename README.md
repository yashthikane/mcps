# Donna MCP Assistant

A local, free AI assistant with a web interface. Donna chats through Groq's free tier
(`openai/gpt-oss-120b`) and acts on your Gmail, Google Calendar and Notion, plus any
MCP server you add. Everything runs on your computer: conversations are stored in a
local SQLite file, and keys and sign-ins are kept in Windows Credential Manager.

## Features

- **Chat with streaming replies.** Live tool-call cards show the arguments, result and timing of each step, and an activity panel lists every tool call.
- **Approval before side effects.** Sending, replying, forwarding, deleting and calendar changes pause for your **Approve / Reject**.
- **Saved conversations** with full-text search, pin, rename and delete.
- **Connections page** with guided setup:
  - **Google** (Gmail + Calendar): upload `credentials.json`, sign in once, one-click **Re-authorize** when the 7-day testing token expires.
  - **Notion**: paste the integration secret, and Donna checks it and lists your shared pages.
  - **Any MCP server**: presets for Filesystem, Fetch, Git and Memory, or a custom command/URL. Donna tests the server, lists its tools, and lets you switch each one on or off.
- **Settings**: Groq key (tested before saving), model, reasoning effort, timezone, pixel grid and reduced motion, **Export** (JSON download) and **Wipe**.
- **Command palette** (Ctrl+K), `/` commands in the composer, an **offline banner**, first-time setup, and a layout that works on phones.

## Quick start (Windows)

```powershell
git clone https://github.com/yashthikane/mcps.git
cd mcps
scripts\start.ps1          # creates the venv, builds the UI on first run, opens http://127.0.0.1:8765
```

Requirements: Python 3.12, Node.js 20+, and a free Groq API key from https://console.groq.com/keys.
First-time setup asks for the key. If `GROQ_API_KEY` is in `.env`, it is imported automatically.

To start again later: `scripts\start.ps1`, or `venv\Scripts\python -m donna`.

### Connecting your apps

Open **Connections** and follow the wizards. Links and exact clicks are shown inside each step.

| Connection | What you need | Time |
|---|---|---|
| Gmail + Calendar | A free Google Cloud project with the Gmail and Calendar APIs enabled, an OAuth client of type **Desktop app**, and yourself as a test user | ~5 min |
| Notion | An internal integration at notion.so/profile/integrations, shared with the pages Donna may use | ~2 min |
| MCP servers | The server's command (`npx …` needs Node.js; `uvx …` needs [uv](https://docs.astral.sh/uv/)) or its HTTP URL | ~1 min |

## Tools

| Connection | Tools (✋ = asks for approval) |
|---|---|
| Gmail (12) | `list_emails`, `read_email`, `search_emails`, `get_unread_emails`, `send_email` ✋, `reply_email` ✋, `forward_email` ✋, `mark_email`, `delete_email` ✋, `create_draft`, `list_drafts`, `send_draft` ✋ |
| Calendar (5) | `get_events`, `list_events` (date range), `create_event` ✋, `update_event` ✋, `delete_event` ✋ |
| Notion (8) | `search_notion`, `list_pages`, `read_page_content`, `create_page` (with content), `update_page_title`, `append_text_to_page`, `delete_page` ✋, `query_database` |
| Weather (1) | `get_weather` |
| Utilities (2) | `square`, `get_jokes` |
| Your MCP servers | Every tool the server exposes. Tools whose names look like writes (write, delete, move, commit, …) ask first. |

## How it works

```
Browser (React + Vite) ──REST + Server-Sent Events──► FastAPI on 127.0.0.1:8765 (python -m donna)
                                                      ├─ agent   Groq streaming tool loop, approvals, Stop
                                                      ├─ hub     MCP clients: built-in tools (in-process) + your servers (stdio/HTTP)
                                                      ├─ store   SQLite data/donna.db (conversations, FTS search, servers, settings)
                                                      └─ vault   Windows Credential Manager (Groq key, Notion secret, Google sign-in)
```

- `donna/`: the backend (`app.py` API, `agent.py` chat loop, `hub.py` MCP connections, `llm.py` Groq, `store.py`, `vault.py`).
- `tools/`: the built-in MCP tools, served by `server.py` / `mcp_instance.py` (also usable from the terminal with `python client.py`).
- `web/`: the React UI (Neon Dusk design).
- `docs/ROADMAP.md`: the plan. `docs/design/`: design explorations.

To stay within Groq's free tier (8K tokens/min), Donna only sends the tools that match your
message, for example Gmail tools when you mention email, and falls back to all tools when nothing matches.

## Development

```powershell
scripts\dev.ps1             # backend with auto-reload (:8765) + Vite dev server (:5173)
scripts\check.ps1           # pytest + TypeScript check + production build
venv\Scripts\python -m pytest -q
```

### Testing the UI end to end

The browser test drives the real app with a scripted model (`DONNA_FAKE_LLM=1`), a throwaway
data folder and a separate keyring service, so your real data and keys are never touched:

```powershell
$env:DONNA_FAKE_LLM="1"; $env:DONNA_DATA_DIR="$env:TEMP\donna-e2e-data"; $env:DONNA_KEYRING_SERVICE="donna-e2e"
venv\Scripts\python -m donna --no-browser --port 8799      # in one terminal
python tests\e2e_ui.py http://127.0.0.1:8799                # in another (needs: pip install playwright)
python tests\e2e_reset.py                                   # clean up afterwards
```
