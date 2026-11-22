"""Running Donna without a browser: one call, one result.

`run_agent_turn` drives the same agent.run_chat the web chat streams, and collects its events into
an AgentResult. Channels, scheduled tasks and approval follow-ups all go through it, so there is
exactly one agent runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .agent import RUNS, Run, run_chat
from .permissions import TurnContext


@dataclass
class AgentResult:
    text: str = ""
    error: str | None = None
    tool_events: list = field(default_factory=list)
    approvals: list = field(default_factory=list)   # approval codes created during the turn
    approval_needed: bool = False                   # unattended manual run hit a gated tool
    stopped: bool = False
    busy: bool = False                              # another run is using this conversation
    message: dict | None = None


def busy(conversation_id: str) -> bool:
    return any(r.conversation_id == conversation_id for r in RUNS.values())


async def run_agent_turn(store, hub, conversation_id: str, text: str, ctx: TurnContext | None = None, *,
                         mode: str = "manual", unattended: bool = False, forced_groups: set[str] | None = None) -> AgentResult:
    if busy(conversation_id):
        return AgentResult(error="That conversation is busy.", busy=True)
    run = Run(conversation_id, unattended=unattended, mode=mode, ctx=ctx)
    RUNS[run.id] = run
    result = AgentResult()
    try:
        async for ev in run_chat(store, hub, run, text, forced_groups or set()):
            if ev["type"] == "error":
                result.error = ev["message"]
            elif ev["type"] == "done":
                result.message = ev["message"]
                result.text = ev["message"]["content"]
                result.tool_events = ev["message"]["tool_events"]
                result.stopped = ev["stopped"]
    finally:
        RUNS.pop(run.id, None)
    result.approval_needed = run.approval_needed
    result.approvals = list(ctx.approvals) if ctx else []
    if result.error and result.text.endswith(f"⚠ {result.error}"):
        result.text = result.text[: -len(f"⚠ {result.error}")].strip()  # the error is reported separately
    return result
