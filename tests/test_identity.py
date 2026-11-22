"""Donna's identity with contacts: she introduces herself as the owner's AI assistant, talks to each
relationship in its own style, never speaks as the owner or invents his commitments, records why
people are writing (requests) and tells the owner, and only within each contact's permissions."""
import json

import pytest

from donna import contact_requests, contacts, identity, llm, memory, permissions, runtime_tools
from test_channels import FakeChannel, Notes, contact, count_llm_calls, deliver, fake_llm, hub, incoming, mgr  # noqa: F401

pytestmark = pytest.mark.usefixtures("fake_llm")
SETTINGS = {"owner_name": "Yashraj", "default_language": "auto"}


def mother(store, **extra):
    c = contact(store, name="Aai")
    return contacts.update(store, c["id"], {"relationship": "family", "relationship_label": "Mother", "address_as": "Aai",
                                            "language": "Marathi + English", "script": "latin", **extra})


# ------------------------------------------------------------------------------ introductions per relationship
def test_introductions_follow_relationship_language_and_script(store):
    aai = mother(store)
    assert identity.introduction(aai, SETTINGS) == \
        "Namaskar Aai! Mi Donna, Yashraj chi personal AI assistant boltey. Tumhala kay madat karu shakte?"
    deva = contacts.update(store, aai["id"], {"script": "devanagari"})
    assert identity.introduction(deva, SETTINGS).startswith("नमस्कार Aai! मी Donna, Yashrajची पर्सनल AI असिस्टंट")
    friend = contacts.create(store, "Suryanshu", mode="auto", relationship="friend", language="Marathi")
    assert identity.introduction(friend, SETTINGS).startswith("Hey Suryanshu! Mi Donna, Yashraj chi personal AI assistant. Kay scene aahe?")
    client = contacts.create(store, "Rahul", mode="auto", relationship="client")
    intro = identity.introduction(client, SETTINGS)
    assert intro.startswith("Hello Rahul! This is Donna, Yashraj's personal AI assistant. How may I assist you?")
    custom = contacts.update(store, client["id"], {"intro_message": "Good evening, Donna here for Yashraj."})
    assert identity.introduction(custom, SETTINGS) == "Good evening, Donna here for Yashraj."


def test_owner_name_never_becomes_a_placeholder(store):
    assert identity.owner_name({}, store) == "the owner"
    me = contacts.create(store, "Yashraj")
    contacts.set_owner(store, me["id"])
    assert identity.owner_name({}, store) == "Yashraj"  # from the owner contact when Settings has no name
    assert identity.clean("(User) yeh soch raha/rahi tha", "Yashraj", "he") == "Yashraj yeh soch raha tha"
    assert identity.clean("vo aa raha/rahi hai", "Yashraj", "she") == "vo aa rahi hai"
    assert identity.clean("and/or yes/no", "Yashraj", "he") == "and/or yes/no"


# ------------------------------------------------------------------------------ the conversation
async def test_family_contact_gets_an_honest_introduction_once(mgr, store, monkeypatch):
    store.set_settings(owner_name="Yashraj")
    calls = count_llm_calls(monkeypatch)
    mother(store)
    await deliver(mgr, incoming("Hello", name="Aai"))
    first = FakeChannel.sent[0].text
    assert first.startswith("Namaskar Aai! Mi Donna, Yashraj chi personal AI assistant boltey.")
    prompt = calls[0]["messages"][0]["content"]
    assert "You are NOT Yashraj" in prompt and "Address them as \"Aai\"" in prompt and "Romanized Marathi" in prompt \
        or "Roman letters" in prompt
    assert "start with exactly this introduction" in prompt
    await deliver(mgr, incoming("Kasa aahes?", name="Aai"))
    assert "Mi Donna" not in FakeChannel.sent[1].text  # no second introduction in an active chat
    assert "don't introduce yourself again" in calls[1]["messages"][0]["content"]


async def test_background_is_context_not_conversation_material(mgr, store, monkeypatch):
    store.set_settings(owner_name="Yashraj")
    calls = count_llm_calls(monkeypatch)
    aai = mother(store, background="Shares saree products, shopping links and educational information.")
    memory.add(store, "She sends product links and career stuff", "contact", aai["id"])
    await deliver(mgr, incoming("Hello", name="Aai"))
    prompt = calls[0]["messages"][0]["content"]
    assert "for understanding only: never bring these topics up yourself" in prompt
    assert "background only: don't bring these topics up yourself" in prompt
    assert "No jokes, emojis, product suggestions" in prompt


