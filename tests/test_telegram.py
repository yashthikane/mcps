"""Telegram adapter: update normalisation, the long-poll loop (offset, dedup) and sending, against a mock Bot API."""
import asyncio
import json

import httpx
import pytest

from donna import vault
from donna.channels.base import ChannelError, OutgoingMessage
from donna.channels.telegram import TelegramChannel

TOKEN = "123456789:" + "A" * 35


def update(uid, text="hi", chat_type="private", is_bot=False, **extra):
    msg = {"message_id": uid * 10, "date": 1719312000, "chat": {"id": 42, "type": chat_type},
           "from": {"id": 42, "is_bot": is_bot, "first_name": "Asha", "username": "asha"}, **extra}
    if text is not None:
        msg["text"] = text
    return {"update_id": uid, "message": msg}


class FakeBotAPI:
    def __init__(self, batches):
        self.batches, self.requests = list(batches), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.requests.append((method, body))
        if TOKEN not in request.url.path:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 7, "username": "donna_bot"}})
        if method == "deleteWebhook":
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": self.batches.pop(0) if self.batches else []})
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})


def channel(api, events=None, config=None):
    saved = {}

    async def on_events(evs):
        events.extend(evs)
    ch = TelegramChannel("telegram", config or {}, lambda: {}, on_events if events is not None else None, saved.update,
                         transport=httpx.MockTransport(api))
    return ch, saved


def test_only_private_human_messages_are_normalised():
    ch, _ = channel(FakeBotAPI([]))
    msg = ch.normalize(update(1))
    assert (msg.sender_id, msg.chat_id, msg.sender_name, msg.text, msg.event_id) == ("42", "42", "Asha", "hi", "msg:42:10")
    assert ch.normalize(update(2, chat_type="group")) is None
    assert ch.normalize(update(3, is_bot=True)) is None
    photo = ch.normalize(update(4, text=None, photo=[{}], caption="look"))
    assert photo.message_type == "image" and photo.text == "look"


async def test_connect_poll_and_send():
    vault.set(vault.TELEGRAM_TOKEN, TOKEN)
    api = FakeBotAPI([[update(5), update(6, "second")]])
    got = []
    ch, saved = channel(api, got)
    st = await ch.connect()
    assert st.status == "connected" and st.account == "@donna_bot" and saved["username"] == "donna_bot"
    await ch.start()
    for _ in range(50):
        if saved.get("offset"):
            break
        await asyncio.sleep(0.02)
    await ch.stop()
    assert [m.text for m in got] == ["hi", "second"] and saved["offset"] == 7
    polls = [b for m, b in api.requests if m == "getUpdates"]
    assert polls[0]["offset"] == 0 and polls[1]["offset"] == 7
    res = await ch.send(OutgoingMessage(channel_id="telegram", recipient_id="42", text="hello"))
    assert res.ok and api.requests[-1] == ("sendMessage", {"chat_id": "42", "text": "hello", "link_preview_options": {"is_disabled": True}})


async def test_bad_token_and_identities():
    vault.set(vault.TELEGRAM_TOKEN, "999:" + "B" * 35)
    ch, _ = channel(FakeBotAPI([]))
    with pytest.raises(ChannelError, match="rejected the bot token"):
        await ch.connect()
    assert ch.identity_for("123456789") == ("123456789", "")
    with pytest.raises(ChannelError):
        ch.identity_for("@asha")


async def test_picture_is_uploaded_with_send_photo():
    vault.set(vault.TELEGRAM_TOKEN, TOKEN)
    seen = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["content-type"], request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 12}})
    ch, _ = channel(api)
    res = await ch.send_image("42", b"\x89PNGdata", "image/png", "donna-img_0123456789ab.png")
    assert res.ok and res.external_id == "42:12"
    path, ctype, body = seen[0]
    assert path.endswith("/sendPhoto") and ctype.startswith("multipart/form-data")
    assert b'name="chat_id"' in body and b"\x89PNGdata" in body and b'filename="donna-img_0123456789ab.png"' in body
