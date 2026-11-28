"""Documents (create_document), news, Google Drive, Jira, and where scheduled results go."""
import json

import httpx
import pytest

from donna import agent, contacts, files, jira, llm, permissions, vault

REAL_CLIENT = httpx.AsyncClient
SITE, EMAIL, TOKEN = "edtr.atlassian.net", "me@example.com", "ATATT" + "x" * 30


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


# ------------------------------------------------------------------------------ documents
def test_pdf_and_markdown_are_saved_with_readable_names():
    md = "## Goals\n- **Login** (EDTR-142): ₹ 4,500\n\n| Task | Owner |\n|---|---|\n| EDTR-142 | Priya |\n\nहिंदी: यह परीक्षण है।"
    name, size, pages = files.create("EDTR sprint notes", md, "pdf")
    assert files.NAME.fullmatch(name) and name.endswith("-edtr-sprint-notes.pdf") and pages == 1
    data = files.path(name).read_bytes()
    assert data.startswith(b"%PDF") and size == len(data)
    assert files.download_name(name) == "edtr-sprint-notes.pdf"

    md_name, _, md_pages = files.create("Notes", "- one", "markdown")
    assert md_name.endswith(".md") and md_pages == 0
    assert files.path(md_name).read_text(encoding="utf-8") == "# Notes\n\n- one\n"


def test_document_errors_and_path_safety():
    for title, content, fmt, msg in (("x", "", "pdf", "empty"), ("x", "a", "docx", "Unknown format"),
                                     ("x", "a" * (files.MAX_CHARS + 1), "pdf", "too long")):
        with pytest.raises(files.FileError, match=msg):
            files.create(title, content, fmt)
    assert files.path("../api.db") is None and files.path("doc_000000000000.pdf") is None
    assert files.download_name("doc_0123456789ab.pdf") == "donna-document.pdf"


async def test_chat_makes_a_pdf_but_not_in_plan_mode(store, hub, fake_llm):
    async def run(mode="manual"):
        conv = store.create_conversation()
        return [e async for e in agent.run_chat(store, hub, agent.Run(conv["id"], mode=mode), "make a pdf of sprint notes", set())]
    end = next(e for e in await run() if e["type"] == "tool.end")
    assert end["name"] == "create_document" and end["ok"] and files.path(files.NAME.search(end["preview"]).group(0))
    assert not any(e["type"].startswith("tool.") for e in await run(mode="plan"))