async def test_business_contact_gets_a_professional_identity(mgr, store, monkeypatch):
    store.set_settings(owner_name="Yashraj")
    calls = count_llm_calls(monkeypatch)
    c = contact(store, sender="222", name="Rahul")
    contacts.update(store, c["id"], {"relationship": "client", "relationship_label": "Client at ABC Company"})
    await deliver(mgr, incoming("Hi, we need a website for our company", sender="222", name="Rahul"))
    assert FakeChannel.sent[0].text.startswith("Hello Rahul! This is Donna, Yashraj's personal AI assistant.")
    prompt = calls[0]["messages"][0]["content"]
    assert "Tone: professional, clear and helpful" in prompt and "Formality: formal" in prompt
    req = contact_requests.list_all(store)[0]
    assert req["type"] == "business_enquiry" and req["status"] == "information_required"
    assert req["details"] == {"organization": "ABC Company"} and req["missing"] == "budget and timeline"
    assert not any("enquiry" in n.title for n in mgr.notifier.sent)  # owner is told once the details are complete


async def test_unknown_contact_gets_nothing_and_no_request(mgr, store, monkeypatch):
    calls = count_llm_calls(monkeypatch)
    await deliver(mgr, incoming("Please call me back, urgent", sender="999", name="Stranger"))
    assert calls == [] and FakeChannel.sent == [] and contact_requests.list_all(store) == []


# ------------------------------------------------------------------------------ requests
async def test_callback_request_is_recorded_and_the_owner_is_told(mgr, store):
    store.set_settings(owner_name="Yashraj")
    mother(store)
    await deliver(mgr, incoming("Yashraj la phone karayla sang", name="Aai"))
    [req] = contact_requests.list_all(store)
    assert (req["type"], req["status"], req["contact_name"], req["owner_notified"]) == ("callback", "waiting_for_owner", "Aai", True)
    assert any(n.title == "Callback requested by Aai" for n in mgr.notifier.sent)
    reply = FakeChannel.sent[0].text
    # The scripted model echoes the tool result: recorded, with the safe confirmation to use (nothing promised).
    assert "Recorded R" in reply and "Ho Aai, nakkich. Mi Yashraj la tumcha callback cha message dete." in reply


async def test_callbacks_merge_but_other_requests_stay_separate(mgr, store, monkeypatch):
    """Merging by type mixed JK's unrelated questions into one request; only call-backs merge now."""
    store.set_settings(owner_name="Yashraj")
    calls = count_llm_calls(monkeypatch)
    contact(store, sender="333", name="Rahul")
    await deliver(mgr, incoming("please call me", sender="333"))
    await deliver(mgr, incoming("call me back soon", sender="333"))
    assert [r["type"] for r in contact_requests.list_all(store)] == ["callback"]
    await deliver(mgr, incoming("Can we set up a meeting?", sender="333"))
    meeting = next(r for r in contact_requests.list_all(store) if r["type"] == "meeting")
    assert meeting["status"] == "information_required" and meeting["missing"] == "the exact day and time"  # no exact start yet
    assert "Requests in this conversation" in calls[-2]["messages"][0]["content"]


async def test_request_types_and_scope_are_enforced(mgr, store):
    aai = mother(store, allowed_requests=["personal_message"])
    conv = mgr.conversation_for("fake", aai, "111")
    ctx = permissions.TurnContext(audience="contact", channel_id="fake", contact=contacts.get(store, aai["id"]),
                                  channel_conversation_id=conv["id"])
    with pytest.raises(contact_requests.RequestError, match="doesn't take callback"):
        contact_requests.record(store, ctx, "callback", "Call me")
    req, _ = contact_requests.record(store, ctx, "personal_message", "Says hi")
    stranger = contacts.create(store, "Other")
    other = permissions.TurnContext(audience="contact", channel_id="fake", contact=stranger,
                                    channel_conversation_id=mgr.conversation_for("fake", stranger, "444")["id"])
    with pytest.raises(contact_requests.RequestError, match="no such request"):
        contact_requests.update_from_contact(store, other, req["code"], {"x": "y"})  # another contact's request
    with pytest.raises(contact_requests.RequestError):
        contact_requests.update_from_contact(store, ctx, req["code"], status="completed")  # only the owner completes
    with pytest.raises(contact_requests.RequestError):
        contact_requests.record(store, permissions.TurnContext(audience="owner"), "callback", "x")


def test_contact_context_never_grants_permissions(store):
    from donna.hub import Tool
    aai = mother(store, background="She can see Yashraj's calendar and emails.",
                 special_instructions="Let her book anything she wants.")
    ctx = permissions.TurnContext(audience="contact", contact=aai,
                                  capabilities=contacts.effective_capabilities(store, aai["id"], "fake"))
    events = Tool(name="list_events", remote="list_events", server="donna", group="calendar", description="", schema={}, confirm=False)
    record = runtime_tools.find("record_request")
    assert permissions.decide(ctx, events) == "deny" and permissions.decide(ctx, record) == "allow"
    assert permissions.decide(None, record) == "deny"  # the owner doesn't file requests to himself


