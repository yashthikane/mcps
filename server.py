# server.py — Unified MCP server
# Imports all tool modules so their @mcp.tool() decorators register with
# the shared FastMCP instance defined in mcp_instance.py.

from mcp_instance import mcp

# Import tool modules to register their tools with the mcp instance
import tools.notion_tools      # Notion workspace management
import tools.calendar_tools    # Google Calendar
import tools.gmail_tools       # Gmail management
import tools.weather_tools     # Weather forecast
import tools.misc_tools        # Square, jokes, etc.

if __name__ == "__main__":
    mcp.run()