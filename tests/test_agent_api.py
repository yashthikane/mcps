"""Agent loop and HTTP API, using the real in-process MCP server and the scripted fake model."""
import asyncio
import json

import pytest

from donna import agent, llm


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", True)


@pytest.fixture
async def hub(store):
    import server  # noqa: F401 - registers tools
    from donna.hub import MCPHub
    from mcp_instance import mcp
    h = MCPHub(store, mcp)
    await h.start()
    yield h
    await h.stop()


async def collect(gen, on_event=None):
    events = []
    async for ev in gen:
        events.append(ev)
        if on_event:
            await on_event(ev)
    return events


def test_select_tools_routes_by_keywords():
    from donna.hub import Tool
    mk = lambda n, g: Tool(n, n, "donna", g, "", {}, False)  # noqa: E731
    tools = [mk("get_weather", "weather"), mk("list_emails", "gmail"), mk("create_page", "notion")]
    assert [t.name for t in agent.select_tools(tools, "weather in Pune?", set(), set())] == ["get_weather"]
    assert {t.name for t in agent.select_tools(tools, "email Priya and add notes to Notion", set(), set())} == {"list_emails", "create_page"}
    assert len(agent.select_tools(tools, "hello there", set(), set())) == 3  # nothing matched → all
    assert [t.name for t in agent.select_tools(tools, "hello", set(), {"gmail"})] == ["list_emails"]
    # recent connections join a matching message, but don't narrow an unrelated one
    assert {t.name for t in agent.select_tools(tools, "and the weather?", {"gmail"}, set())} == {"get_weather", "list_emails"}
    assert len(agent.select_tools(tools, "list folders you can access", {"gmail"}, set())) == 3
    fs = Tool("files__list_directory", "list_directory", "srv1", "srv1", "", {}, False)
    assert agent.select_tools(tools + [fs], "which folders can you see", set(), set()) == [fs]


async def test_tool_call_streams_and_saves(store, hub, fake_llm):
    conv = store.create_conversation()
    run = agent.Run(conv["id"])
    events = await collect(agent.run_chat(store, hub, run, "what is the square of 7", set()))
    types = [e["type"] for e in events]
    assert types[0] == "run.start" and types[-1] == "done"
    end = next(e for e in events if e["type"] == "tool.end")
    assert end["name"] == "square" and end["ok"] and end["preview"] == "49"
    assert "49" in "".join(e["text"] for e in events if e["type"] == "delta")
    msgs = store.messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["tool_events"][0]["name"] == "square"
    assert store.get_conversation(conv["id"])["title"] == "What is the square of 7"


@pytest.mark.parametrize("approve", [False, True])
async def test_confirmation_gate(store, hub, fake_llm, approve):
    conv = store.create_conversation()
    run = agent.Run(conv["id"])

    async def decide(ev):
        if ev["type"] == "confirm.request":
            assert ev["name"] == "create_event" and ev["args"]["summary"] == "Prep"
            asyncio.get_running_loop().call_later(0.05, run.decide, ev["call_id"], approve)

    events = await collect(agent.run_chat(store, hub, run, "create event Prep tomorrow", set()), decide)
    end = next(e for e in events if e["type"] == "tool.end")
    if approve:
        assert any(e["type"] == "tool.start" for e in events)
        assert end["status"] == "error" and "Google isn't connected" in end["preview"]  # no credentials in tests
    else:
        assert not any(e["type"] == "tool.start" for e in events)
        assert end["status"] == "rejected"


async def test_cancel_stops_streaming(store, hub, fake_llm):
    conv = store.create_conversation()
    run = agent.Run(conv["id"])
    seen = 0

    async def stop_early(ev):
        nonlocal seen
        if ev["type"] == "delta":
            seen += 1
            if seen == 5:
                run.cancel()

    events = await collect(agent.run_chat(store, hub, run, "say something slow", set()), stop_early)
    assert events[-1]["type"] == "done" and events[-1]["stopped"] is True
    assert seen < 20
    assert store.messages(conv["id"])[-1]["content"].endswith("_[stopped]_")


async def test_missing_groq_key_is_explained(store, hub):
    conv = store.create_conversation()
    events = await collect(agent.run_chat(store, hub, agent.Run(conv["id"]), "hi", set()))
    assert events[-1]["type"] == "error" and "Groq API key" in events[-1]["message"]


def test_http_api(tmp_path, fake_llm):
    from fastapi.testclient import TestClient
    from donna.app import app
    app.state.db_path = tmp_path / "api.db"
    with TestClient(app) as c:
        conv = c.post("/api/v1/conversations").json()
        with c.stream("POST", f"/api/v1/conversations/{conv['id']}/messages", json={"text": "square of 12"}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert events[-1]["type"] == "done"
        assert any(e["type"] == "tool.end" and e["preview"] == "144" for e in events)

        assert c.patch(f"/api/v1/conversations/{conv['id']}", json={"pinned": True, "title": "Maths"}).json()["pinned"] is True
        assert [x["title"] for x in c.get("/api/v1/conversations", params={"q": "144"}).json()] == ["Maths"]
        assert len(c.get(f"/api/v1/conversations/{conv['id']}/messages").json()) == 2

        conns = {x["id"]: x for x in c.get("/api/v1/connections").json()}
        assert conns["gmail"]["state"] == "not_set_up" and conns["weather"]["state"] == "connected"
        assert len(conns["gmail"]["tools"]) == 12 and len(conns["calendar"]["tools"]) == 5 and len(conns["notion"]["tools"]) == 8
        c.patch("/api/v1/connections/weather", json={"enabled": False})
        assert {x["id"]: x for x in c.get("/api/v1/connections").json()}["weather"]["state"] == "off"

        assert c.put("/api/v1/settings", json={"timezone": "Mars/Base"}).status_code == 400
        assert c.put("/api/v1/settings", json={"timezone": "Asia/Kolkata"}).json()["timezone"] == "Asia/Kolkata"
        assert c.put("/api/v1/settings/groq-key", json={"key": "bad"}).status_code == 400
        ok = c.put("/api/v1/settings/groq-key", json={"key": "gsk_test_1234"}).json()
        assert ok["hint"] == "…1234" and c.get("/api/v1/settings").json()["groq_key_set"] is True

        assert c.post("/api/v1/connections/notion", json={"token": "short"}).status_code == 400
        assert c.post("/api/v1/connections/notion", json={"token": "ntn_" + "a" * 40}).json()["pages"]
        bad = c.post("/api/v1/connections/google/credentials", files={"file": ("credentials.json", b"{}", "application/json")})
        assert bad.status_code == 400 and "OAuth client" in bad.json()["detail"]

        assert c.post("/api/v1/mcp-servers/test", json={"name": "x", "transport": "stdio", "command": ""}).status_code == 400
        assert c.post("/api/v1/conversations", headers={"Origin": "https://evil.example"}).status_code == 403

        export = c.get("/api/v1/export")
        assert "attachment" in export.headers["content-disposition"] and export.json()["conversations"]
        assert c.post("/api/v1/wipe").json()["deleted"] == 1
        assert c.get("/api/v1/conversations").json() == []
