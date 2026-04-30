# client.py
import asyncio
import json
from groq import Groq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import os
from dotenv import load_dotenv

load_dotenv()

groq_client = Groq()  # reads GROQ_API_KEY from env automatically

async def run():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:

            # 1. Initialize connection + discover tools
            await session.initialize()
            tools_response = await session.list_tools()

            # 2. Convert MCP tools → OpenAI-compatible format (what Groq expects)
            tools = []
            for tool in tools_response.tools:
                tools.append({
                    "type": "function",          # ← Groq needs this wrapper
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    }
                })

            print("Available tools:", [t["function"]["name"] for t in tools])

            messages = [
                {"role": "system", "content": "You are a helpful assistant. When calling tools, strictly pass arguments as a single JSON object (e.g. {\"n\": 33}), not as an array."}
            ]

            while True:
                # To prevent input() from blocking the asyncio event loop, we can run it in a thread
                # However, for a simple CLI, a standard input() works, but we can do it properly:
                user_input = await asyncio.to_thread(input, "\nYou: ")

                if user_input.lower() in ["exit", "quit"]:
                    break

                messages.append({"role": "user", "content": user_input})

                while True:
                    response = groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                    )

                    msg = response.choices[0].message
                    messages.append(msg)

                    # If no tool calls → print answer
                    if not msg.tool_calls:
                        print("\nAssistant:", msg.content)
                        break

                    # Execute tools
                    for tool_call in msg.tool_calls:
                        fn_name = tool_call.function.name
                        fn_args = json.loads(tool_call.function.arguments)

                        print(f"\nCalling tool: {fn_name}({fn_args})")

                        result = await session.call_tool(fn_name, fn_args)
                        result_text = result.content[0].text

                        print(f"Result: {result_text}")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text,
                        })

if __name__ == "__main__":
    asyncio.run(run())