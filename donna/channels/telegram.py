"""Telegram through the Bot API with long polling: no public URL or webhook is needed, so it
works on a laptop. Create a bot with @BotFather and paste its token in Connections.

Only private chats are handled; messages from groups, channels and other bots are dropped. The
polling offset is saved in the channel config, and updates are deduplicated by message, so a
restart neither loses nor repeats messages.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

from .. import vault
from .base import BaseChannel, ChannelError, IncomingMessage, OutgoingMessage, SendResult, StatusUpdate

log = logging.getLogger("donna.telegram")
API = "https://api.telegram.org"
POLL_SECONDS = 30
TYPES = {"photo": "image", "voice": "audio", "audio": "audio", "video": "video", "video_note": "video",
         "document": "document", "sticker": "sticker", "location": "location"}


class TelegramChannel(BaseChannel):
    type = "telegram"
    label = "Telegram"
    max_text = 4000

    def __init__(self, *args, transport: httpx.AsyncBaseTransport | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._transport = transport
        self._task: asyncio.Task | None = None

    async def _call(self, method: str, http_timeout: float = 20, **params):
        token = vault.get(vault.TELEGRAM_TOKEN)
        if not token:
            raise ChannelError("Add your Telegram bot token in Connections first.", 400)
        try:
            async with httpx.AsyncClient(timeout=http_timeout, transport=self._transport) as c:
                r = await c.post(f"{API}/bot{token}/{method}", json=params)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ChannelError("Can't reach Telegram. Check your internet connection.", 503, retryable=True) from e
        if not body.get("ok"):
            code = body.get("error_code", r.status_code)
            if code == 401 or code == 404:
                raise ChannelError("Telegram rejected the bot token. Copy it again from @BotFather.", 400)
            # 409: another getUpdates is still open (e.g. right after a restart); it clears by itself.
            raise ChannelError(f"Telegram: {body.get('description', 'error')}", 502 if code >= 500 else 400,
                               retryable=code in (409, 429) or code >= 500)
        return body.get("result")

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> StatusUpdate:
        me = await self._call("getMe")
        await self._call("deleteWebhook")  # getUpdates doesn't work while a webhook is set
        self.save_config(username=me.get("username", ""), bot_id=me.get("id"))
        return StatusUpdate(status="connected", account=f"@{me.get('username', '')}")

    async def disconnect(self) -> None:
        await self.stop()

    async def health_check(self) -> StatusUpdate:
        me = await self._call("getMe")
        return StatusUpdate(status="connected", account=f"@{me.get('username', '')}")

    async def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._poll(), name=f"telegram-poll-{self.id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _poll(self) -> None:
        backoff = 1
        while True:
            try:
                updates = await self._get_updates()
                backoff = 1
                events = [m for m in (self.normalize(u) for u in updates) if m]
                if events:
                    await self.on_events(events)
                if updates:
                    self.save_config(offset=updates[-1]["update_id"] + 1)
                else:
                    await asyncio.sleep(1)  # an empty answer came back early: don't spin
            except asyncio.CancelledError:
                raise
            except ChannelError as e:
                if not e.retryable:
                    await self.on_events([StatusUpdate(status="error", detail=str(e))])
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:  # noqa: BLE001 - keep polling through surprises
                log.warning("Telegram polling error: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _get_updates(self) -> list[dict]:
        return await self._call("getUpdates", http_timeout=POLL_SECONDS + 15, offset=int(self.config.get("offset", 0)),
                                timeout=POLL_SECONDS, allowed_updates=["message"]) or []

    # ------------------------------------------------------------------ messages
    def normalize(self, update: dict) -> IncomingMessage | None:
        m = update.get("message") or {}
        chat, sender = m.get("chat") or {}, m.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot") or "message_id" not in m:
            return None
        mtype = "text" if "text" in m else next((TYPES[k] for k in TYPES if k in m), "other")
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or sender.get("username")
        return IncomingMessage(
            event_id=f"msg:{chat['id']}:{m['message_id']}", channel_id=self.id, channel_type=self.type,
            sender_id=str(chat["id"]), sender_name=name, chat_id=str(chat["id"]), message_id=str(m["message_id"]),
            message_type=mtype, text=m.get("text") or m.get("caption"),
            timestamp=datetime.fromtimestamp(int(m.get("date", 0)) or datetime.now().timestamp(), timezone.utc),
            attachments=[] if mtype == "text" else [{"type": mtype}],
            metadata={"username": sender.get("username", "")})

    async def send(self, message: OutgoingMessage) -> SendResult:
        try:
            r = await self._call("sendMessage", chat_id=message.recipient_id, text=message.text,
                                 link_preview_options={"is_disabled": True})
        except ChannelError as e:
            return SendResult(ok=False, error=str(e), retryable=e.retryable)
        return SendResult(ok=True, external_id=f"{message.recipient_id}:{r.get('message_id', '')}")

    async def send_image(self, recipient_id: str, data: bytes, mimetype: str, filename: str, caption: str = "",
                         idempotency_key: str = "") -> SendResult:
        """sendPhoto as a multipart upload (Telegram has no idempotency key; the manager never retries an
        uncertain send)."""
        token = vault.get(vault.TELEGRAM_TOKEN)
        if not token:
            return SendResult(ok=False, error="Add your Telegram bot token in Connections first.")
        fields = {"chat_id": recipient_id, **({"caption": caption[:1024]} if caption else {})}
        try:
            async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
                r = await c.post(f"{API}/bot{token}/sendPhoto", data=fields, files={"photo": (filename, data, mimetype)})
            body = r.json()
        except httpx.TimeoutException as e:  # the upload may have arrived
            return SendResult(ok=False, error=f"Telegram didn't answer ({type(e).__name__}).", uncertain=True)
        except (httpx.HTTPError, ValueError):
            return SendResult(ok=False, error="Can't reach Telegram. Check your internet connection.", retryable=True)
        if not body.get("ok"):
            return SendResult(ok=False, error=f"Telegram: {body.get('description', 'error')}")
        return SendResult(ok=True, external_id=f"{recipient_id}:{(body.get('result') or {}).get('message_id', '')}")

    def identity_for(self, value: str) -> tuple[str, str]:
        value = value.strip()
        if not re.fullmatch(r"-?\d{4,}", value):
            raise ChannelError("Telegram contacts are added by numeric user ID. Easiest: ask them to message your bot, "
                               "then enable them under Contacts → Discovered.")
        return value, ""