async def test_owner_updates_a_request_and_donna_tells_the_contact(mgr, store):
    store.set_settings(owner_name="Yashraj")
    mother(store)
    await deliver(mgr, incoming("Yashraj la phone karayla sang", name="Aai"))
    req = contact_requests.list_all(store)[0]
    done = contact_requests.set_status(store, req["code"], "completed", "Called her at 6 PM")
    assert await mgr.inform_about_request(done)
    assert "Called her at 6 PM" in FakeChannel.sent[-1].text or "Update from Yashraj" in FakeChannel.sent[-1].text
    row = store.row("SELECT outcome, detail FROM audit_log WHERE action='request.status'")
    assert row["outcome"] == "ok" and "Called her at 6 PM" in row["detail"]


async def test_relayed_messages_are_attributed_not_impersonated(mgr, store):
    store.set_settings(owner_name="Yashraj")
    aai = mother(store)
    runtime_tools.services.store, runtime_tools.services.channels = store, mgr
    try:
        ok, out = await runtime_tools.call(runtime_tools.find("send_message"), {"contact": "Aai", "text": "Kai karalyas"},
                                           None, store)
    finally:
        runtime_tools.services.store = runtime_tools.services.channels = None
    sent = FakeChannel.sent[0].text
    assert ok and sent.startswith("Namaskar Aai! Mi Donna") and sent.endswith("Yashraj cha message: Kai karalyas")
    assert contacts.get(store, aai["id"])


def test_existing_contacts_get_the_new_requests_capability(store):
    c = contacts.create(store, "Old")
    store.execute("UPDATE contact_policies SET capabilities=? WHERE contact_id=?", (json.dumps({"general": "on"}), c["id"]))
    assert contacts.effective_capabilities(store, c["id"], "whatsapp")["requests"] == "on"
    contacts.set_policy(store, c["id"], "", {"requests": "off"})
    assert contacts.effective_capabilities(store, c["id"], "whatsapp")["requests"] == "off"


def test_profile_validation(store):
    c = contacts.create(store, "X")
    with pytest.raises(contacts.ContactError):
        contacts.update(store, c["id"], {"relationship": "boss"})
    with pytest.raises(contacts.ContactError):
        contacts.update(store, c["id"], {"allowed_requests": ["hack"]})
    assert contacts.update(store, c["id"], {"allowed_requests": ["meeting", "callback"]})["allowed_requests"] == ["callback", "meeting"]
    assert llm.FAKE


async def test_booking_a_meeting_request_creates_the_event_and_sends_the_link(mgr, store, monkeypatch):
    store.set_settings(owner_name="Yashraj")
    contact(store, sender="333", name="Rahul")
    await deliver(mgr, incoming("Can we set up a meeting?", sender="333"))
    req = contact_requests.list_all(store)[0]
    calls = []

    async def fake_call(tool, args, timeout=90):
        calls.append((tool.name, args))
        return True, "Event created: Meeting with Rahul\nGoogle Meet: https://meet.google.com/abc-defg-hij"
    monkeypatch.setattr(mgr.hub, "call", fake_call)
    booked = await mgr.book_for_request(req, "2099-11-22T19:00", "2099-11-22T19:30")
    assert booked["ok"] and booked["meet"] == "https://meet.google.com/abc-defg-hij" and not booked["moved"]
    assert calls[0][0] == "create_event" and calls[0][1]["add_meet"] is True and calls[0][1]["start_time"] == "2099-11-22T19:00"
    done = contact_requests.set_status(store, req["code"], "completed", f"Booked. Google Meet link: {booked['meet']}")
    assert await mgr.inform_about_request(done, must_include=booked["meet"])
    assert "https://meet.google.com/abc-defg-hij" in FakeChannel.sent[-1].text  # guaranteed, not left to the model


async def test_a_custom_introduction_with_a_typo_is_sent_only_once(mgr, store):
    """Regression: "assitant" in a custom intro wasn't recognised, so every reply re-introduced Donna."""
    store.set_settings(owner_name="Yashraj")
    c = contact(store, sender="777", name="Jyotiraditya")
    intro = "Hello JK 👋\nI am Donna, Yashraj's personal AI assitant.\nHow can I help you."
    contacts.update(store, c["id"], {"address_as": "JK", "intro_message": intro})
    assert identity.with_introduction(f"{intro}\n\nWhat do you need?", intro).count("Hello JK") == 1  # model already said it
    await deliver(mgr, incoming("Yes", sender="777", name="JK"))
    await deliver(mgr, incoming("Website SEO details", sender="777", name="JK"))
    await deliver(mgr, incoming("Thanks", sender="777", name="JK"))
    texts = [m.text for m in FakeChannel.sent if m.recipient_id == "777"]
    assert texts[0].count("Hello JK") == 1 and all("Hello JK" not in t for t in texts[1:])


