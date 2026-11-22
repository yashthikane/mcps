"""The Claude Code provider: Donna drives the official `claude -p` CLI (signed in with the owner's own
subscription; Donna never stores that sign-in). Claude Code's tools are off; it returns a reply and at most
one of Donna's tools, which Donna runs through her usual loop and permissions. The CLI is simulated here."""
import json
import subprocess

import pytest

from donna import llm


class FakeCLI:
    """Stands in for subprocess.run(["claude", "-p", ...]): answers each call from a script."""

    def __init__(self, steps):
        self.steps, self.calls = list(steps), []

    def __call__(self, args, input=None, **kw):  # noqa: A002 - mirrors subprocess.run
        self.calls.append({"args": args, "prompt": input, "cwd": kw.get("cwd")})
        step = self.steps.pop(0)
        if isinstance(step, tuple):  # (returncode, stdout, stderr)
            return subprocess.CompletedProcess(args, step[0], step[1], step[2])
        out = {"is_error": False, "subtype": "success", "structured_output": step,
               "usage": {"input_tokens": 10, "output_tokens": 5}}
        return subprocess.CompletedProcess(args, 0, json.dumps(out), "")


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", False)
    monkeypatch.setattr(llm, "claude_cli", lambda: "claude")

    def install(steps):
        fake = FakeCLI(steps)
        monkeypatch.setattr(llm.subprocess, "run", fake)
        return fake
    return install


@pytest.fixture
async def hub(store):
    import server  # noqa: F401
    from donna.hub import MCPHub
    from mcp_instance import mcp
    h = MCPHub(store, mcp)
    await h.start()
    yield h
    await h.stop()


async def test_a_turn_runs_donnas_tool_then_answers(cli, store, hub):
    from donna.runtime import run_agent_turn
    store.set_settings(llm_provider="claude_code", claude_model="opus")
    fake = cli([{"reply": "", "tool_call": {"name": "square", "arguments": {"n": 7}}},
                {"reply": "7 squared is 49.", "tool_call": None}])
    cid = store.create_conversation()["id"]
    res = await run_agent_turn(store, hub, cid, "what's the square of 7?")
    assert res.error is None and res.text == "7 squared is 49."
    assert res.tool_events[0]["name"] == "square" and res.tool_events[0]["status"] == "done"
    first, second = fake.calls
    args = first["args"]
    assert args[:2] == ["claude", "-p"] and args[args.index("--tools") + 1] == "" and args[args.index("--model") + 1] == "opus"
    assert "--no-session-persistence" in args and "--json-schema" in args
    assert "AVAILABLE TOOLS:" in first["prompt"] and "- square:" in first["prompt"]
    assert "[You called square]" in second["prompt"] and "[Result of square] 49" in second["prompt"]
    assert "HOW YOU ACT" in args[args.index("--system-prompt") + 1]
    assert "mcps" not in (first["cwd"] or "").split("\\")[-1]  # a neutral folder, not the repo (no CLAUDE.md)


async def test_permissions_still_apply_to_claude_code_tool_calls(cli, store, hub):
    """Claude choosing a tool doesn't bypass Donna: an approval-gated tool still waits for the owner."""
    from donna.permissions import TurnContext
    from donna.runtime import run_agent_turn
    store.set_settings(llm_provider="claude_code")
    cli([{"reply": "", "tool_call": {"name": "create_event",
                                     "arguments": {"summary": "Prep", "start_time": "2026-10-01T09:00", "end_time": "2026-10-01T09:30"}}},
         {"reply": "Sent for your approval.", "tool_call": None}])
    cid = store.create_conversation()["id"]
    res = await run_agent_turn(store, hub, cid, "create an event", TurnContext(audience="owner", channel_id="whatsapp"))
    assert len(res.approvals) == 1 and res.tool_events[0]["status"] == "pending"


async def test_cli_errors_become_readable_messages(cli, store):
    cli([(1, json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"}), "")])
    with pytest.raises(llm.LLMError, match="isn't signed in"):
        async for _ in llm.claude_code_turn("opus", [{"role": "user", "content": "hi"}], []):
            pass
    cli([(1, "", "Claude usage limit reached. Resets at 5pm")])
    with pytest.raises(llm.LLMError, match="usage limit"):
        async for _ in llm.claude_code_turn("opus", [{"role": "user", "content": "hi"}], []):
            pass


def test_provider_choice_and_readiness(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", False)
    assert llm.provider({}) == "groq" and llm.provider({"llm_provider": "claude_code"}) == "claude_code"
    assert llm.provider({"llm_provider": "something"}) == "groq"
    monkeypatch.setattr(llm, "claude_cli", lambda: None)
    assert "isn't installed" in llm.problem({"llm_provider": "claude_code"})
    assert "Groq API key" in llm.problem({"llm_provider": "groq"})  # no key in the test keyring


def test_long_system_prompts_go_through_stdin():
    system, prompt = llm._render([{"role": "system", "content": "x" * 30000}, {"role": "user", "content": "hi"}], [])
    assert len(system) > llm._ARG_LIMIT and "[User] hi" in prompt  # claude_code_turn moves it into the prompt
