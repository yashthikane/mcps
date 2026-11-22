"""WhatsApp (OpenWA) adapter: webhook authentication and normalisation, the connect flow and sending,
against a mock OpenWA, plus the signed webhook route through the real app."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from donna import contacts, llm, vault
from donna.channels.base import DeliveryUpdate, OutgoingMessage, StatusUpdate, WebhookRejected
from donna.channels.whatsapp import WhatsAppChannel, sign

SECRET = "s" * 32


class FakeOpenWA:
    """Enough of OpenWA's REST API for connect / qr / send."""

    def __init__(self):
        self.requests = []
        self.webhooks = [{"id": "old", "url": "http://127.0.0.1:8765/api/v1/webhooks/whatsapp"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body, dict(request.headers)))
        path = request.url.path
        if request.headers.get("x-api-key") != "key-1234567890":
            return httpx.Response(401, json={"message": "bad key"})
        if path == "/api/sessions" and request.method == "GET":
            return httpx.Response(200, json=[])
        if path == "/api/sessions" and request.method == "POST":
            return httpx.Response(201, json={"id": "sess-1", "name": body["name"], "status": "created", "engineLoaded": False})
        if path == "/api/sessions/sess-1" and request.method == "GET":
            return httpx.Response(200, json={"id": "sess-1", "status": "qr_ready", "engineLoaded": True})
        if path == "/api/sessions/sess-1/qr":
            return httpx.Response(200, json={"qrCode": "data:image/png;base64,QQ==", "status": "qr_ready"})
        if path == "/api/sessions/sess-1/webhooks" and request.method == "GET":
            return httpx.Response(200, json=self.webhooks)
        if path == "/api/sessions/sess-1/webhooks" and request.method == "POST":
            return httpx.Response(201, json={"id": "hook-2", **body})
        if path.startswith("/api/sessions/sess-1/webhooks/") and request.method == "DELETE":
            return httpx.Response(200, json={})
        if path == "/api/sessions/sess-1/start":
            return httpx.Response(200, json={"id": "sess-1", "status": "initializing"})
        if path == "/api/sessions/sess-1/messages/send-text":
            return httpx.Response(201, json={"messageId": "true_91999@c.us_ABC", "timestamp": 1})
        return httpx.Response(404, json={"message": "not found"})


def channel(openwa=None, config=None):
    saved = {}
    ch = WhatsAppChannel("whatsapp", config or {}, lambda: {"openwa_url": "http://127.0.0.1:2785"},
                         lambda events: None, saved.update, transport=httpx.MockTransport(openwa or FakeOpenWA()))
    return ch, saved


def envelope(data, event="message.received", ts=None):
    return json.dumps({"event": event, "timestamp": (ts or datetime.now(timezone.utc)).isoformat(), "sessionId": "sess-1",
                       "data": data}).encode()


def message(**kw):
    return {"id": "true_919812345678@c.us_3EB0", "from": "919812345678@c.us", "to": "911111111111@c.us",
            "chatId": "919812345678@c.us", "body": "Hi Donna", "type": "chat", "timestamp": 1719312000,
            "fromMe": False, "isGroup": False, "kind": "individual", "contact": {"pushName": "Rahul"}, **kw}


def signed(raw):
    return {"x-openwa-signature": sign(SECRET, raw)}


@pytest.fixture
def secret():
    vault.set(vault.OPENWA_SECRET, SECRET)
    vault.set(vault.OPENWA_KEY, "key-1234567890")


# ------------------------------------------------------------------------------ webhook verification
def test_signed_message_is_normalised(secret):
    ch, _ = channel(config={"session_id": "sess-1"})
    raw = envelope(message())
    [msg] = ch.parse_webhook(raw, signed(raw))
    assert (msg.sender_id, msg.chat_id, msg.sender_phone, msg.sender_name, msg.text) == \
        ("919812345678@c.us", "919812345678@c.us", "919812345678", "Rahul", "Hi Donna")
    assert msg.event_id == "msg:true_919812345678@c.us_3EB0" and msg.message_type == "text"


