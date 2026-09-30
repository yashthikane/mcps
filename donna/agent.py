"""The chat loop: stream a model turn, run the tools it asks for (pausing for approval on
anything that sends, changes or deletes), feed results back, repeat, then save the reply."""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from . import llm, vault
from .hub import BUILTIN, MCPHub, Tool

MAX_STEPS = 8
APPROVAL_TIMEOUT = 600
HISTORY_CHARS = 9000
TOOL_RESULT_CHARS = 4000

# Keyword routing keeps each request small: the Groq free tier allows 8K tokens/minute,
# and sending all ~30 tool schemas costs ~3K tokens per call.
KEYWORDS = {
    "gmail": r"e-?mails?|inbox|\bmail|gmail|unread|repl(y|ies)|draft|forward|sender|subject|message from",
    "calendar": r"calendar|meeting|event|schedul|appointment|agenda|\bbusy\b|\bfree\b|remind|invite",
    "notion": r"notion|\bpages?\b|\bnotes?\b|database|\bdocs?\b|wiki|workspace",
    "weather": r"weather|temperature|\brain|forecast|sunny|humid",
    "utils": r"square|joke|funny",
    "schedule": r"remind|schedul|snooze|recurring|\btasks?\b|every (day|morning|evening|night|week|hour|\d+ ?(min|hour))|every (mon|tue|wed|thu|fri|sat|sun)",
}
STOP_WORDS = {"get", "list", "read", "create", "update", "delete", "set", "the", "a", "of", "to", "info", "multiple", "allowed"}


# Permission modes, chosen per chat message (composer switch) or per scheduled task:
#   plan    read-only tools only; Donna writes a plan instead of acting
#   manual  actions that send, change or delete wait for Approve / Reject (refused in scheduled runs)
#   auto    every action runs without asking
MODES = ("plan", "manual", "auto")


def needs_approval(tool: Tool, args: dict) -> bool:
    """Approval-gated in manual mode. Scheduling an auto-mode task is gated too: it hands future,
    unattended runs permission to act, so the user must agree to that once, when it's created."""
    if tool.confirm:
        return True
    return tool.name in ("schedule_task",) and str(args.get("permission_mode", "")).lower() == "auto"


class Run:
    def __init__(self, conversation_id: str, unattended: bool = False, mode: str = "manual"):
        self.id = "r_" + uuid.uuid4().hex[:8]
        self.conversation_id = conversation_id
        self.cancelled = False
        self.pending: dict[str, asyncio.Future] = {}
        # Scheduled runs have nobody to approve actions: in manual mode they're refused and flagged.
        self.unattended = unattended
        self.mode = mode if mode in MODES else "manual"
        self.approval_needed = False

    def decide(self, call_id: str, approved: bool) -> bool:
        fut = self.pending.get(call_id)
        if fut and not fut.done():
            fut.set_result(approved)
            return True
        return False

    def cancel(self) -> None:
        self.cancelled = True
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(False)


RUNS: dict[str, Run] = {}


SYNONYMS = {"directory": "folder", "directories": "folders", "file": "document", "repository": "repo", "commit": "git"}


def select_tools(tools: list[Tool], text: str, recent_groups: set[str], forced: set[str]) -> list[Tool]:
    """Pick the tool groups this message needs. Connections used in the last turns are kept for
    follow-ups, but only when the message matches something; otherwise every tool is offered."""
    low = text.lower()
    groups = set(forced)
    for group, pattern in KEYWORDS.items():
        if re.search(pattern, low):
            groups.add(group)
    for t in tools:
        if t.server != "donna":
            words = set(re.split(r"[_\W]+", t.remote.lower())) - STOP_WORDS
            words |= {SYNONYMS[w] for w in words if w in SYNONYMS}
            server_words = set(t.name.split("__")[0].split("_"))
            if any(w and len(w) > 2 and w in low for w in words | server_words):
                groups.add(t.group)
    if not groups:
        return tools
    picked = [t for t in tools if t.group in groups | set(recent_groups)]
    return picked or tools