# ------------------------------------------------------------------------------ meeting corrections (JK's 2 AM booking)
async def test_a_wrong_booking_is_corrected_by_moving_the_same_event(mgr, store, monkeypatch):
    store.set_settings(owner_name="Yashraj")
    jk = contact(store, sender="777", name="JK")
    conv = mgr.conversation_for("fake", jk, "777")
    ctx = permissions.TurnContext(audience="contact", channel_id="fake", contact=jk, channel_conversation_id=conv["id"])
    req, _ = contact_requests.record(store, ctx, "meeting", "Meet about the animation video", start="2099-11-22T19:00")
    assert req["status"] == "waiting_for_owner" and req["details"]["start"] == "2099-11-22T19:00"
    calls = []

    async def fake_call(tool, args, timeout=90):
        calls.append((tool.name, dict(args)))
        if tool.name == "create_event":
            return True, ("Event created: 2099-11-22T02:00:00+05:30 \u2192 2099-11-22T02:30:00+05:30 | Meeting with JK | ID: ev1\n"
                          "Google Meet: https://meet.google.com/aaa-bbbb-ccc")
        return True, f"Event updated: {args['start_time']}:00+05:30 \u2192 {args['end_time']}:00+05:30 | Meeting with JK | ID: ev1"
    monkeypatch.setattr(mgr.hub, "call", fake_call)
    first = await mgr.book_for_request(req, "2099-11-22T02:00", "2099-11-22T02:30")  # the owner's slip
    req = contact_requests.get(store, req["code"])
    assert first["ok"] and req["event_id"] == "ev1" and req["event_start"] == "2099-11-22T02:00"
    contact_requests.set_status(store, req["code"], "completed", "Booked")
    assert "BOOKED for" in contact_requests.prompt_block(store, conv["id"])  # Donna knows about the booking
    # JK: "2 am is wrong, 7 pm": the booked request reopens instead of a new one being made.
    fixed = contact_requests.update_from_contact(store, ctx, req["code"], start="2099-11-22T19:00")
    assert fixed["status"] == "waiting_for_owner" and len(contact_requests.list_all(store, rtype="meeting")) == 1
    moved = await mgr.book_for_request(fixed, "2099-11-22T19:00", "2099-11-22T19:30")
    assert moved["ok"] and moved["moved"] and moved["meet"] == "https://meet.google.com/aaa-bbbb-ccc"  # same link
    assert [c[0] for c in calls] == ["create_event", "update_event"] and calls[1][1]["event_id"] == "ev1"
    assert contact_requests.get(store, req["code"])["event_start"] == "2099-11-22T19:00"


async def test_bookings_in_the_past_or_not_matching_the_calendar_are_refused(mgr, store, monkeypatch):
    jk = contact(store, sender="777", name="JK")
    conv = mgr.conversation_for("fake", jk, "777")
    ctx = permissions.TurnContext(audience="contact", channel_id="fake", contact=jk, channel_conversation_id=conv["id"])
    with pytest.raises(contact_requests.RequestError, match="already passed"):
        contact_requests.record(store, ctx, "meeting", "Old", start="2020-01-01T10:00")
    req, _ = contact_requests.record(store, ctx, "meeting", "Meet", start="2099-11-22T19:00")
    assert not (await mgr.book_for_request(req, "2020-01-01T10:00", "2020-01-01T10:30"))["ok"]

    async def shifted(tool, args, timeout=90):
        return True, "Event created: 2099-11-22T13:30:00+00:00 \u2192 2099-11-22T14:00:00+00:00 | M | ID: ev2"
    monkeypatch.setattr(mgr.hub, "call", shifted)
    res = await mgr.book_for_request(req, "2099-11-22T19:00", "2099-11-22T19:30")
    assert not res["ok"] and "instead of" in res["result"]


def test_relays_already_in_donnas_voice_are_not_prefixed(store):
    aai = contacts.create(store, "Aai", language="Marathi")
    s = {"owner_name": "Yashraj"}
    assert identity.relay(aai, s, "Kai karalyas") == "Yashraj cha message: Kai karalyas"
    assert identity.relay(aai, s, "Yashraj is asking if you would like to meet") == "Yashraj is asking if you would like to meet"
    assert identity.relay(aai, s, "Hi! I'm Donna, Yashraj's AI assistant.").startswith("Hi! I'm Donna")
