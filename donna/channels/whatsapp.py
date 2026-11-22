"""WhatsApp through OpenWA (https://github.com/rmyndharis/OpenWA), a separate local gateway.

OpenWA runs WhatsApp Web (whatsapp-web.js) and owns the session; Donna talks to its REST API on
127.0.0.1:2785 with the API key from the vault, and receives events on a signed webhook:

- POST /api/sessions, /start, GET /api/sessions/{id}[/qr]                session "donna" and its QR
- POST /api/sessions/{id}/webhooks {url, events, secret}                 events to /api/v1/webhooks/whatsapp
- POST /api/sessions/{id}/messages/send-text {chatId, text}              replies (Idempotency-Key header)
- deliveries carry X-OpenWA-Signature: sha256=<HMAC-SHA256(raw body, secret)>

Only private chats are handled: groups, status updates, broadcasts and our own messages are dropped.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone

import httpx

from .. import vault
from .base import (BaseChannel, ChannelError, ChannelEvent, DeliveryUpdate, IncomingMessage, OutgoingMessage, SendResult,
                   StatusUpdate, WebhookRejected)

SESSION_NAME = "donna"
EVENTS = ["message.received", "message.ack", "session.status", "session.authenticated", "session.disconnected"]
MAX_BODY = 2 * 1024 * 1024
REPLAY_WINDOW = 600  # seconds; OpenWA retries within ~1 minute
STATUS = {"ready": "connected", "qr_ready": "qr", "created": "connecting", "initializing": "connecting",
          "authenticating": "connecting", "disconnected": "disconnected", "failed": "error", "action_required": "error"}
TYPES = {"chat": "text", "text": "text", "image": "image", "ptt": "audio", "audio": "audio", "voice": "audio",
         "video": "video", "document": "document", "sticker": "sticker", "location": "location"}


def webhook_url() -> str:
    return os.getenv("DONNA_URL", "http://127.0.0.1:8765").rstrip("/") + "/api/v1/webhooks/whatsapp"


def sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class WhatsAppChannel(BaseChannel):
    type = "whatsapp"
    label = "WhatsApp"
    max_text = 4000

    def __init__(self, *args, transport: httpx.AsyncBaseTransport | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._transport = transport  # tests inject httpx.MockTransport

    # ------------------------------------------------------------------ OpenWA API
    def _base(self) -> str:
        return (self.settings().get("openwa_url") or "http://127.0.0.1:2785").rstrip("/")

    async def _req(self, method: str, path: str, **kwargs):
        key = vault.get(vault.OPENWA_KEY)
        if not key:
            raise ChannelError("WhatsApp isn't set up yet. Open Connections → WhatsApp and click Connect.", 503)
        try:
            async with httpx.AsyncClient(base_url=self._base(), headers={"X-API-Key": key}, timeout=30,
                                         transport=self._transport) as c:
                r = await c.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ChannelError(f"Can't reach OpenWA at {self._base()}. Is it running? (scripts\\openwa.ps1)", 503,
                               retryable=True) from e
        if r.status_code in (401, 403):
            raise ChannelError("OpenWA rejected Donna's API key. Restart OpenWA (close its node.exe) and click Connect again.", 400)
        if r.status_code >= 400:
            try:
                detail = r.json().get("message") or r.text
            except ValueError:
                detail = r.text
            detail = detail if isinstance(detail, str) else "; ".join(map(str, detail))
            # A 5xx can come after WhatsApp already sent the message, so only a 429 (refused before
            # doing anything) is safe to retry. Connection errors above are retryable too.
            raise ChannelError(f"OpenWA: {detail[:200]}", r.status_code if r.status_code < 500 else 502,
                               retryable=r.status_code == 429)
        return r.json() if r.content else {}

    async def _session(self) -> dict:
        sid = self.config.get("session_id")
        if sid:
            try:
                return await self._req("GET", f"/api/sessions/{sid}")
            except ChannelError as e:
                if e.status != 404:
                    raise
        found = await self._req("GET", "/api/sessions", params={"name": SESSION_NAME})
        session = found[0] if found else await self._req("POST", "/api/sessions", json={"name": SESSION_NAME})
        self.save_config(session_id=session["id"])
        return session

    def _status(self, session: dict) -> StatusUpdate:
        status = STATUS.get(session.get("status", ""), "connecting")
        detail = session.get("lastError") or ""
        restriction = session.get("restriction") or {}
        if restriction:
            detail = f"WhatsApp restricted this account: {restriction.get('kind')} ({restriction.get('code')})"
        return StatusUpdate(status=status, detail=detail or "", account=session.get("phone") or "")

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> StatusUpdate:
        session = await self._session()
        sid = session["id"]
        if not session.get("engineLoaded"):
            try:
                await self._req("POST", f"/api/sessions/{sid}/start")
            except ChannelError as e:
                if e.status not in (400, 409):  # already starting/started
                    raise
        url = webhook_url()
        for hook in await self._req("GET", f"/api/sessions/{sid}/webhooks"):
            if hook.get("url") == url:  # re-register so OpenWA has the current secret
                await self._req("DELETE", f"/api/sessions/{sid}/webhooks/{hook['id']}")
        hook = await self._req("POST", f"/api/sessions/{sid}/webhooks",
                               json={"url": url, "events": EVENTS, "secret": vault.secret(vault.OPENWA_SECRET), "retryCount": 3})
        self.save_config(webhook_id=hook.get("id", ""))
        return await self.health_check()

    async def disconnect(self) -> None:
        sid = self.config.get("session_id")
        if not sid:
            return
        if self.config.get("webhook_id"):
            try:
                await self._req("DELETE", f"/api/sessions/{sid}/webhooks/{self.config['webhook_id']}")
            except ChannelError:
                pass
        try:
            await self._req("POST", f"/api/sessions/{sid}/stop")
        except ChannelError:
            pass
        self.save_config(webhook_id="")

    async def health_check(self) -> StatusUpdate:
        return self._status(await self._session())

    async def login_code(self) -> dict:
        session = await self._session()
        if session.get("status") != "qr_ready":
            return {"status": self._status(session).status}
        try:
            qr = await self._req("GET", f"/api/sessions/{session['id']}/qr")
        except ChannelError:
            return {"status": "connecting"}
        return {"status": "qr", "qr": qr.get("qrCode", "")}

    # ------------------------------------------------------------------ webhook
    def parse_webhook(self, raw: bytes, headers: dict[str, str]) -> list[ChannelEvent]:
        if len(raw) > MAX_BODY:
            raise WebhookRejected("Webhook body too large.", 413)
        secret = vault.get(vault.OPENWA_SECRET)
        if not secret:
            raise WebhookRejected("WhatsApp isn't connected.")
        if not hmac.compare_digest(headers.get("x-openwa-signature", ""), sign(secret, raw)):
            raise WebhookRejected("Invalid webhook signature.")
        try:
            body = json.loads(raw)
            sent = datetime.fromisoformat(str(body["timestamp"]).replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError) as e:
            raise WebhookRejected("Malformed webhook.", 400) from e
        if abs((datetime.now(timezone.utc) - sent).total_seconds()) > REPLAY_WINDOW:
            raise WebhookRejected("Stale webhook (possible replay).")
        session = self.config.get("session_id")
        if session and body.get("sessionId") and body["sessionId"] != session:
            return []
        event, data = body.get("event", ""), body.get("data") or {}
        if event == "message.received":
            msg = self.normalize(data)
            return [msg] if msg else []
        if event == "message.ack" and (data.get("messageId") or data.get("id")):
            return [DeliveryUpdate(external_id=str(data.get("messageId") or data.get("id")), status=str(data.get("status", "")))]
        if event == "session.authenticated":
            return [StatusUpdate(status="connected", account=str(data.get("phone") or ""))]
        if event == "session.disconnected":
            return [StatusUpdate(status="disconnected", detail=str(data.get("reason") or ""))]
        if event == "session.status":
            return [StatusUpdate(status=STATUS.get(str(data.get("status")), "connecting"))]
        return []

    def normalize(self, d: dict) -> IncomingMessage | None:
        """OpenWA's message.received data → IncomingMessage, or None for what Donna doesn't handle."""
        chat = str(d.get("chatId") or d.get("from") or "")
        if (d.get("fromMe") or d.get("isGroup") or d.get("isStatusBroadcast") or not chat or not d.get("id")
                or d.get("kind") not in (None, "individual") or chat.endswith(("@g.us", "@broadcast", "@newsletter"))):
            return None
        phone = re.sub(r"\D", "", str(d.get("senderPhone") or (chat.split("@")[0] if chat.endswith("@c.us") else "")))
        mtype = TYPES.get(str(d.get("type", "chat")), "other")
        media = d.get("media") or {}
        attachments = [{"type": mtype, "mimetype": media.get("mimetype", ""), "filename": media.get("filename", ""),
                        "size": media.get("sizeBytes")}] if media or mtype not in ("text",) else []
        contact = d.get("contact") or {}
        try:
            ts = datetime.fromtimestamp(int(d.get("timestamp")), timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        return IncomingMessage(
            event_id=f"msg:{d['id']}", channel_id=self.id, channel_type=self.type, sender_id=chat,
            sender_name=contact.get("pushName") or contact.get("name"), sender_phone=phone, chat_id=chat,
            message_id=str(d["id"]), message_type=mtype, text=(d.get("body") or None) if mtype != "location" else None,
            timestamp=ts, attachments=attachments,
            metadata={k: d[k] for k in ("isForwarded", "isLidSender", "quotedMessage") if d.get(k)})

    # ------------------------------------------------------------------ sending
    async def send(self, message: OutgoingMessage) -> SendResult:
        sid = self.config.get("session_id")
        if not sid:
            return SendResult(ok=False, error="WhatsApp isn't connected.")
        headers = {"Idempotency-Key": message.idempotency_key} if message.idempotency_key else {}
        try:
            r = await self._req("POST", f"/api/sessions/{sid}/messages/send-text",
                                json={"chatId": message.recipient_id, "text": message.text}, headers=headers)
        except ChannelError as e:
            # OpenWA answers 5xx when the engine failed mid-send ("may not have been delivered") and
            # 409 when an earlier attempt with this key did: either way it may be in the chat.
            uncertain = e.status in (409, 502)
            return SendResult(ok=False, error=str(e), retryable=e.retryable, uncertain=uncertain)
        return SendResult(ok=True, external_id=str(r.get("messageId", "")))

    def identity_for(self, value: str) -> tuple[str, str]:
        value = value.strip()
        if "@" in value:
            local = value.split("@")[0]
            return value, re.sub(r"\D", "", local) if value.endswith("@c.us") else ""
        phone = re.sub(r"\D", "", value)
        if len(phone) < 8:
            raise ChannelError("Enter the WhatsApp number with its country code, e.g. +91 98123 45678.")
        return f"{phone}@c.us", phone

    def format_text(self, markdown: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", markdown)              # bold
        text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)    # headings → bold lines
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
        text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
        return text.strip()