MODE_PROMPTS = {
    "plan": ("\nPLAN MODE: don't carry out any action. You may use the available read-only tools to look things up, "
             "then answer with a short numbered plan: which steps and tools you would use and with what arguments. "
             "Actions that send, change or delete aren't available in this mode. End by telling the user to switch "
             "to Manual or Auto mode to run it."),
    "manual": "",
    "auto": "\nAUTO MODE: the user pre-approved every action; call tools directly without asking for confirmation.",
}


def system_prompt(settings: dict, mode: str = "manual") -> str:
    tz = settings.get("timezone") or "UTC"
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 - unknown timezone name
        now, tz = datetime.now().astimezone(), "local"
    return (
        "You are Donna, a capable personal assistant running locally for one user. "
        f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M')} ({tz}).\n"
        "You can act on the user's Gmail, Google Calendar, Notion, the weather and any connected MCP servers through tools.\n"
        "Rules:\n"
        "- Use IDs returned by tools (email, draft, event, page, database IDs). Never invent IDs; look them up first.\n"
        "- For calendar times use local time without an offset, e.g. 2026-10-01T09:00; all-day events use dates.\n"
        "- Tools that send, change or delete things ask the user for approval automatically. Just call them; don't ask for confirmation in text first.\n"
        "- If a tool says a service isn't connected or a sign-in expired, tell the user to fix it in Connections.\n"
        "- If the user rejected an action, acknowledge it and don't retry.\n"
        "- Call one tool at a time. Be concise and use Markdown lists for several items."
        + MODE_PROMPTS.get(mode, "")
    )


def history(store, conversation_id: str) -> list[dict]:
    """Recent turns as plain messages. Tool results from earlier turns are summarised into the
    assistant text so follow-ups ("delete that page") can use the IDs."""
    out, total = [], 0
    for m in reversed(store.messages(conversation_id)):
        content = m["content"] or ""
        if m["role"] == "assistant" and m["tool_events"]:
            notes = "; ".join(f"{e['name']} → {(e.get('preview') or '')[:300]}" for e in m["tool_events"])
            content = f"{content}\n\n[Tool results: {notes}]"
        total += len(content)
        if total > HISTORY_CHARS and out:
            break
        out.append({"role": "user" if m["role"] == "user" else "assistant", "content": content})
    return list(reversed(out))


def recent_groups(store, conversation_id: str, hub: MCPHub) -> set[str]:
    groups: set[str] = set()
    by_name = {t.name: t.group for t in hub.all_tools()}
    for m in store.messages(conversation_id)[-4:]:
        for e in m["tool_events"]:
            if e["name"] in by_name:
                groups.add(by_name[e["name"]])
    return groups


def title_from(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")[:7]
    title = " ".join(words)
    return (title[:48] + "…") if len(title) > 48 else title[:1].upper() + title[1:]


async def run_chat(store, hub: MCPHub, run: Run, text: str, forced_groups: set[str]):
    """Async generator of UI events: run.start, delta, status, tool.start, confirm.request,
    tool.end, done, error."""
    cid = run.conversation_id
    settings = store.settings()
    conv = store.get_conversation(cid)
    prior = history(store, cid)
    groups = recent_groups(store, cid, hub)
    store.add_message(cid, "user", text)
    if conv and conv["title"] == "New chat":
        store.update_conversation(cid, title=title_from(text))
    yield {"type": "run.start", "run_id": run.id, "conversation": store.get_conversation(cid)}

    api_key = vault.get(vault.GROQ_KEY) or ""
    if not api_key and not llm.FAKE:
        msg = "Add your Groq API key in Settings to start chatting."
        store.add_message(cid, "assistant", f"⚠ {msg}")
        yield {"type": "error", "message": msg}
        return

    available = hub.enabled_tools(settings)
    if run.unattended:  # a scheduled task must not schedule more tasks
        available = [t for t in available if t.group != "schedule"]
    if run.mode == "plan":  # plan mode may only read
        available = [t for t in available if not t.writes]
    tools = select_tools(available, text, groups, forced_groups)
    specs = [t.spec() for t in tools]
    messages = [{"role": "system", "content": system_prompt(settings, run.mode)}, *prior, {"role": "user", "content": text}]
    answer: list[str] = []
    events: list[dict] = []
    error = None

    try:
        for _ in range(MAX_STEPS):
            turn_text, calls = [], []
            async for kind, value in llm.stream_turn(api_key, settings["model"], messages, specs, settings.get("reasoning_effort", "medium")):
                if run.cancelled:
                    break
                if kind == "delta":
                    turn_text.append(value)
                    answer.append(value)
                    yield {"type": "delta", "text": value}
                elif kind == "status":
                    yield {"type": "status", "text": value}
                elif kind == "tool_calls":
                    calls = value
                elif kind == "usage":
                    store.bump_usage(value.get("total_tokens", 0))
            if run.cancelled or not calls:
                break
            if answer and not answer[-1].endswith("\n"):
                answer.append("\n\n")
                yield {"type": "delta", "text": "\n\n"}

            messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": [
                {"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                call_id = c["id"] or f"call_{i}"
                result = ""
                async for ev in _run_tool(hub, run, settings, call_id, c, events):
                    if ev["type"] == "_result":
                        result = ev["text"]
                    else:
                        yield ev
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result[:TOOL_RESULT_CHARS]})
                if run.cancelled:
                    break
            if run.cancelled:
                break
        else:
            answer.append("\n\n_(Stopped after 8 tool steps.)_")
    except llm.LLMError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001 - never leave the UI hanging
        error = f"Something went wrong: {e}"

    content = "".join(answer).strip()
    if run.cancelled:
        content = (content + "\n\n_[stopped]_").strip()
    if error:
        content = (content + f"\n\n⚠ {error}").strip()
    msg = store.add_message(cid, "assistant", content, events)
    if error:
        yield {"type": "error", "message": error}
    yield {"type": "done", "message": msg, "stopped": run.cancelled, "conversation": store.get_conversation(cid)}


