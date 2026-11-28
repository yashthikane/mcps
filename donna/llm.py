"""The model behind Donna, chosen in Settings:

- groq: Groq's API with native tool calls (streaming), with the key from the vault.
- claude_code: the official Claude Code CLI on this computer (`claude -p`), signed in with the owner's own
  Claude subscription. Donna never sees or stores that sign-in. Claude Code's own tools are switched off
  (`--tools ""`): each call returns a reply and at most one of Donna's tools to run (structured output), and
  Donna runs it with her usual permissions and approvals, then asks again.

Plus a scripted fake model for tests (DONNA_FAKE_LLM=1).
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile

import groq

from . import DATA_DIR, vault

FAKE = os.getenv("DONNA_FAKE_LLM") == "1"
PROVIDERS = ("groq", "claude_code")
CLAUDE_TIMEOUT = 240  # seconds per step; a cold start plus a long reply fits well within this
_ARG_LIMIT = 24000    # Windows caps a command line at 32K characters; longer system prompts go via stdin


class LLMError(Exception):
    """A user-facing model error (bad key, rate limit exhausted, network)."""


# ---------------------------------------------------------------------------- provider choice
def provider(settings: dict) -> str:
    p = settings.get("llm_provider") or "groq"
    return p if p in PROVIDERS else "groq"


def claude_cli() -> str | None:
    from .hub import resolve_command
    found = shutil.which("claude")
    return resolve_command(found) if found else None


def problem(settings: dict) -> str | None:
    """Why Donna can't call the chosen model right now, or None."""
    if FAKE:
        return None
    if provider(settings) == "claude_code":
        return None if claude_cli() else ("Claude Code isn't installed. Install it (https://claude.com/claude-code), "
                                          "sign in with `claude`, or switch the model provider back to Groq in Settings.")
    return None if vault.get(vault.GROQ_KEY) else "Add your Groq API key in Settings to start chatting."


async def turn(settings: dict, messages: list, tools: list):
    """One model step with the provider from Settings. Same events as stream_turn."""
    if not FAKE and provider(settings) == "claude_code":
        async for ev in claude_code_turn(settings.get("claude_model") or "opus", messages, tools,
                                         settings.get("reasoning_effort", "medium")):
            yield ev
        return
    async for ev in stream_turn(vault.get(vault.GROQ_KEY) or "", settings["model"], messages, tools,
                                settings.get("reasoning_effort", "medium")):
        yield ev


async def complete_with(settings: dict, messages: list) -> str:
    """One tool-less step returned as text (summaries), with the provider from Settings."""
    parts = []
    async for kind, value in turn(settings, messages, []):
        if kind == "delta":
            parts.append(value)
    return "".join(parts)


# ---------------------------------------------------------------------------- Claude Code
_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Text for the person now. Empty when you only call a tool first."},
        "tool_call": {"type": ["object", "null"], "description": "One tool to run next, or null when you're done.",
                      "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                      "required": ["name", "arguments"]},
    },
    "required": ["reply", "tool_call"],
    "additionalProperties": False,
}
_PROTOCOL = (
    "\n\nHOW YOU ACT: you can't run tools yourself. Each time, answer with JSON: \"reply\" (what to say now) and "
    "\"tool_call\" (one of the listed tools with arguments matching its schema, or null). When you call a tool, keep "
    "\"reply\" empty or to one short sentence; you'll see the result in the next message and can continue. When you "
    "have everything, give the final \"reply\" with \"tool_call\": null. Use only the listed tools and only their "
    "listed parameters; never claim a tool ran unless you see its result."
)


