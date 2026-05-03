# tools/misc_tools.py — Miscellaneous utility tools

import httpx
from mcp_instance import mcp


@mcp.tool()
def square(n: int) -> int:
    """Calculate square of a number."""
    return n * n


@mcp.tool()
async def get_jokes() -> str:
    """Get random jokes"""
    url = "https://official-joke-api.appspot.com/random_joke"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        data = response.json()

    setup = data["setup"]
    punchline = data["punchline"]

    return f"{setup} - {punchline}"
