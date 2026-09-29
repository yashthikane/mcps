"""Groq chat streaming with tool calls, plus a scripted fake model for UI tests (DONNA_FAKE_LLM=1)."""
import asyncio
import json
import os
import re

import groq

FAKE = os.getenv("DONNA_FAKE_LLM") == "1"


class LLMError(Exception):
    """A user-facing model error (bad key, rate limit exhausted, network)."""


async def stream_turn(api_key: str, model: str, messages: list, tools: list, effort: str = "medium"):
    """Yield ("delta", text) | ("status", text) | ("tool_calls", [{id, name, arguments}]) | ("usage", dict)."""
    if FAKE:
        async for ev in _fake_turn(messages, tools):
            yield ev
        return

    client = groq.AsyncGroq(api_key=api_key, max_retries=0, timeout=90)
    kwargs = dict(model=model, messages=messages, stream=True, temperature=1, top_p=1,
                  max_completion_tokens=2048)
    if tools:
        kwargs.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = effort

    for attempt in range(4):
        calls: dict[int, dict] = {}
        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    if getattr(chunk, "x_groq", None) and getattr(chunk.x_groq, "usage", None):
                        yield "usage", chunk.x_groq.usage.model_dump()
                    continue
                d = chunk.choices[0].delta
                if d.content:
                    yield "delta", d.content
                for tc in d.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function and tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["arguments"] += tc.function.arguments
                x = getattr(chunk, "x_groq", None)
                if x is not None and getattr(x, "usage", None):
                    yield "usage", x.usage.model_dump()
            if calls:
                yield "tool_calls", [calls[i] for i in sorted(calls)]
            return
        except groq.AuthenticationError as e:
            raise LLMError("Groq rejected the API key. Update it in Settings.") from e
        except groq.RateLimitError as e:
            wait = _retry_after(e)
            if attempt == 3 or wait > 60:
                raise LLMError(f"Groq's free-tier limit was reached. Try again in about {max(1, round(wait))} seconds.") from e
            yield "status", f"Groq rate limit reached, retrying in {wait:.0f}s…"
            await asyncio.sleep(wait)
        except groq.BadRequestError as e:
            body = getattr(e, "body", None) or {}
            err = body.get("error", body) if isinstance(body, dict) else {}
            if isinstance(err, dict) and err.get("code") == "tool_use_failed" and attempt < 2:
                yield "status", "The model produced an invalid tool call, retrying…"
                kwargs["messages"] = messages + [{"role": "user", "content":
                    "Your last tool call had invalid arguments. Call the tool again with valid JSON that matches its schema."}]
                continue
            raise LLMError(f"Groq couldn't process the request: {_msg(e)}") from e
        except groq.APIConnectionError as e:
            raise LLMError("Can't reach Groq. Check your internet connection.") from e
        except groq.APIStatusError as e:
            if e.status_code >= 500 and attempt < 3:
                yield "status", "Groq had a server error, retrying…"
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise LLMError(f"Groq error {e.status_code}: {_msg(e)}") from e


def _retry_after(e: groq.RateLimitError) -> float:
    try:
        return float(e.response.headers.get("retry-after", "5"))
    except (TypeError, ValueError, AttributeError):
        return 5.0


def _msg(e: Exception) -> str:
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(e)[:300]


async def validate_key(api_key: str) -> list[str]:
    """Return available chat model IDs, or raise LLMError."""
    if FAKE:
        if api_key.startswith("gsk_"):
            return ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        raise LLMError("Groq rejected the API key. Check it and try again.")
    try:
        models = await groq.AsyncGroq(api_key=api_key, max_retries=0, timeout=15).models.list()
    except groq.AuthenticationError as e:
        raise LLMError("Groq rejected the API key. Check it and try again.") from e
    except groq.APIConnectionError as e:
        raise LLMError("Can't reach Groq. Check your internet connection.") from e
    return sorted(m.id for m in models.data if getattr(m, "active", True))


# ---------------------------------------------------------------------------- fake model
async def _fake_turn(messages: list, tools: list):
    """Deterministic stand-in for Groq used by the UI tests. Never calls the network."""
    names = {t["function"]["name"] for t in tools}
    last = messages[-1]
    if last["role"] == "tool":
        text = f"Done. Result: {last['content'][:240]}"
        for word in re.findall(r"\S+\s*", text):
            yield "delta", word
            await asyncio.sleep(0.01)
        return
    user = last["content"]
    low = user.lower()

    def call(name, args):
        return "tool_calls", [{"id": "call_" + name, "name": name, "arguments": json.dumps(args)}]

    if (m := re.search(r"weather in ([A-Za-z ]+)", user)) and "get_weather" in names:
        yield call("get_weather", {"city": m.group(1).strip()})
    elif (m := re.search(r"square (?:of )?(-?\d+)", low)) and "square" in names:
        yield call("square", {"n": int(m.group(1))})
    elif "create event" in low and "create_event" in names:
        yield call("create_event", {"summary": "Prep", "start_time": "2026-10-01T09:00", "end_time": "2026-10-01T09:30"})
    elif "list folders" in low and (fs := next((n for n in names if n.endswith("__list_allowed_directories")), None)):
        yield call(fs, {})
    elif "slow" in low:
        for i in range(200):
            yield "delta", f"word{i} "
            await asyncio.sleep(0.05)
    else:
        for word in re.findall(r"\S+\s*", f"You said: {user}. I'm the test model."):
            yield "delta", word
            await asyncio.sleep(0.01)