def _render(messages: list, tools: list) -> tuple[str, str]:
    """(system prompt, conversation as text) for the CLI, which takes one prompt per call."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system") + (_PROTOCOL if tools else "")
    lines = []
    if tools:
        lines.append("AVAILABLE TOOLS:")
        for t in tools:
            f = t["function"]
            lines.append(f"- {f['name']}: {f.get('description', '').strip()}\n  parameters: "
                         f"{json.dumps(f.get('parameters', {}), ensure_ascii=False)}")
        lines.append("")
    lines.append("CONVERSATION (oldest first):")
    names = {}
    for m in messages:
        if m["role"] == "user":
            lines.append(f"[User] {m['content']}")
        elif m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"[You] {m['content']}")
            for c in m.get("tool_calls") or []:
                names[c["id"]] = c["function"]["name"]
                lines.append(f"[You called {c['function']['name']}] {c['function']['arguments']}")
        elif m["role"] == "tool":
            lines.append(f"[Result of {names.get(m.get('tool_call_id'), 'tool')}] {m['content']}")
    lines.append("\nNow decide your next step and answer with the JSON.")
    return system, "\n".join(lines)


async def claude_code_turn(model: str, messages: list, tools: list, effort: str = "medium"):
    """One step through the Claude Code CLI. Yields ("delta", text), ("tool_calls", [...]), ("usage", {...})."""
    exe = claude_cli()
    if not exe:
        raise LLMError("Claude Code isn't installed. Install it and sign in with `claude`, or switch back to Groq in Settings.")
    system, prompt = _render(messages, tools)
    if len(system) > _ARG_LIMIT:  # keep the command line under Windows' limit
        prompt, system = f"INSTRUCTIONS:\n{system}\n\n{prompt}", "Follow the INSTRUCTIONS at the start of the message exactly."
    args = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--model", model,
            "--system-prompt", system, "--json-schema", json.dumps(_STEP_SCHEMA)]
    if effort in ("low", "medium", "high"):
        args += ["--effort", effort]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="cc-", dir=DATA_DIR)  # a neutral folder: no project CLAUDE.md gets loaded
    yield "status", "Asking Claude…"

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=workdir, timeout=CLAUDE_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        done = await asyncio.to_thread(run)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"Claude didn't answer within {CLAUDE_TIMEOUT} seconds. Try again.") from e
    except OSError as e:
        raise LLMError(f"Couldn't start Claude Code: {e}") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    try:
        out = json.loads(done.stdout or "{}")
    except ValueError:
        out = {}
    if done.returncode != 0 or out.get("is_error") or "structured_output" not in out:
        detail = (out.get("result") or done.stderr or done.stdout or "no output").strip().splitlines()
        text = detail[-1][:300] if detail else "no output"
        if re.search(r"log ?in|auth|credential|token|unauthori", text, re.I):
            raise LLMError("Claude Code isn't signed in. Run `claude` in a terminal and sign in (or `claude setup-token`), "
                           "then try again.")
        if re.search(r"usage limit|rate limit|limit reached", text, re.I):
            raise LLMError(f"Your Claude usage limit was reached: {text}")
        raise LLMError(f"Claude Code error: {text}")
    usage = out.get("usage") or {}
    yield "usage", {"total_tokens": sum(int(usage.get(k) or 0) for k in
                                        ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))}
    step = out.get("structured_output") or {}
    reply = str(step.get("reply") or "").strip()
    call = step.get("tool_call")
    if reply:
        yield "delta", reply
    if isinstance(call, dict) and call.get("name") and tools:
        n = sum(1 for m in messages if m["role"] == "tool")
        yield "tool_calls", [{"id": f"cc_{n}", "name": str(call["name"]),
                              "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False)}]


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
    elif (m := re.search(r"(?:picture|image) of (.+)", user, re.I)) and "generate_image" in names:
        yield call("generate_image", {"prompt": m.group(1).strip()})
    elif "create event" in low and "create_event" in names:
        yield call("create_event", {"summary": "Prep", "start_time": "2026-10-01T09:00", "end_time": "2026-10-01T09:30"})
    elif "book a meet" in low and "create_event" in names:
        yield call("create_event", {"summary": "Catch-up", "start_time": "2026-10-02T16:00", "end_time": "2026-10-02T16:30",
                                    "add_meet": True})
    elif re.search(r"\bfree\b|availab", low) and "check_availability" in names:
        yield call("check_availability", {"start": "2026-10-02"})
    elif (m := re.search(r"remember (?:that )?(.+)", user, re.I)) and "remember" in names:
        yield call("remember", {"note": m.group(1)})
    elif re.search(r"phone|call me|callback|call back", low) and "record_request" in names:
        yield call("record_request", {"type": "callback", "summary": "Wants a call back"})
    elif re.search(r"\bmeeting\b", low) and "record_request" in names:
        yield call("record_request", {"type": "meeting", "summary": "Wants a meeting", "details": {"preferred_time": "tomorrow 4 PM"}})
    elif re.search(r"website|quotation|enquiry", low) and "record_request" in names:
        yield call("record_request", {"type": "business_enquiry", "summary": "Website development enquiry",
                                      "details": {"organization": "ABC Company"}, "missing": "budget and timeline"})
    elif "send email" in low and "send_email" in names:
        yield call("send_email", {"to": "boss@example.com", "subject": "Hi", "body": "Hello"})
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
