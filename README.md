# Omni MCP Assistant

A modular, local-first AI assistant built using Python, FastMCP, and the Groq API. The assistant operates as an interactive terminal CLI where you can ask it to perform various agentic tasks using tools.

## Features

- **Interactive Agent Loop:** Talk directly to the `llama-3.3-70b-versatile` model via Groq, which uses a tool-calling agentic loop to dynamically perform actions.
- **Context-Aware:** The assistant is injected with the current system date and time, meaning it perfectly understands relative time references like "tomorrow" or "next week".
- **Google Calendar Integration:** Uses OAuth to seamlessly read your upcoming events and schedule new events on your Google Calendar.
- **Weather API:** Checks real-time weather and temperature for any city using the Open-Meteo API.
- **Utility Tools:** Includes simple tools like calculating squares and fetching random jokes.

## Setup

1. **Install Dependencies:**
   ```bash
   pip install httpx fastmcp mcp groq python-dotenv google-api-python-client google-auth-httplib2 google-auth-oauthlib
   ```

2. **Configure API Keys:**
   Create a `.env` file in the root directory and add your Groq API key:
   ```env
   GROQ_API_KEY=your_api_key_here
   ```

3. **Google Calendar Auth:**
   To use the Calendar features, you will need a `credentials.json` file from Google Cloud Console placed in the root directory. On first run, it will open your browser to authenticate and create a local `token.json`.

## Usage

1. **Run the Client:**
   ```bash
   python client.py
   ```
2. Type your requests when prompted with `You:` and enjoy!
