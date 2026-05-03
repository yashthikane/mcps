# Donna MCP Assistant

A modular, local-first AI assistant built using Python, FastMCP, and the Groq API. Donna operates as an interactive terminal CLI where you can ask it to perform various agentic tasks using tools — from managing your Notion workspace to scheduling Google Calendar events.

## Features

- **Interactive Agent Loop:** Talk directly to the `llama-3.3-70b-versatile` model via Groq, which uses a tool-calling agentic loop to dynamically perform actions.
- **Context-Aware:** The assistant is injected with the current system date and time, meaning it perfectly understands relative time references like "tomorrow" or "next week".
- **Notion Integration:** Full workspace management — list pages, read content, create/update/delete pages using the official Notion API.
- **Google Calendar Integration:** Uses OAuth to seamlessly read your upcoming events and schedule new events on your Google Calendar.
- **Weather API:** Checks real-time weather and temperature for any city using the Open-Meteo API.
- **Utility Tools:** Includes simple tools like calculating squares and fetching random jokes.

## Project Structure

```
mcp/
├── .env                    # API keys (Groq, Google, Notion)
├── .gitignore
├── mcp_instance.py         # Shared FastMCP server instance
├── server.py               # Entry point — imports all tools, runs server
├── client.py               # LLM client (Groq + MCP)
├── credentials.json        # Google OAuth credentials (not tracked)
├── token.json              # Google OAuth token (not tracked)
├── requirements.txt        # Python dependencies
└── tools/
    ├── __init__.py
    ├── notion_tools.py     # List, read, create, update, delete Notion pages
    ├── calendar_tools.py   # Get events, create events on Google Calendar
    ├── weather_tools.py    # Real-time weather by city
    └── misc_tools.py       # Square calculator, random jokes
```

## Setup

1. **Clone the repo:**
   ```bash
   git clone https://github.com/<your-username>/mcp.git
   cd mcp
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   # source venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```

3. **Configure API Keys:**
   Create a `.env` file in the root directory:
   ```env
   GROQ_API_KEY=your_groq_api_key
   INTERNAL_INTERGRATION_TOKEN=your_notion_integration_token
   ```

4. **Notion Setup:**
   - Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations) and create a new integration.
   - Copy the **Internal Integration Secret** and add it to `.env` as `INTERNAL_INTERGRATION_TOKEN`.
   - Share any Notion pages/databases you want Donna to access with the integration via the **"Add connections"** menu on each page.

5. **Google Calendar Auth:**
   Place a `credentials.json` file from Google Cloud Console in the root directory. On first run, it will open your browser to authenticate and create a local `token.json`.

## Usage

1. **Run the Client:**
   ```bash
   python client.py
   ```
2. Type your requests when prompted with `You:` and enjoy!

## Available Tools

| Tool | Description |
|---|---|
| `list_pages` | List all Notion pages & databases |
| `read_page_content` | Read properties & blocks of a Notion page |
| `create_page` | Create a new Notion page |
| `update_page_title` | Update a Notion page title |
| `append_text_to_page` | Append text blocks to a Notion page |
| `delete_page` | Archive/delete a Notion page |
| `get_events` | Get upcoming 5 Google Calendar events |
| `create_event` | Create a new Google Calendar event |
| `get_weather` | Get current weather for any city |
| `square` | Calculate the square of a number |
| `get_jokes` | Fetch a random joke |