def test_bad_signature_stale_and_oversized_webhooks_are_rejected(secret):
    ch, _ = channel()
    raw = envelope(message())
    with pytest.raises(WebhookRejected, match="signature"):
        ch.parse_webhook(raw, {"x-openwa-signature": "sha256=" + "0" * 64})
    with pytest.raises(WebhookRejected, match="signature"):
        ch.parse_webhook(raw, {})
    old = envelope(message(), ts=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(WebhookRejected, match="Stale"):
        ch.parse_webhook(old, signed(old))
    with pytest.raises(WebhookRejected) as e:
        ch.parse_webhook(b"x" * (3 * 1024 * 1024), {})
    assert e.value.status == 413


def test_unconfigured_secret_rejects_everything():
    ch, _ = channel()
    raw = envelope(message())
    with pytest.raises(WebhookRejected):
        ch.parse_webhook(raw, {"x-openwa-signature": sign("", raw)})


@pytest.mark.parametrize("extra", [{"fromMe": True}, {"isGroup": True, "chatId": "123@g.us"}, {"kind": "status"},
                                   {"isStatusBroadcast": True}, {"chatId": "status@broadcast"}])
def test_own_group_and_status_messages_are_dropped(secret, extra):
    ch, _ = channel()
    raw = envelope(message(**extra))
    assert ch.parse_webhook(raw, signed(raw)) == []


def test_media_lid_ack_and_session_events(secret):
    ch, _ = channel()
    raw = envelope(message(type="ptt", body="", chatId="12345@lid", senderPhone="+91 98123 45678", media={"mimetype": "audio/ogg"}))
    [msg] = ch.parse_webhook(raw, signed(raw))
    assert msg.message_type == "audio" and msg.sender_id == "12345@lid" and msg.sender_phone == "919812345678"
    assert msg.attachments[0]["mimetype"] == "audio/ogg"
    raw = envelope({"messageId": "out-1", "status": "read"}, event="message.ack")
    assert ch.parse_webhook(raw, signed(raw)) == [DeliveryUpdate(external_id="out-1", status="read")]
    raw = envelope({"phone": "911111111111"}, event="session.authenticated")
    assert ch.parse_webhook(raw, signed(raw)) == [StatusUpdate(status="connected", account="911111111111")]


def test_lid_sender_matches_a_contact_added_by_phone(store):
    c = contacts.create(store, "Rahul", mode="auto")
    contacts.add_identity(store, c["id"], "whatsapp", "919812345678@c.us", "919812345678")
    found, created = contacts.resolve(store, "whatsapp", "12345@lid", "919812345678", "Rahul")
    assert not created and found["id"] == c["id"]
    assert {i["external_id"] for i in found["identities"]} == {"919812345678@c.us", "12345@lid"}


# ------------------------------------------------------------------------------ OpenWA API
async def test_connect_creates_the_session_and_registers_a_signed_webhook(secret):
    openwa = FakeOpenWA()
    ch, saved = channel(openwa)
    st = await ch.connect()
    assert st.status == "qr" and saved["session_id"] == "sess-1" and saved["webhook_id"] == "hook-2"
    calls = [(m, p) for m, p, _, _ in openwa.requests]
    assert ("POST", "/api/sessions") in calls and ("POST", "/api/sessions/sess-1/start") in calls
    assert ("DELETE", "/api/sessions/sess-1/webhooks/old") in calls  # stale registration replaced
    hook = next(b for m, p, b, _ in openwa.requests if m == "POST" and p.endswith("/webhooks"))
    assert hook["secret"] == SECRET and "message.received" in hook["events"] and hook["url"].endswith("/api/v1/webhooks/whatsapp")
    assert (await ch.login_code())["qr"].startswith("data:image/png")


async def test_send_uses_the_chat_id_and_an_idempotency_key(secret):
    openwa = FakeOpenWA()
    ch, _ = channel(openwa, {"session_id": "sess-1"})
    res = await ch.send(OutgoingMessage(channel_id="whatsapp", recipient_id="12345@lid", text="hello", idempotency_key="m1"))
    assert res.ok and res.external_id == "true_91999@c.us_ABC"
    _, path, body, headers = openwa.requests[-1]
    assert path == "/api/sessions/sess-1/messages/send-text" and body == {"chatId": "12345@lid", "text": "hello"}
    assert headers["idempotency-key"] == "m1"


async def test_missing_key_and_unreachable_gateway_are_readable_errors():
    ch, _ = channel()
    with pytest.raises(Exception, match="setup-whatsapp"):
        await ch.connect()
    vault.set(vault.OPENWA_KEY, "key-1234567890")

    def down(request):
        raise httpx.ConnectError("refused")
    ch2 = WhatsAppChannel("whatsapp", {"session_id": "sess-1"}, lambda: {}, lambda e: None, lambda c: None,
                          transport=httpx.MockTransport(down))
    res = await ch2.send(OutgoingMessage(channel_id="whatsapp", recipient_id="1@c.us", text="x"))
    assert not res.ok and res.retryable and "Can't reach OpenWA" in res.error


def test_identity_and_formatting():
    ch, _ = channel()
    assert ch.identity_for("+91 98123-45678") == ("919812345678@c.us", "919812345678")
    assert ch.identity_for("12345@lid") == ("12345@lid", "")
    assert ch.format_text("## Plan\n**Bold** and [link](https://x.y)\n- one") == "*Plan*\n*Bold* and link (https://x.y)\n• one"


# ------------------------------------------------------------------------------ the HTTP route
def test_webhook_route_authenticates_and_deduplicates(tmp_path, monkeypatch, secret):
    monkeypatch.setattr(llm, "FAKE", True)
    from donna.app import app
    app.state.db_path = tmp_path / "wa.db"
    app.state.channel_adapter_kwargs = {"whatsapp": {"transport": httpx.MockTransport(FakeOpenWA())}}
    try:
        with TestClient(app) as c:
            store = app.state.store
            store.set_settings(require_trigger=False)
            store.execute("INSERT INTO channels (id, type, display_name, enabled, status, config, created_at, updated_at) "
                          "VALUES ('whatsapp','whatsapp','WhatsApp',1,'connected','{\"session_id\": \"sess-1\"}',0,0)")
            r = contacts.create(store, "Rahul", mode="auto")
            contacts.add_identity(store, r["id"], "whatsapp", "919812345678@c.us")
            raw = envelope(message())
            ok = c.post("/api/v1/webhooks/whatsapp", content=raw, headers=signed(raw))
            assert ok.status_code == 200 and ok.json() == {"accepted": 1, "duplicates": 0}
            again = c.post("/api/v1/webhooks/whatsapp", content=raw, headers=signed(raw))
            assert again.json() == {"accepted": 0, "duplicates": 1}
            bad = c.post("/api/v1/webhooks/whatsapp", content=raw, headers={"x-openwa-signature": "sha256=00"})
            assert bad.status_code == 401
            assert c.get("/api/v1/audit", params={"outcome": "denied"}).json()[0]["action"] == "webhook.rejected"
            c.portal.call(app.state.channels.idle)  # let the background turn finish
            inbox = c.get("/api/v1/inbox").json()
            assert inbox[0]["contact_name"] == "Rahul"
            msgs = c.get(f"/api/v1/inbox/{inbox[0]['id']}/messages").json()
            assert msgs[0]["direction"] == "in" and msgs[0]["content"] == "Hi Donna" and msgs[0]["status"] == "answered"
            assert msgs[-1]["direction"] == "out" and msgs[-1]["status"] == "sent" and "Hi Donna" in msgs[-1]["content"]
    finally:
        app.state.channel_adapter_kwargs = None


# ------------------------------------------------------------------------------ the Connect button (OpenWA setup job)
async def _finish(job):
    job.begin()
    await job.task
    return job.snapshot()


async def test_connect_installs_starts_and_reaches_the_qr(monkeypatch):
    from donna import openwa
    calls, state = [], {"installed": False, "running": False}
    monkeypatch.setattr(openwa, "problems", lambda: [])
    monkeypatch.setattr(openwa, "installed", lambda: state["installed"])
    monkeypatch.setattr(openwa, "running", lambda port=2785: state["running"])
    monkeypatch.setattr(openwa, "install", lambda say: (calls.append("install"), state.update(installed=True)))
    monkeypatch.setattr(openwa, "start", lambda say: (calls.append("start"), state.update(running=True)))

    async def connect():
        calls.append("connect")
        return {"status": "qr"}
    snap = await _finish(openwa.Setup(connect))
    assert calls == ["install", "start", "connect"] and snap["phase"] == "qr" and not snap["busy"]
    calls.clear()
    snap = await _finish(openwa.Setup(connect))  # second time: already installed and running
    assert calls == ["connect"] and snap["phase"] == "qr"


async def test_connect_reports_missing_node_and_failures(monkeypatch):
    from donna import openwa
    monkeypatch.setattr(openwa, "problems", lambda: ["Install Node.js 22 LTS or newer from https://nodejs.org."])

    async def connect():
        raise AssertionError("not reached")
    snap = await _finish(openwa.Setup(connect))
    assert snap["phase"] == "error" and "Node.js" in snap["error"]

    monkeypatch.setattr(openwa, "problems", lambda: [])
    monkeypatch.setattr(openwa, "installed", lambda: True)
    monkeypatch.setattr(openwa, "running", lambda port=2785: False)

    def broken(say):
        raise openwa.SetupError("OpenWA didn't start within 120 seconds.")
    monkeypatch.setattr(openwa, "start", broken)
    snap = await _finish(openwa.Setup(connect))
    assert snap["phase"] == "error" and "didn't start" in snap["error"] and any("✕" in line for line in snap["log"])


def test_connect_route_starts_the_job_on_the_server_loop(tmp_path, monkeypatch):
    from donna import openwa
    monkeypatch.setattr(llm, "FAKE", True)
    monkeypatch.setattr(openwa, "problems", lambda: ["Install Node.js 22 LTS or newer."])
    from donna.app import app
    app.state.db_path = tmp_path / "setup.db"
    with TestClient(app) as c:
        assert c.post("/api/v1/channels/whatsapp/setup").status_code == 200
        c.portal.call(lambda: app.state.channels.whatsapp_setup.task)
        state = c.get("/api/v1/channels/whatsapp/setup").json()
        assert state["phase"] == "error" and "Node.js" in state["error"]
        assert [x for x in c.get("/api/v1/channels").json() if x["id"] == "whatsapp"][0]["setup"]["phase"] == "error"


async def test_an_engine_failure_after_sending_is_unconfirmed_not_retried(secret):
    """OpenWA 5xx ("the engine returned no message … may not have been delivered") can follow a real
    delivery: no automatic retry, and the result says not to send again."""
    calls = []

    def openwa(request):
        calls.append(request.url.path)
        return httpx.Response(500, json={"message": "the engine returned no message for this send, so it may not have been delivered"})
    ch, _ = channel(openwa, {"session_id": "sess-1"})
    res = await ch.send(OutgoingMessage(channel_id="whatsapp", recipient_id="1@c.us", text="hi", idempotency_key="k1"))
    assert not res.ok and res.uncertain and not res.retryable and len(calls) == 1