def test_file_route_serves_inline_and_as_download(tmp_path, fake_llm):
    from fastapi.testclient import TestClient
    from donna.app import app
    app.state.db_path = tmp_path / "api.db"
    name, _, _ = files.create("Sprint notes", "- one", "pdf")
    with TestClient(app) as c:
        r = c.get(f"/api/v1/files/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].startswith("inline") and "sprint-notes.pdf" in r.headers["content-disposition"]
        assert c.get(f"/api/v1/files/{name}?download=1").headers["content-disposition"].startswith("attachment")
        assert c.get("/api/v1/files/doc_000000000000.pdf").status_code == 404
        conns = {x["id"]: x for x in c.get("/api/v1/connections").json()}
        assert conns["files"]["state"] == "connected" and conns["news"]["state"] == "connected"
        assert conns["jira"]["state"] == "not_set_up" and conns["jira"]["setup"] == "jira"


# ------------------------------------------------------------------------------ news
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Old story - Mint</title><source>Mint</source><link>https://n/1</link><pubDate>Mon, 24 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>New story - The Verge</title><source>The Verge</source><link>https://n/2</link><pubDate>Thu, 27 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_feed_is_parsed_newest_first_and_entities_are_refused():
    from tools.news_tools import parse
    items = parse(RSS, 5)
    assert [i["title"] for i in items] == ["New story", "Old story"] and items[0]["source"] == "The Verge"
    with pytest.raises(ValueError):
        parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss/>', 5)


async def test_get_news_groups_by_topic(monkeypatch):
    from tools import news_tools
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, content=RSS)
    monkeypatch.setattr(news_tools.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    fn = getattr(news_tools.get_news, "fn", news_tools.get_news)
    out = await fn("AI, edtech", 1)
    lines = out.splitlines()
    assert lines[0] == "## AI" and lines[1].startswith("- New story (The Verge, ") and lines[2] == "## edtech"
    assert lines[3].startswith("- Old story (Mint, ")  # a story shown under one topic isn't repeated under the next
    assert "q=AI+when%3A1d" in asked[0] and "gl=IN" in asked[0]


# ------------------------------------------------------------------------------ Google Drive
async def test_drive_needs_its_own_permission():
    from tools import drive_tools
    from tools.google_auth import NO_DRIVE
    vault.set(vault.GOOGLE_TOKEN, json.dumps({"refresh_token": "r", "scopes": ["https://www.googleapis.com/auth/calendar"]}))
    name, _, _ = files.create("Plan", "- one", "pdf")
    fn = getattr(drive_tools.upload_to_drive, "fn", drive_tools.upload_to_drive)
    assert await fn(name) == f"Error: {NO_DRIVE}"
    assert (await fn("doc_000000000000.pdf")).startswith("Error: no such file")
    assert drive_tools._source(name)[1:] == ("application/pdf", "plan.pdf")


# ------------------------------------------------------------------------------ Jira
class FakeJira:
    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if request.headers.get("authorization", "").split()[-1] != httpx.BasicAuth(EMAIL, TOKEN)._auth_header.split()[-1]:
            return httpx.Response(401)
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"displayName": "Yashraj"})
        if path == "/rest/api/3/project/search":
            return httpx.Response(200, json={"values": [{"key": "EDTR", "name": "Edtech"}, {"key": "OPS", "name": "Ops"}]})
        if path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [
                {"key": "EDTR-142", "fields": {"summary": "Login revamp", "status": {"name": "In Review"},
                                               "assignee": {"displayName": "Priya"}, "updated": "2026-11-27T10:00:00"}},
                {"key": "OPS-9", "fields": {"summary": "Secret", "status": {"name": "To Do"}}}]})
        if path == "/rest/api/3/issue/EDTR-142":
            return httpx.Response(200, json={"key": "EDTR-142", "fields": {
                "summary": "Login revamp", "status": {"name": "In Review"}, "assignee": {"displayName": "Priya"},
                "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "OTP flow"}]}]},
                "subtasks": [{"key": "EDTR-143", "fields": {"summary": "Android", "status": {"name": "Done", "statusCategory": {"key": "done"}}}},
                             {"key": "EDTR-144", "fields": {"summary": "iOS", "status": {"name": "To Do", "statusCategory": {"key": "new"}}}}],
                "comment": {"comments": [{"created": "2026-11-27T13:40:00", "author": {"displayName": "Priya"},
                                          "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "waiting on QA"}]}]}}]}}})
        if path == "/rest/api/3/issue/EDTR-142/comment":
            return httpx.Response(201, json={"id": "1"})
        if path == "/rest/api/3/issue/EDTR-142/transitions":
            if request.method == "GET":
                return httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done", "to": {"name": "Done"}}]})
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def jira_api(monkeypatch):
    api = FakeJira()
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(api), **kw))
    vault.set(vault.JIRA_SITE, SITE)
    vault.set(vault.JIRA_EMAIL, EMAIL)
    vault.set(vault.JIRA_TOKEN, TOKEN)
    return api


def test_site_and_project_keys_are_checked():
    assert jira.normalize_site("https://EDTR.atlassian.net/jira/software") == "edtr.atlassian.net"
    for bad in ("evil.com", "edtr.atlassian.net.evil.com", "127.0.0.1"):
        with pytest.raises(jira.JiraError):
            jira.normalize_site(bad)
    assert jira.projects_of("edtr, ops, bad key, 1X") == ["EDTR", "OPS"]


async def test_issue_details_comment_and_move(jira_api):
    text = await jira.issue("edtr-142")
    assert "EDTR-142 · Login revamp | status: In Review | assignee: Priya" in text
    assert "OTP flow" in text and "Subtasks (1 of 2 done)" in text and "Priya: waiting on QA" in text
    assert await jira.comment("EDTR-142", "QA tomorrow") == "Comment added to EDTR-142."
    body = jira_api.requests[-1][2]["body"]
    assert body["type"] == "doc" and body["content"][0]["content"][0]["text"] == "QA tomorrow"
    assert await jira.move("EDTR-142", "done") == "EDTR-142 moved to Done."
    with pytest.raises(jira.JiraError, match="can move to: Done"):
        await jira.move("EDTR-142", "Blocked")


async def test_shared_projects_limit_what_a_search_can_reach(jira_api):
    found = await jira.search("login", jql="project = OPS) OR (project = OPS", allowed=["EDTR"])
    assert [i["key"] for i in found] == ["EDTR-142"]  # OPS-9 came back but is dropped
    sent = jira_api.requests[-1][2]["jql"]
    assert sent.startswith("project in (EDTR) AND text ~ \"login\"") and "OPS" not in sent  # raw JQL never runs
    with pytest.raises(jira.JiraError, match="isn't shared"):
        await jira.issue("OPS-9", ["EDTR"])
    with pytest.raises(jira.JiraError, match="isn't one of the projects"):
        await jira.search(project="OPS", allowed=["EDTR"])
    owner = await jira.search(jql="updated >= -7d")
    assert {i["key"] for i in owner} == {"EDTR-142", "OPS-9"} and "(updated >= -7d)" in jira_api.requests[-1][2]["jql"]


def test_jira_connection_routes(tmp_path, fake_llm, jira_api):
    from fastapi.testclient import TestClient
    from donna.app import app
    app.state.db_path = tmp_path / "api.db"
    for k in (vault.JIRA_SITE, vault.JIRA_EMAIL, vault.JIRA_TOKEN):
        vault.delete(k)
    with TestClient(app) as c:
        assert c.post("/api/v1/connections/jira", json={"site": "evil.com", "email": EMAIL, "token": TOKEN}).status_code == 400
        assert c.post("/api/v1/connections/jira", json={"site": SITE, "email": EMAIL, "token": "ATATT" + "y" * 30}).status_code == 400
        assert not jira.connected()  # a rejected token is never saved
        ok = c.post("/api/v1/connections/jira", json={"site": "https://edtr.atlassian.net", "email": EMAIL, "token": TOKEN}).json()
        assert ok["name"] == "Yashraj" and [p["key"] for p in ok["projects"]] == ["EDTR", "OPS"] and jira.connected()
        assert c.put("/api/v1/settings", json={"jira_projects": ["edtr", "not valid"]}).json()["jira_projects"] == ["EDTR"]
        assert c.get("/api/v1/jira/projects").json()["shared"] == ["EDTR"]
        conn = {x["id"]: x for x in c.get("/api/v1/connections").json()}["jira"]
        assert conn["state"] == "connected" and "EDTR" in conn["detail"]
        c.delete("/api/v1/connections/jira")
        assert not jira.connected()


# ------------------------------------------------------------------------------ Jira for contacts (enforced in code)
def _contact(store, **caps):
    c = contacts.create(store, "Jyotiraditya", mode="auto")
    contacts.set_policy(store, c["id"], "", caps)
    ctx = permissions.TurnContext(audience="contact", channel_id="whatsapp", contact=contacts.get(store, c["id"]),
                                  capabilities=contacts.effective_capabilities(store, c["id"], "whatsapp"))
    return ctx


async def _call(store, hub, ctx, name, args):
    conv = store.create_conversation()
    run = agent.Run(conv["id"], ctx=ctx)
    events = []
    out = [e async for e in agent._run_tool(store, hub, run, store.settings(), "c1", {"name": name, "arguments": json.dumps(args)}, events)]
    return events[-1], out[-1]["text"]


async def test_contacts_only_reach_shared_projects(store, hub, jira_api):
    ctx = _contact(store, **{"jira.read": "on", "jira.comment": "approval"})
    record, text = await _call(store, hub, ctx, "get_jira_issue", {"key": "EDTR-142"})
    assert record["status"] == "rejected" and "No Jira projects are shared" in text  # nothing shared yet

    store.set_settings(jira_projects=["EDTR"])
    record, text = await _call(store, hub, ctx, "search_jira_issues", {"query": "login", "jql": "project = OPS", "projects": "OPS"})
    assert record["args"]["projects"] == "EDTR" and record["args"]["jql"] == "" and "OPS-9" not in text and "EDTR-142" in text
    record, text = await _call(store, hub, ctx, "get_jira_issue", {"key": "OPS-9", "projects": "OPS"})
    assert record["status"] == "error" and "isn't shared" in text

    record, text = await _call(store, hub, ctx, "add_jira_comment", {"key": "EDTR-142", "text": "QA can start tomorrow"})
    assert record["status"] == "pending" and "approval" in text  # a contact's comment always waits for the owner
    assert record["args"]["text"].endswith("(added by Donna for Jyotiraditya)")
    assert not any(r[1].endswith("/comment") for r in jira_api.requests)
    assert permissions.forced_approval("jira.comment", __import__("donna.hub", fromlist=["CONFIRM"]).CONFIRM)


async def test_jira_tools_are_hidden_without_the_permission(store, hub, jira_api):
    ctx = _contact(store)
    store.set_settings(jira_projects=["EDTR"])
    record, text = await _call(store, hub, ctx, "get_jira_issue", {"key": "EDTR-142"})
    assert record["status"] == "rejected" and "Not permitted" in text


# ------------------------------------------------------------------------------ where scheduled results go
class FakeService:
    def __init__(self, store):
        self.store = store


def test_deliver_to_maps_to_channels_or_a_contact(store):
    from donna.tasks import TaskError
    from tools.schedule_tools import _deliver
    svc = FakeService(store)
    owner = contacts.create(store, "Me", mode="auto")
    contacts.set_owner(store, owner["id"])
    rahul = contacts.create(store, "Rahul Sharma", mode="auto")
    contacts.create(store, "Rahul Verma", mode="auto")
    assert _deliver(svc, "") == {} and _deliver(svc, "web") == {}
    assert _deliver(svc, "my WhatsApp") == {"notification_channels": ["owner_chat", "in_app"]}
    assert _deliver(svc, "Me") == {"notification_channels": ["owner_chat", "in_app"]}
    assert _deliver(svc, "rahul sharma") == {"deliver_contact_id": rahul["id"]}
    with pytest.raises(TaskError, match="Rahul Sharma, Rahul Verma|Rahul Verma, Rahul Sharma"):
        _deliver(svc, "Rahul")
    with pytest.raises(TaskError, match="no contact"):
        _deliver(svc, "Nobody")


def test_contacts_cant_schedule_messages_to_others(store):
    from donna.hub import Tool
    ctx = _contact(store, **{"tasks.create": "on"})
    tool = Tool("schedule_task", "schedule_task", "donna", "schedule", "", {}, False, True)
    assert permissions.decide(ctx, tool, {"kind": "reminder"}) == "allow"
    assert permissions.decide(ctx, tool, {"kind": "reminder", "deliver_to": "Rahul"}) == "deny"
