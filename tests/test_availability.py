"""Availability (your status), the away greeting + quick menu, guided flows, and the meeting follow-ups:
status precedence, once-per-period greetings for saved contacts only, menu choices by poll vote or number,
owner commands from the phone, urgent pings in DND/Sleeping, and a contact cancelling their own meeting."""
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_channels import FakeChannel, contact, count_llm_calls, deliver, fake_llm, hub, incoming, mgr  # noqa: F401

from donna import availability, contact_requests, contacts, runtime_tools
from donna.permissions import TurnContext

NOW = datetime(2026, 11, 24, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()  # a Tuesday, 3 PM
SETTINGS = {"timezone": "Asia/Kolkata", "owner_name": "Yashraj", "calendar_auto_status": True}


def away(store, mode="sleeping", detail="", minutes=60, set_at=None):
    store.set_settings(require_trigger=True, calendar_auto_status=False,
                       availability={"mode": mode, "detail": detail, "until": time.time() + minutes * 60,
                                     "set_at": set_at or time.time()})


def conv_of(store):
    return store.row("SELECT * FROM channel_conversations WHERE channel_id='fake' ORDER BY created_at DESC LIMIT 1")


# ------------------------------------------------------------------------------ the status itself
def test_status_precedence_manual_then_calendar_then_sleep_then_available():
    s = {**SETTINGS, "sleep_hours": {"enabled": True, "start": "14:00", "end": "16:00"}}
    manual = {"mode": "working", "detail": "the Q4 launch", "until": NOW + 3600, "set_at": 5}
    assert availability.compute({**s, "availability": manual}, NOW, {"id": "e1", "end": NOW + 600})["mode"] == "working"
    expired = {**manual, "until": NOW - 1}
    st = availability.compute({**s, "availability": expired}, NOW, {"id": "e1", "end": NOW + 600})
    assert st["mode"] == "meeting" and st["source"] == "calendar" and st["key"] == "c:e1"
    st = availability.compute({**s, "availability": expired}, NOW, None)
    assert st["mode"] == "sleeping" and st["source"] == "sleep_hours"
    assert availability.compute({**s, "availability": expired, "sleep_hours": {"enabled": False}}, NOW, None)["mode"] == "available"
    # "Available until 5 PM" set by hand beats the calendar
    st = availability.compute({**s, "availability": {"mode": "available", "until": NOW + 3600, "set_at": 1}}, NOW,
                              {"id": "e1", "end": NOW + 600})
    assert st["mode"] == "available" and st["source"] == "manual" and not st["away"]


def test_sleep_window_across_midnight():
    s = {**SETTINGS, "sleep_hours": {"enabled": True, "start": "23:00", "end": "07:00"}}
    at_2am = datetime(2026, 11, 25, 2, 0, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    st = availability.compute(s, at_2am, None)
    assert st["mode"] == "sleeping" and st["key"] == "s:2026-11-24"
    assert datetime.fromtimestamp(st["until"], ZoneInfo("Asia/Kolkata")).hour == 7


def test_phrase_and_until_text():
    st = availability.compute({**SETTINGS, "availability": {"mode": "working", "detail": "The Q4 launch",
                                                           "until": NOW + 3 * 3600, "set_at": 1}}, NOW, None)
    assert availability.phrase(st, SETTINGS, now=NOW) == "Yashraj is working on the Q4 launch until 6:00 PM"
    leave = availability.compute({**SETTINGS, "availability": {"mode": "leave", "until": NOW + 5 * 86400, "set_at": 1}}, NOW, None)
    assert availability.phrase(leave, SETTINGS, now=NOW) == "Yashraj is on leave until Sun 29 Nov"


def test_status_command_parsing():
    p = availability.parse_command
    assert p("hello", SETTINGS, NOW) is None
    mode, detail, until = p("status sleeping until 7am", SETTINGS, NOW)
    assert mode == "sleeping" and detail == "" and datetime.fromtimestamp(until, ZoneInfo("Asia/Kolkata")).strftime("%d %H:%M") == "25 07:00"
    assert p("status working on the launch until 6pm", SETTINGS, NOW)[:2] == ("working", "the launch")
    assert p("Status DND for 2 hours", SETTINGS, NOW)[0] == "dnd" and p("status dnd for 2 hours", SETTINGS, NOW)[2] == NOW + 7200
    assert p("status available", SETTINGS, NOW) == ("available", "", None)
    assert p("status travelling to Pune", SETTINGS, NOW)[:2] == ("custom", "travelling to Pune")
    with pytest.raises(availability.AvailabilityError):
        p("status sleeping until whenever", SETTINGS, NOW)


def test_set_status_validates(store):
    with pytest.raises(availability.AvailabilityError):
        availability.set_status(store, "meeting")  # calendar-only
    with pytest.raises(availability.AvailabilityError):
        availability.set_status(store, "custom", "")
    with pytest.raises(availability.AvailabilityError):
        availability.set_status(store, "dnd", until=time.time() - 5)
    saved = availability.set_status(store, "office", "ignored detail")
    assert saved["mode"] == "office" and saved["detail"] == "" and store.settings()["availability"]["mode"] == "office"


def test_free_slots_skip_busy_times_and_weekends():
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 11, 27, 9, 0, tzinfo=tz)  # Friday 9 AM
    busy = [(datetime(2026, 11, 27, 10, 0, tzinfo=tz), datetime(2026, 11, 27, 12, 0, tzinfo=tz))]
    out = availability.free_slots(SETTINGS, minutes=30, busy_fn=lambda a, b: busy, now=now)
    assert "Fri 27 Nov, 12:00 PM" in out and "10:00 AM" not in out.split("Mon")[0]
    assert "Sat" not in out and "Sun" not in out and "Mon 30 Nov, 10:00 AM" in out
    assert "NOT free" in availability.free_slots(SETTINGS, at="2026-11-27T10:30", minutes=30, busy_fn=lambda a, b: busy, now=now)
    assert "is free" in availability.free_slots(SETTINGS, at="2026-11-27T14:00", minutes=30, busy_fn=lambda a, b: busy, now=now)


def test_urgent_ping_follows_the_dnd_and_sleeping_toggles():
    dnd, sleeping, office = ({"mode": m} for m in ("dnd", "sleeping", "office"))
    assert not availability.urgent_ping_allowed({}, dnd) and not availability.urgent_ping_allowed({}, sleeping)
    assert availability.urgent_ping_allowed({"urgent_notify_dnd": True}, dnd)
    assert availability.urgent_ping_allowed({"urgent_notify_sleeping": True}, sleeping)
    assert availability.urgent_ping_allowed({}, office)


def test_menu_choices_by_number_word_and_vote():
    opts = availability.menu_options({"menu_options": [{"id": "meeting", "enabled": True}, {"id": "message", "enabled": False},
                                                        {"id": "urgent", "enabled": True}]})
    assert [o["id"] for o in opts] == ["meeting", "urgent"]
    assert availability.parse_choice("2", opts)["id"] == "urgent" and availability.parse_choice("3", opts) is None
    assert availability.parse_choice("Meeting!", opts)["id"] == "meeting"
    assert availability.parse_choice("can we meet tomorrow at the usual place?", opts) is None
    assert availability.option_by_vote("⚡ It's urgent", opts)["id"] == "urgent"


# ------------------------------------------------------------------------------ the greeting in the pipeline
async def test_away_greeting_and_menu_once_per_status(mgr, store, monkeypatch):
    calls = count_llm_calls(monkeypatch)
    contact(store)
    away(store, "working", "the Q4 launch")
    await deliver(mgr, incoming("hey, are you free today?"))
    texts = [m.text for m in FakeChannel.sent]
    assert len(texts) == 2 and calls == []  # greeting + numbered menu (this channel has no polls), no model call
    assert "I'm Donna, Yashraj's personal AI assistant" in texts[0] and "Yashraj is working on the Q4 launch until" in texts[0]
    assert "@donna" in texts[0] and "1. 📩 Pass a message" in texts[1] and "4. ⚡ It's urgent" in texts[1]
    await deliver(mgr, incoming("ok no worries"))
    assert len(FakeChannel.sent) == 2  # left for you, no second greeting in the same status period
    away(store, "dnd")  # a new status period greets again
    await deliver(mgr, incoming("still there?"))
    assert len(FakeChannel.sent) == 4 and "isn't taking messages right now" in FakeChannel.sent[2].text


async def test_no_greeting_when_available_or_switched_off(mgr, store):
    contact(store)
    store.set_settings(require_trigger=True, calendar_auto_status=False)
    await deliver(mgr, incoming("hi"))
    assert len(FakeChannel.sent) == 1 and "include @donna" in FakeChannel.sent[0].text  # the old one-time hint
    away(store)
    store.set_settings(greeting_enabled=False)
    await deliver(mgr, incoming("hello?"))
    assert len(FakeChannel.sent) == 1


async def test_unknown_senders_are_never_greeted(mgr, store, monkeypatch):
    away(store)
    await deliver(mgr, incoming("who is this", sender="999", name="Stranger"))
    assert FakeChannel.sent == []


async def test_menu_number_starts_a_flow_that_needs_no_trigger(mgr, store, monkeypatch):
    calls = count_llm_calls(monkeypatch)
    contact(store)
    away(store)
    await deliver(mgr, incoming("hi"))
    await deliver(mgr, incoming("2"))  # 📅 Schedule a meeting
    assert calls, "the choice reaches Donna"
    system = " ".join(m["content"] for m in calls[0]["messages"] if m["role"] == "system")
    assert "Schedule a meeting" in system and "suggest_meeting_slots" in system
    assert any(m["role"] == "user" and m["content"].endswith("📅 Schedule a meeting") for m in calls[0]["messages"])
    assert conv_of(store)["menu_flow"] == "meeting"  # the request still lacks a time, so the flow goes on
    n = len(calls)
    await deliver(mgr, incoming("about the vendor quote"))  # no @donna, still Donna's: the flow is active
    assert len(calls) > n


async def test_poll_vote_starts_the_flow_and_its_request_ends_it(mgr, store, monkeypatch):
    contact(store)
    away(store)
    await deliver(mgr, incoming("hi"))
    await deliver(mgr, incoming("📞 Request a call back", mtype="choice"))
    # the scripted model records the callback straight away, which hands it to you and ends the flow
    req = contact_requests.list_all(store)[0]
    assert req["type"] == "callback" and req["status"] == "waiting_for_owner"
    assert conv_of(store)["menu_flow"] == ""


async def test_a_vote_on_something_else_is_ignored(mgr, store):
    contact(store)
    away(store)
    await deliver(mgr, incoming("hi"))
    await deliver(mgr, incoming("🍕 Pizza", mtype="choice"))
    assert len(FakeChannel.sent) == 2


# ------------------------------------------------------------------------------ the owner's phone
async def test_owner_sets_status_from_chat(mgr, store):
    me = contact(store, sender="555", name="Yashraj")
    contacts.set_owner(store, me["id"])
    await deliver(mgr, incoming("status sleeping until 7am", sender="555", name="Yashraj"))
    assert store.settings()["availability"]["mode"] == "sleeping"
    assert FakeChannel.sent[-1].text.startswith("Status set: 😴 Sleeping until")
    await deliver(mgr, incoming("status sleeping until someday", sender="555", name="Yashraj"))
    assert "couldn't read the time" in FakeChannel.sent[-1].text


async def test_owner_request_commands(mgr, store):
    me = contact(store, sender="555", name="Yashraj")
    contacts.set_owner(store, me["id"])
    await deliver(mgr, incoming("approve R99", sender="555", name="Yashraj"))
    assert FakeChannel.sent[-1].text == "There's no request R99."


# ------------------------------------------------------------------------------ meetings
async def test_contact_cancelling_their_booked_meeting_removes_it(mgr, store, monkeypatch):
    rahul = contact(store)
    await deliver(mgr, incoming("@donna hi"))
    conv = conv_of(store)
    ctx = TurnContext(audience="contact", channel_id="fake", contact=rahul, channel_conversation_id=conv["id"],
                      capabilities={"requests": "on"})
    start = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT10:00")
    req, _ = contact_requests.record(store, ctx, "meeting", "Vendor quote review", start=start)
    contact_requests.set_booking(store, req["id"], "evt-1", start, start[:11] + "10:30", "https://meet.google.com/abc-defg-hij")
    contact_requests.set_status(store, req["id"], "completed", "Booked")
    deleted = []

    async def fake_call(hub, tool, args, ctx, store):
        deleted.append(args)
        return True, "Event deleted."
    monkeypatch.setattr("donna.channels.manager.find_tool", lambda hub, name, settings: object())
    monkeypatch.setattr("donna.channels.manager.call_tool", fake_call)
    ok, out = await runtime_tools.call(runtime_tools.find("update_request"), {"request_id": req["code"], "status": "cancelled"},
                                       ctx, store)
    assert ok and "off Yashraj's calendar" in out and deleted == [{"event_id": "evt-1"}]
    assert contact_requests.get(store, req["id"])["status"] == "cancelled"
    assert any("cancelled the meeting" in n.title for n in mgr.notifier.sent)


def test_meeting_notification_tells_the_owner_how_to_approve(store):
    req = {"id": 7, "code": "R7", "type": "meeting", "summary": "Quote review", "details": {"start": "2026-11-27T15:00"},
           "missing": "", "contact_name": "Rahul", "event_id": ""}
    title, body = contact_requests.notification(req)
    assert title == "Meeting request from Rahul" and 'Reply "approve R7" to book it with a Google Meet link' in body


def test_settings_api_validates_menu_and_hours(store):
    from fastapi.testclient import TestClient

    from donna import app as app_module
    app_module.app.state.db_path = store.path
    with TestClient(app_module.app) as c:
        r = c.put("/api/v1/settings", json={"menu_options": [{"id": "urgent", "enabled": True}, {"id": "bogus"}],
                                            "sleep_hours": {"enabled": True, "start": "23:30", "end": "06:30"}})
        assert r.status_code == 200
        assert [o["id"] for o in r.json()["menu_options"]] == ["urgent", "message", "meeting", "callback"]
        assert r.json()["menu_options"][1]["enabled"] is False
        assert c.put("/api/v1/settings", json={"sleep_hours": {"start": "25:00", "end": "06:00"}}).status_code == 400
        assert c.put("/api/v1/availability", json={"mode": "working", "detail": "launch"}).json()["status"]["mode"] == "working"
        assert c.put("/api/v1/availability", json={"mode": "meeting"}).status_code == 400
    json.dumps(store.settings())  # still serialisable