async def _run_tool(hub: MCPHub, run: Run, settings: dict, call_id: str, call: dict, events: list):
    """Execute one tool call, yielding UI events. The last item is {"type": "_result", "text": ...}
    carrying the text that goes back to the model."""
    tool = hub.find(call["name"], settings)
    try:
        args = json.loads(call["arguments"] or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        bad_args = ""
    except ValueError as e:
        args, bad_args = {}, str(e)
    record = {"call_id": call_id, "name": call["name"], "server": tool.server if tool else "", "args": args,
              "status": "running", "ok": False, "preview": "", "ms": 0}

    def finish(status: str, text: str, preview: str | None = None):
        record.update(status=status, preview=(preview if preview is not None else text)[:1500])
        events.append(record)
        return [{"type": "tool.end", **record}, {"type": "_result", "text": text}]

    if not tool:
        for ev in finish("error", f"Error: unknown or disabled tool '{call['name']}'."):
            yield ev
        return
    if bad_args:
        for ev in finish("error", f"Error: invalid JSON arguments ({bad_args}). Call the tool again with valid arguments."):
            yield ev
        return
    gated = needs_approval(tool, args)
    if run.mode == "plan" and (tool.writes or gated):
        for ev in finish("rejected", "Plan mode: this action was not executed. Describe it in the plan instead.",
                         "Not run (plan mode)"):
            yield ev
        return
    if gated and run.mode == "auto":
        record["approved"] = "auto"
        gated = False
    if gated and run.unattended:
        run.approval_needed = True
        for ev in finish("rejected", "This action needs the user's approval, which isn't available in a scheduled run "
                         "in manual mode. Don't retry it; tell the user to run it from chat or set the task to Auto mode.",
                         "Needs approval (scheduled run, manual mode)"):
            yield ev
        return
    if gated:
        fut = asyncio.get_running_loop().create_future()
        run.pending[call_id] = fut
        yield {"type": "confirm.request", "run_id": run.id, "call_id": call_id, "name": tool.name,
               "server": tool.server, "args": args, "description": tool.description.strip().splitlines()[0][:160] if tool.description.strip() else ""}
        try:
            approved = await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            approved = False
        run.pending.pop(call_id, None)
        if not approved:
            for ev in finish("rejected", "The user rejected this action. Do not retry it unless they ask.", "Rejected by you"):
                yield ev
            return
        record["approved"] = True
    yield {"type": "tool.start", **record}
    t0 = time.perf_counter()
    ok, out = await hub.call(tool, args)
    record.update(ok=ok, ms=round((time.perf_counter() - t0) * 1000))
    for ev in finish("done" if ok else "error", out):
        yield ev
