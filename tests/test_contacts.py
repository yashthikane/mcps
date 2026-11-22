"""Contacts, policies, working hours, the SQLite migration, the headless runtime, task delivery and the
dashboard API for contacts / approvals / memories."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from donna import contacts, llm, permissions
from donna.store import SCHEMA, Store


# ------------------------------------------------------------------------------ contacts
def test_contacts_default_to_disabled_with_general_help_only(store):
    c = contacts.create(store, "Rahul")
    assert c["mode"] == "disabled" and not c["discovered"]
    assert contacts.effective_capabilities(store, c["id"], "whatsapp") == permissions.CONTACT_DEFAULTS
    with pytest.raises(contacts.ContactError):
        contacts.create(store, "  ")
    with pytest.raises(contacts.ContactError):
        contacts.update(store, c["id"], {"mode": "god"})


def test_channel_policy_overrides_the_default(store):
    c = contacts.create(store, "Rahul", mode="auto")
    contacts.set_policy(store, c["id"], "", {"calendar.availability": "on"})
    contacts.set_policy(store, c["id"], "telegram", {"calendar.availability": "off"})
    assert contacts.effective_capabilities(store, c["id"], "whatsapp")["calendar.availability"] == "on"
    assert contacts.effective_capabilities(store, c["id"], "telegram")["calendar.availability"] == "off"
    with pytest.raises(contacts.ContactError):
        contacts.set_policy(store, c["id"], "", {"root.access": "on"})
    with pytest.raises(contacts.ContactError):
        contacts.set_policy(store, c["id"], "", {"email.send": "yes"})


def test_identities_are_unique_and_one_owner_at_a_time(store):
    a, b = contacts.create(store, "A"), contacts.create(store, "B")
    contacts.add_identity(store, a["id"], "whatsapp", "1@c.us")
    with pytest.raises(contacts.ContactError, match="already belongs"):
        contacts.add_identity(store, b["id"], "whatsapp", "1@c.us")
    contacts.set_owner(store, a["id"])
    contacts.set_owner(store, b["id"])
    assert contacts.owner(store)["id"] == b["id"] and not contacts.get(store, a["id"])["is_owner"]


def test_working_hours_in_the_contacts_timezone():
    c = {"working_hours": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "20:00"}, "timezone": "Asia/Kolkata"}
    ist = ZoneInfo("Asia/Kolkata")
    assert contacts.in_working_hours(c, "UTC", datetime(2026, 10, 5, 10, 0, tzinfo=ist))      # Monday 10:00
    assert not contacts.in_working_hours(c, "UTC", datetime(2026, 10, 5, 21, 0, tzinfo=ist))  # Monday 21:00
    assert not contacts.in_working_hours(c, "UTC", datetime(2026, 10, 4, 10, 0, tzinfo=ist))  # Sunday
    assert contacts.in_working_hours({"working_hours": {}}, "UTC")
    night = {"working_hours": {"days": list(range(7)), "start": "22:00", "end": "06:00"}}
    assert contacts.in_working_hours(night, "UTC", datetime(2026, 10, 5, 23, 30, tzinfo=ZoneInfo("UTC")))


def test_confirm_tools_are_never_plain_on_for_contacts():
    from donna.hub import Tool
    t = Tool(name="send_email", remote="send_email", server="donna", group="gmail", description="", schema={}, confirm=True, writes=True)
    ctx = permissions.TurnContext(audience="contact", contact={"id": "c"}, capabilities={"email.send": "on"})
    assert permissions.decide(ctx, t) == "approve"
    ctx.suggest_only = True
    assert permissions.decide(ctx, t) == "deny"


# ------------------------------------------------------------------------------ migration
def test_an_existing_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO conversations VALUES ('c1', 'Old chat', 0, 1, 1)")
    db.commit()
    db.close()
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] >= 1
    assert store.list_conversations()[0]["source"] == "web"
    assert store.rows("SELECT COUNT(*) AS n FROM contacts")[0]["n"] == 0
    Store(path)  # reopening doesn't re-apply


# ------------------------------------------------------------------------------ headless runtime
async def test_run_agent_turn_without_a_browser(store, monkeypatch):
    monkeypatch.setattr(llm, "FAKE", True)
    import server  # noqa: F401
    from donna.hub import MCPHub
    from donna.runtime import run_agent_turn
    from mcp_instance import mcp
    hub = MCPHub(store, mcp)
    await hub.start()
    try:
        cid = store.create_conversation()["id"]
        res = await run_agent_turn(store, hub, cid, "square of 7")
        assert res.error is None and "49" in res.text and res.tool_events[0]["name"] == "square"
        # an owner turn on a channel turns a gated action into an approval instead of waiting
        ctx = permissions.TurnContext(audience="owner", channel_id="whatsapp", channel_label="WhatsApp")
        res = await run_agent_turn(store, hub, cid, "create event Prep", ctx)
        assert len(res.approvals) == 1 and res.tool_events[-1]["status"] == "pending"
        res = await run_agent_turn(store, hub, cid, "create event Prep", permissions.TurnContext(audience="owner", channel_id="x"),
                                   mode="auto")
        assert res.approvals == [] and res.tool_events[-1].get("approved") == "auto"
    finally:
        await hub.stop()


async def test_task_delivery_to_a_contact_needs_their_initiate_permission(store):
    from donna import tasks
    svc = tasks.TaskService(None, None, None, store, None)
    c = contacts.create(store, "Rahul", mode="auto")
    assert "doesn't allow" in svc._contact_problem(c["id"])
    contacts.set_policy(store, c["id"], "", {"initiate": "on"})
    assert svc._contact_problem(c["id"]) is None
    contacts.update(store, c["id"], {"mode": "disabled"})
    assert "doesn't allow" in svc._contact_problem(c["id"])


# ------------------------------------------------------------------------------ dashboard API
def test_contacts_approvals_and_memories_api(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "FAKE", True)
    from donna.app import app
    app.state.db_path = tmp_path / "api.db"
    with TestClient(app) as c:
        caps = {x["id"]: x for x in c.get("/api/v1/capabilities").json()}
        assert caps["email.send"]["forced_approval"] and not caps["calendar.availability"]["forced_approval"]
        r = c.post("/api/v1/contacts", json={"display_name": "Rahul", "mode": "auto", "channel_id": "whatsapp",
                                             "address": "+91 98123 45678"})
        assert r.status_code == 200, r.text
        rahul = r.json()
        assert rahul["identities"][0]["external_id"] == "919812345678@c.us"
        assert c.post("/api/v1/contacts", json={"display_name": "X", "channel_id": "whatsapp", "address": "12"}).status_code == 400
        assert c.patch(f"/api/v1/contacts/{rahul['id']}", json={"mode": "nope"}).status_code == 400
        assert c.put(f"/api/v1/contacts/{rahul['id']}/policies",
                     json={"channel_id": "", "capabilities": {"calendar.availability": "on"}}).status_code == 200
        assert c.put(f"/api/v1/contacts/{rahul['id']}/policies", json={"capabilities": {"bad": "on"}}).status_code == 400
        assert c.post(f"/api/v1/contacts/{rahul['id']}/memories", json={"content": "Prefers mornings"}).status_code == 200
        assert c.get(f"/api/v1/contacts/{rahul['id']}/memories").json()[0]["content"] == "Prefers mornings"
        assert c.post("/api/v1/memories", json={"content": "I'm vegetarian"}).status_code == 200
        assert c.get("/api/v1/memories").json()[0]["scope"] == "global"
        assert c.post("/api/v1/approvals/ZZZZ/approve", json={}).status_code == 409
        assert c.get("/api/v1/inbox/counts").json() == {"unread": 0, "escalated": 0, "failed": 0, "approvals": 0, "requests": 0}
        chans = {x["id"]: x for x in c.get("/api/v1/channels").json()}
        assert chans["whatsapp"]["status"] == "not_set_up" and chans["telegram"]["credentials"] is False
        assert c.put("/api/v1/channels/telegram/token", json={"token": "nope"}).status_code == 400
        s = c.put("/api/v1/settings", json={"messaging_enabled": False, "contact_rate_per_hour": 5})
        assert s.status_code == 200 and s.json()["messaging_enabled"] is False
        assert c.put("/api/v1/settings", json={"openwa_url": "http://evil.example:2785"}).status_code == 400
        assert c.get("/api/v1/audit").json()[0]["action"] in ("settings.switches", "contact.policy")
        assert c.delete(f"/api/v1/contacts/{rahul['id']}").status_code == 200
        assert c.get("/api/v1/contacts").json() == []
