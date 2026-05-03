# mcp_instance.py — Shared FastMCP server instance
# All tool modules import `mcp` from here to register their tools.

from fastmcp import FastMCP

mcp = FastMCP("Donna")
