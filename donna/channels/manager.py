"""ChannelManager: every messaging channel goes through here, whatever the platform.

Inbound, for each message (doc: "unauthorized messages are rejected before they reach the LLM"):
 1. the adapter verifies and normalises the platform event;
 2. deduplicate: INSERT OR IGNORE into channel_events, UNIQUE(channel, external id); a duplicate stops here;
 3. acknowledge the platform at once; process in the background, one turn at a time per chat and,
    to respect Groq's free tier, one model turn at a time overall;
 4. checks before any model call: global kill switch, channel pause, who the sender is (unknown →
    a disabled, discovered contact), the contact's mode and pause, working hours and rate limits;
 5. by mode: disabled → stored only; human_first → the owner is alerted, Donna stays silent;
    suggest → Donna drafts, the owner approves the reply; auto → Donna answers within the
    contact's capabilities, gated tools become owner approvals;
 6. a model failure is never sent to the contact: the event fails, the owner is told, the Inbox can retry.

Outbound goes through send_text: kill switch and pause checks, platform formatting, splitting,
one retry with the same idempotency key, and a delivery record per message.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import approvals, audit, availability, contact_requests, contacts, identity, llm, memory, openwa, runtime_tools
from ..agent import call_tool, find_tool
from ..notify import Note
from ..permissions import TurnContext
from ..runtime import run_agent_turn
from .base import BaseChannel, ChannelError, DeliveryUpdate, IncomingMessage, OutgoingMessage, SendResult, StatusUpdate
from .registry import REGISTRY

log = logging.getLogger("donna.channels")
HEALTH_SECONDS = 60
APPROVAL_CMD = re.compile(r"^\s*(approve|reject)\b\s*([A-Za-z0-9]{4})?\s*[.!]?\s*$", re.I)
# Requests (R12) answered from the owner's phone: "approve R12" books a meeting request, "decline R12" turns it down.
REQUEST_CMD = re.compile(r"^\s*(approve|book|accept|decline|reject)\s+(R\d+)\s*[.!]?\s*$", re.I)
DELIVERY_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 1}


def _a(word: str) -> str:
    return f"an {word}" if word[:1] in "aeiou" else f"a {word}"


class ChannelManager:
    def __init__(self, store, hub, notifier=None, adapter_kwargs: dict | None = None):
        self.store = store
        self.hub = hub
        self.notifier = notifier
        self.adapter_kwargs = adapter_kwargs or {}  # tests: {"whatsapp": {"transport": MockTransport}}
        # Start OpenWA when WhatsApp is connected; tests (which inject a fake gateway) turn this off.
        self.manage_gateway = "whatsapp" not in self.adapter_kwargs
        self.adapters: dict[str, BaseChannel] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._llm = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()
        self._health: asyncio.Task | None = None
        self.whatsapp_setup = openwa.Setup(lambda: self.connect("whatsapp"))  # the Connect button's job
        runtime_tools.services.store, runtime_tools.services.channels = store, self  # requests notify the owner through us

    # ================================================================== channel rows
    def row(self, channel_id: str) -> dict | None:
        r = self.store.row("SELECT * FROM channels WHERE id=?", (channel_id,))
        if r:
            r["config"] = json.loads(r["config"] or "{}")
            r["enabled"], r["paused"] = bool(r["enabled"]), bool(r["paused"])
        return r

    def list(self) -> list[dict]:
        """Every known channel type, connected or not, for the dashboard."""
        out = []
        for ctype, cls in REGISTRY.items():
            r = self.row(ctype) or {"id": ctype, "type": ctype, "display_name": cls.label, "enabled": False, "paused": False,
                                    "status": "not_set_up", "config": {}, "last_error": "", "last_activity_at": None}
            cfg = r.pop("config")
            r["account"] = cfg.get("account", "")
            r["label"] = cls.label
            r["credentials"] = self._has_credentials(ctype)
            if ctype == "whatsapp":
                r["setup"] = self.whatsapp_setup.snapshot()
            out.append(r)
        return out

    @staticmethod
    def _has_credentials(ctype: str) -> bool:
        from .. import vault
        return vault.has(vault.OPENWA_KEY) if ctype == "whatsapp" else vault.has(vault.TELEGRAM_TOKEN) if ctype == "telegram" else False

    def _ensure_row(self, ctype: str) -> dict:
        if ctype not in REGISTRY:
            raise ChannelError(f"Unknown channel type: {ctype}", 404)
        now = time.time()
        self.store.execute("INSERT OR IGNORE INTO channels (id, type, display_name, enabled, created_at, updated_at) "
                           "VALUES (?,?,?,0,?,?)", (ctype, ctype, REGISTRY[ctype].label, now, now))
        return self.row(ctype)

    def _save_config(self, channel_id: str, config: dict) -> None:
        self.store.execute("UPDATE channels SET config=?, updated_at=? WHERE id=?", (json.dumps(config), time.time(), channel_id))

    def adapter(self, channel_id: str) -> BaseChannel:
        if channel_id not in self.adapters:
            if not self.row(channel_id) and channel_id not in REGISTRY:
                raise ChannelError(f"Unknown channel: {channel_id}", 404)
            r = self.row(channel_id) or {"type": channel_id, "config": {}}  # not connected yet: nothing is saved
            cls = REGISTRY[r["type"]]
            self.adapters[channel_id] = cls(channel_id, r["config"], self.store.settings,
                                            lambda events, cid=channel_id: self.ingest(cid, events),
                                            lambda cfg, cid=channel_id: self._save_config(cid, cfg),
                                            **self.adapter_kwargs.get(r["type"], {}))
        return self.adapters[channel_id]

    def label(self, channel_id: str) -> str:
        cls = REGISTRY.get((self.row(channel_id) or {}).get("type", channel_id))
        return cls.label if cls else channel_id

    # ================================================================== lifecycle
    async def start(self) -> None:
        interrupted = self.store.rows("SELECT id, received_at FROM channel_events WHERE status IN ('received', 'processing')")
        self.store.execute(
            "UPDATE channel_events SET status='failed', result='Interrupted by a restart. Retry it from the Inbox.', "
            "processed_at=? WHERE status IN ('received', 'processing')", (time.time(),))
        if interrupted:
            log.warning("%d channel events were interrupted by a restart", len(interrupted))
        retry_ids = [e["id"] for e in interrupted if e["received_at"] > time.time() - 15 * 60 and self._unanswered(e["id"])]
        approvals.expire(self.store)
        audit.prune(self.store)
        for r in self.store.rows("SELECT id FROM channels WHERE enabled=1"):
            if r["id"] == "whatsapp" and self.manage_gateway:
                self.spawn(self.whatsapp_setup.ensure_running())  # Donna owns the OpenWA process
            try:
                await self.adapter(r["id"]).start()
            except Exception as e:  # noqa: BLE001 - one broken channel mustn't stop Donna
                self._apply_status(r["id"], StatusUpdate(status="error", detail=str(e)[:300]))
        self._health = asyncio.create_task(self._health_loop(), name="channel-health")
        for event_id in retry_ids:  # the person is still waiting for an answer: try once more
            await self.retry(event_id)

    def _unanswered(self, event_id: str) -> bool:
        """Nothing was sent in that chat after this message arrived, so retrying can't answer it twice."""
        inbound = self.store.row("SELECT channel_conversation_id, created_at FROM channel_messages WHERE event_id=?", (event_id,))
        if not inbound:
            return True
        return not self.store.row("SELECT 1 FROM channel_messages WHERE channel_conversation_id=? AND direction='out' "
                                  "AND created_at>=?", (inbound["channel_conversation_id"], inbound["created_at"]))

    async def stop(self) -> None:
        if self._health:
            self._health.cancel()
        for a in self.adapters.values():
            await a.stop()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def idle(self) -> None:
        """Wait until background processing is done (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_SECONDS)
            for r in self.store.rows("SELECT id FROM channels WHERE enabled=1"):
                await self.refresh(r["id"])

    async def refresh(self, channel_id: str) -> dict:
        try:
            self._apply_status(channel_id, await self.adapter(channel_id).health_check())
        except ChannelError as e:
            self._apply_status(channel_id, StatusUpdate(status="error", detail=str(e)))
        except Exception as e:  # noqa: BLE001
            self._apply_status(channel_id, StatusUpdate(status="error", detail=f"{type(e).__name__}: {e}"[:300]))
        return self.public(channel_id)

    def public(self, channel_id: str) -> dict:
        return next(c for c in self.list() if c["id"] == channel_id)

    def _apply_status(self, channel_id: str, st: StatusUpdate) -> None:
        r = self.row(channel_id)
        if not r:
            return
        cfg = dict(r["config"])
        if st.account:
            cfg["account"] = st.account
        self.store.execute("UPDATE channels SET status=?, last_error=?, config=?, updated_at=? WHERE id=?",
                           (st.status, st.detail[:500] if st.status in ("error", "disconnected") else "", json.dumps(cfg),
                            time.time(), channel_id))
        if channel_id in self.adapters:
            self.adapters[channel_id].config = cfg
        if r["status"] != st.status:
            audit.record(self.store, f"channel:{channel_id}", "channel.status", channel_id, status=st.status, detail=st.detail)
            if r["status"] == "connected" and st.status in ("disconnected", "error"):
                self.spawn(self.notify_owner(f"{self.label(channel_id)} disconnected",
                                             st.detail or "Donna can't send or receive messages there until it reconnects. "
                                                          "Reconnect it in Connections.", level="warn", chat=False))

    async def connect(self, channel_type: str) -> dict:
        self._ensure_row(channel_type)
        a = self.adapter(channel_type)
        st = await a.connect()  # raises ChannelError with a readable message
        self.store.execute("UPDATE channels SET enabled=1, updated_at=? WHERE id=?", (time.time(), channel_type))
        self._apply_status(channel_type, st)
        await a.start()
        audit.record(self.store, "owner", "channel.connect", channel_type, status=st.status)
        return self.public(channel_type)

    async def disconnect(self, channel_id: str) -> dict:
        if self.row(channel_id):
            a = self.adapter(channel_id)
            try:
                await a.disconnect()
            except ChannelError as e:
                log.warning("Disconnecting %s: %s", channel_id, e)
            await a.stop()
            self.store.execute("UPDATE channels SET enabled=0, status='disconnected', updated_at=? WHERE id=?",
                               (time.time(), channel_id))
            audit.record(self.store, "owner", "channel.disconnect", channel_id)
        return self.public(channel_id)

    def pause(self, channel_id: str, paused: bool) -> dict:
        if not self.row(channel_id):
            raise ChannelError("Connect the channel first.", 404)
        self.store.execute("UPDATE channels SET paused=?, updated_at=? WHERE id=?", (1 if paused else 0, time.time(), channel_id))
        audit.record(self.store, "owner", "channel.pause" if paused else "channel.resume", channel_id)
        return self.public(channel_id)

    async def login_code(self, channel_id: str) -> dict:
        if not self.row(channel_id):
            raise ChannelError("Connect the channel first.", 404)
        return await self.adapter(channel_id).login_code()

    # ================================================================== inbound
    async def handle_webhook(self, channel_id: str, raw: bytes, headers: dict[str, str]) -> dict:
        r = self.row(channel_id)
        if not r or not r["enabled"]:
            from .base import WebhookRejected
            raise WebhookRejected(f"{channel_id} isn't connected.", 404)
        events = self.adapter(channel_id).parse_webhook(raw, headers)  # raises WebhookRejected
        return await self.ingest(channel_id, events)

    async def ingest(self, channel_id: str, events: list) -> dict:
        accepted = duplicates = 0
        for ev in events:
            if isinstance(ev, IncomingMessage):
                event_id = self._record_event(ev)
                if event_id:
                    accepted += 1
                    self.spawn(self.process(event_id))
                else:
                    duplicates += 1
            elif isinstance(ev, StatusUpdate):
                self._apply_status(channel_id, ev)
            elif isinstance(ev, DeliveryUpdate):
                self._delivery(ev)
        if events:
            self.store.execute("UPDATE channels SET last_activity_at=? WHERE id=?", (time.time(), channel_id))
        return {"accepted": accepted, "duplicates": duplicates}

    def _record_event(self, msg: IncomingMessage) -> str | None:
        event_id = uuid.uuid4().hex[:16]
        cur = self.store.execute(
            "INSERT OR IGNORE INTO channel_events (id, channel_id, external_event_id, payload, received_at) VALUES (?,?,?,?,?)",
            (event_id, msg.channel_id, msg.event_id, msg.model_dump_json(), time.time()))
        return event_id if cur.rowcount == 1 else None

    def _delivery(self, ev: DeliveryUpdate) -> None:
        status = {"pending": "sent"}.get(ev.status, ev.status)
        if status not in DELIVERY_RANK:
            return
        for m in self.store.rows("SELECT id, status FROM channel_messages WHERE external_id=? AND direction='out'", (ev.external_id,)):
            if DELIVERY_RANK.get(status, 0) > DELIVERY_RANK.get(m["status"], 0) or status == "failed":
                self.store.execute("UPDATE channel_messages SET status=? WHERE id=?", (status, m["id"]))

    async def retry(self, event_id: str) -> None:
        cur = self.store.execute("UPDATE channel_events SET status='received', result='' WHERE id=? AND status='failed'", (event_id,))
        if cur.rowcount != 1:
            raise ChannelError("Only a failed message can be retried.", 409)
        audit.record(self.store, "owner", "channel.retry", event_id)
        self.spawn(self.process(event_id))

    async def process(self, event_id: str) -> None:
        try:
            await self._process(event_id)
        except Exception as e:  # noqa: BLE001 - mark it failed instead of losing it
            log.exception("Processing channel event %s failed", event_id)
            self._finish(event_id, "failed", f"Error: {type(e).__name__}: {e}"[:500])

    def _finish(self, event_id: str, status: str, result: str, inbound_id: str | None = None, inbound_status: str | None = None) -> None:
        self.store.execute("UPDATE channel_events SET status=?, result=?, processed_at=? WHERE id=?",
                           (status, result[:1000], time.time(), event_id))
        if inbound_id and inbound_status:
            self.store.execute("UPDATE channel_messages SET status=?, error=? WHERE id=?",
                               (inbound_status, result[:500] if inbound_status == "failed" else "", inbound_id))

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _process(self, event_id: str) -> None:
        ev = self.store.row("SELECT * FROM channel_events WHERE id=?", (event_id,))
        if not ev:
            return
        msg = IncomingMessage.model_validate_json(ev["payload"])
        self.store.execute("UPDATE channel_events SET status='processing' WHERE id=?", (event_id,))
        settings = self.store.settings()
        channel = self.row(msg.channel_id) or {}
        contact, created = contacts.resolve(self.store, msg.channel_id, msg.sender_id, msg.sender_phone, msg.sender_name or "")
        conv = self.conversation_for(msg.channel_id, contact, msg.chat_id)
        inbound = self._inbound(conv, event_id, msg)
        contacts.touch(self.store, contact["id"])
        name, label = contact["display_name"], self.label(msg.channel_id)

        def done(status: str, result: str, in_status: str) -> None:
            self._finish(event_id, status, result, inbound, in_status)

        if not settings.get("messaging_enabled", True):
            return done("ignored", "Messaging is switched off (kill switch).", "ignored")
        if channel.get("paused"):
            return done("ignored", f"{label} is paused.", "ignored")
        text = self._agent_text(msg)
        if contact["is_owner"]:
            return await self._owner_turn(event_id, inbound, msg, contact, conv, text, settings)
        if created and settings.get("unknown_contact_action") == "notify":
            await self.notify_owner(f"New {label} message from {name}", f"{(msg.text or '')[:300]}\nEnable them in Contacts "
                                    "if Donna should answer.", chat=False)
        if contact["mode"] == "disabled" or contact["paused"]:
            audit.record(self.store, f"contact:{contact['id']}", "message.ignored", msg.message_id, outcome="denied",
                         reason="paused" if contact["paused"] else "disabled")
            return done("ignored", "Contact is disabled." if not contact["paused"] else "Contact is paused.", "ignored")
        if contact["mode"] == "human_first":
            self.store.add_message(conv["conversation_id"], "user", text)  # context for your reply, or a later mode change
            self.store.execute("UPDATE channel_conversations SET escalated=1 WHERE id=?", (conv["id"],))
            await self.notify_owner(f"{name} wrote on {label}", (msg.text or text)[:500], level="warn",
                                    conversation_id=conv["conversation_id"])
            return done("done", "Passed to you (human first).", "escalated")
        if not text.strip():
            return done("ignored", "Empty message.", "ignored")

        # The away menu: a tapped poll option (or "2" right after the greeting) starts a guided flow, and the
        # flow's follow-up messages reach Donna without the trigger word.
        flow = self._flow(conv)
        if msg.message_type == "choice" or (not flow and self._menu_open(conv)):
            if opt := self._menu_choice(conv, msg, text, settings):
                flow = opt["id"]
                self.store.execute("UPDATE channel_conversations SET menu_flow=?, menu_flow_at=? WHERE id=?",
                                   (flow, time.time(), conv["id"]))
                audit.record(self.store, f"contact:{contact['id']}", "menu.choice", conv["id"], option=flow)
                text = availability.option_text(opt)
            elif msg.message_type == "choice":
                return done("ignored", "A vote that isn't on Donna's current menu.", "ignored")
        mentioned = identity.mentions_trigger(text, settings)
        if mentioned:
            text = identity.strip_trigger(text, settings) or "Hi"
        elif not flow:
            if await self._maybe_greet(conv, contact, settings):
                return done("done", "You're away: sent the greeting and menu.", "answered")
            if settings.get("require_trigger", True):
                # The owner's own WhatsApp: people chat with him as usual and reach Donna only with the trigger word.
                if not conv.get("trigger_hint_at"):  # told once per chat, never again
                    self.store.execute("UPDATE channel_conversations SET trigger_hint_at=? WHERE id=?", (time.time(), conv["id"]))
                    await self.send_text(conv, identity.trigger_hint(contact, settings, self.store), author="donna")
                return done("ignored", f"No {identity.trigger_of(settings)}: left for you.", "ignored")
        turn_started = time.time()
        flow_note = availability.flow_instructions(flow, settings, self.store) if flow else ""

        mode, why = contact["mode"], ""
        if mode == "auto" and not contacts.in_working_hours(contact, settings.get("timezone") or "UTC"):
            mode, why = "suggest", "outside their working hours"
        if mode == "auto" and self._over_rate(contact["id"], settings):
            mode, why = "suggest", "the reply rate limit was reached"
        intro_needed = identity.needs_introduction(self.store, conv)
        ctx = TurnContext(audience="contact", channel_id=msg.channel_id, channel_label=label, contact=contact,
                          channel_conversation_id=conv["id"],
                          capabilities=contacts.effective_capabilities(self.store, contact["id"], msg.channel_id),
                          suggest_only=mode == "suggest", persist_reply=mode == "auto",
                          extra_prompt=self._summary_block(conv) + (f"\n\n{flow_note}" if flow_note else ""),
                          history_since=conv["summary_upto"], intro_needed=intro_needed)
        self.store.execute("UPDATE channel_messages SET status='processing' WHERE id=?", (inbound,))
        async with self._lock(conv["id"]), self._llm:
            res = await run_agent_turn(self.store, self.hub, conv["conversation_id"], text, ctx)
        if res.error:
            await self.notify_owner(f"Donna couldn't answer {name}", f"{res.error}\nRetry it from the Inbox.", level="error",
                                    conversation_id=conv["conversation_id"], chat=False)
            return done("failed", res.error, "failed")
        reply = res.text.strip()
        if reply and intro_needed:  # enforced, not just asked: the first reply always says who Donna is
            reply = identity.with_introduction(reply, identity.introduction(contact, settings, self.store))
            if res.message and res.message.get("id"):
                self.store.execute("UPDATE messages SET content=? WHERE id=?", (reply, res.message["id"]))
        for code in res.approvals:
            await self._alert_approval(code)
        if flow:
            self._advance_flow(conv["id"], turn_started)
        if mode == "auto":
            if reply:
                sent = await self.send_text(conv, reply, author="donna")
                if not sent.ok and not sent.uncertain:
                    return done("failed", f"Reply not delivered: {sent.error}", "failed")
            done("done", "Answered.", "answered")
        else:
            if reply:
                req = approvals.create(self.store, ctx, "reply", args={"text": reply},
                                       description=f"Reply to {name}: {reply[:160]}")
                await self.notify_owner(
                    f"Suggested reply to {name}" + (f" ({why})" if why else ""),
                    f"{name}: {(msg.text or text)[:200]}\nDonna: {reply[:400]}\n\n"
                    f"Reply \"approve {req['id']}\" to send it, or \"reject {req['id']}\".",
                    conversation_id=conv["conversation_id"])
            done("done", "Drafted a reply for your approval." + (f" ({why})" if why else ""), "drafted")
        self.spawn(self._summarize(conv["id"]))

    async def _owner_turn(self, event_id, inbound, msg, contact, conv, text, settings) -> None:
        """The owner writing from their own phone: status, request and approval commands are handled in code,
        everything else is a normal owner turn."""
        reply = None
        try:
            if parsed := availability.parse_command(text, settings):
                availability.set_status(self.store, *parsed)
                st = await availability.effective(self.store)
                reply = f"Status set: {st['emoji']} {st['label']}" + (f" ({st['detail']})" if st["detail"] else "") \
                    + availability.until_text(st["until"], settings) + "."
                audit.record(self.store, "owner:chat", "availability.set", st["mode"])
        except availability.AvailabilityError as e:
            reply = str(e)
        if reply is None and (m := REQUEST_CMD.match(text)):
            reply = await self._request_command(m.group(1).lower(), m.group(2))
        if reply is not None:
            await self.send_text(conv, reply, author="donna")
            self._finish(event_id, "done", reply, inbound, "answered")
            return
        if m := APPROVAL_CMD.match(text):
            reply = await self._approval_command(m.group(1).lower(), m.group(2))
            await self.send_text(conv, reply, author="donna")
            self._finish(event_id, "done", reply, inbound, "answered")
            return
        ctx = TurnContext(audience="owner", channel_id=msg.channel_id, channel_label=self.label(msg.channel_id), contact=contact,
                          channel_conversation_id=conv["id"], extra_prompt=self._summary_block(conv),
                          history_since=conv["summary_upto"])
        async with self._lock(conv["id"]), self._llm:
            res = await run_agent_turn(self.store, self.hub, conv["conversation_id"], text, ctx,
                                       mode=settings.get("permission_mode", "manual"))
        reply = res.text.strip()
        if res.error:
            reply = (reply + "\n\n" if reply else "") + f"⚠ {res.error}"
        for code in res.approvals:
            a = approvals.get(self.store, code)
            reply += f"\n\nNeeds your OK ({code}): {a['description']}\nReply \"approve {code}\" or \"reject {code}\"."
        if reply.strip():
            await self.send_text(conv, reply.strip(), author="donna")
        self._finish(event_id, "failed" if res.error else "done", res.error or "Answered.", inbound,
                     "failed" if res.error else "answered")
        self.spawn(self._summarize(conv["id"]))

    async def _approval_command(self, verb: str, code: str | None) -> str:
        if not code:
            pending = approvals.list_all(self.store, "pending", limit=2)
            if len(pending) != 1:
                return ("Nothing is waiting for your approval." if not pending
                        else "Several requests are waiting; reply with the code, e.g. \"approve " + pending[0]["id"] + "\".")
            code = pending[0]["id"]
        try:
            if verb == "reject":
                await self.reject(code, "owner:chat")
                return f"Rejected {code.upper()}."
            row = await self.approve(code, "owner:chat")
        except approvals.ApprovalError as e:
            return str(e)
        if row["status"] == "executed":
            return f"Done ({row['id']}): {row['result'][:600]}"
        return f"Couldn't do it ({row['id']}): {row['result'][:600]}"

    async def _request_command(self, verb: str, rid: str) -> str:
        try:
            req = contact_requests.get(self.store, rid)
        except contact_requests.RequestError:
            req = None
        if not req:
            return f"There's no request {rid.upper()}."
        if req["status"] not in contact_requests.OPEN:
            return f"{req['code']} is already {req['status'].replace('_', ' ')}."
        if verb in ("decline", "reject"):
            r = await self.decline_request(req, actor="owner:chat")
            return f"Declined {r['code']}" + (f" and told {req.get('contact_name') or 'them'}." if r["contact_informed"] else ".")
        if req["type"] != "meeting":
            r = contact_requests.set_status(self.store, req["id"], "approved", None, "owner:chat")
            informed = await self.inform_about_request(r)
            return f"Marked {r['code']} as approved" + (f" and told {req.get('contact_name') or 'them'}." if informed else ".")
        return (await self.approve_meeting(req, actor="owner:chat"))["message"]

    # ================================================================== away greeting + menu
    def _flow(self, conv: dict) -> str:
        """The menu option this chat is working through, while it's still active."""
        if conv.get("menu_flow") and time.time() - (conv.get("menu_flow_at") or 0) < availability.FLOW_SECONDS:
            return conv["menu_flow"]
        return ""

    @staticmethod
    def _menu_open(conv: dict) -> bool:
        return bool(conv.get("greeted_at")) and time.time() - conv["greeted_at"] < availability.MENU_OPEN_SECONDS \
            and conv.get("menu_options", "[]") != "[]"

    def _menu_choice(self, conv: dict, msg: IncomingMessage, text: str, settings: dict) -> dict | None:
        """The option picked: a poll vote (by its text) or a typed reply ("2", "meeting"), from the menu as sent."""
        sent = json.loads(conv.get("menu_options") or "[]")
        options = [{"id": i, **availability.MENU[i]} for i in sent if i in availability.MENU]
        if not options:
            options = availability.menu_options(settings)
        if msg.message_type == "choice":
            return availability.option_by_vote(text, options)
        return availability.parse_choice(text, options)

    def _advance_flow(self, channel_conversation_id: str, turn_started: float) -> None:
        """Keep the flow going, or end it once its request reached the owner in this turn."""
        done = self.store.row("SELECT 1 FROM requests WHERE channel_conversation_id=? AND updated_at>=? "
                              "AND status IN ('waiting_for_owner', 'cancelled')", (channel_conversation_id, turn_started))
        self.store.execute("UPDATE channel_conversations SET menu_flow=?, menu_flow_at=? WHERE id=?",
                           ("" if done else self.conversation(channel_conversation_id)["menu_flow"], time.time(),
                            channel_conversation_id))

    async def _maybe_greet(self, conv: dict, contact: dict, settings: dict) -> bool:
        """While the owner is away, a saved contact gets one greeting (and the menu) per status period."""
        if not settings.get("greeting_enabled", True) or contact.get("discovered") or contact.get("is_owner"):
            return False
        status = await availability.effective(self.store)
        if not status["away"] or conv.get("greeted_key") == status["key"]:
            return False
        options = availability.menu_options(settings)
        text = availability.greeting(contact, settings, self.store, status)
        self.store.execute("UPDATE channel_conversations SET greeted_key=?, greeted_at=?, menu_options=?, menu_flow='', "
                           "trigger_hint_at=COALESCE(trigger_hint_at, ?) WHERE id=?",
                           (status["key"], time.time(), json.dumps([o["id"] for o in options]), time.time(), conv["id"]))
        self.store.add_message(conv["conversation_id"], "assistant", text)  # so a later turn knows what was said
        sent = await self.send_text(conv, text, author="donna")
        if sent.ok and options:
            await self.send_menu(conv, options)
        audit.record(self.store, f"contact:{contact['id']}", "menu.greeting", conv["id"], status=status["mode"],
                     outcome="ok" if sent.ok else "failed")
        return True

    async def send_menu(self, conv: dict, options: list[dict]) -> SendResult:
        """The away menu: a WhatsApp poll, or numbered text where polls aren't available."""
        settings = self.store.settings()
        channel = self.row(conv["channel_id"])
        if not settings.get("messaging_enabled", True) or not channel or not channel["enabled"] or channel["paused"]:
            return SendResult(ok=False, error="The channel isn't available.")
        labels = [availability.option_text(o) for o in options]
        fallback = availability.menu_fallback(options)
        mid = uuid.uuid4().hex[:12]
        self.store.execute("INSERT INTO channel_messages (id, channel_conversation_id, direction, author, message_type, content, "
                           "status, created_at) VALUES (?,?,'out','donna','menu',?,'queued',?)", (mid, conv["id"], fallback, time.time()))
        result = await self.adapter(conv["channel_id"]).send_menu(conv["external_chat_id"], availability.MENU_QUESTION, labels,
                                                                  fallback, idempotency_key=mid)
        self.store.execute("UPDATE channel_messages SET status=?, external_id=?, error=? WHERE id=?",
                           ("sent" if result.ok else "unconfirmed" if result.uncertain else "failed", result.external_id,
                            result.error[:500], mid))
        return result

    # ================================================================== meeting requests
    async def approve_meeting(self, req: dict, start: str = "", end: str = "", title: str = "", actor: str = "owner") -> dict:
        """Book a meeting request (at the time the contact confirmed, unless given), then send them the Meet link.
        Returns {"ok", "message", "request", "booked", "informed"}."""
        start = (start or req["details"].get("start") or "")[:16]
        if not start:
            return {"ok": False, "message": f"{req['code']} has no time yet; open it in the Inbox to pick one."}
        if not end:
            minutes = int(req["details"].get("duration_minutes") or self.store.settings().get("meeting_default_minutes") or 30)
            end = (datetime.fromisoformat(start) + timedelta(minutes=minutes)).isoformat()[:16]
        who = req.get("contact_name") or "contact"
        topic = req["details"].get("topic", "")
        booked = await self.book_for_request(req, start, end, title or (f"{topic} · {who}" if topic else ""), True)
        if not booked["ok"]:
            return {"ok": False, "message": f"Couldn't book {req['code']}: {booked['result'][:300]}"}
        when = f"{contact_requests.fmt_local(booked['start'])} to {contact_requests.fmt_local(booked['end']).split(', ')[-1]}"
        link = booked["meet"]
        confirmation = f"📅 {when}" + (f"\nGoogle Meet: {link}" if link else "")
        outcome = f"{'Moved to' if booked['moved'] else 'Booked for'} {when}." + (f" Google Meet link: {link}" if link else "")
        r = contact_requests.set_status(self.store, req["id"], "completed", outcome, actor)
        informed = await self.inform_about_request(r, must_include=confirmation)
        return {"ok": True, "request": r, "booked": booked, "informed": informed,
                "message": f"{'Moved' if booked['moved'] else 'Booked'} {req['code']} with {who}: {when}"
                           + (f"\n{link}" if link else "") + ("\nI've sent them the details." if informed
                                                              else "\nI couldn't message them; send the link yourself.")}

    async def decline_request(self, req: dict, note: str = "", actor: str = "owner") -> dict:
        r = contact_requests.set_status(self.store, req["id"], "rejected", note or None, actor)
        r["contact_informed"] = await self.inform_about_request(r)
        return r

    async def cancel_booking(self, req: dict) -> tuple[bool, str]:
        """A contact cancelled their own booked meeting: delete the event (invites are withdrawn) and tell the owner."""
        tool = find_tool(self.hub, "delete_event", self.store.settings())
        if not tool:
            return False, "Google Calendar isn't connected."
        ok, out = await call_tool(self.hub, tool, {"event_id": req["event_id"]}, None, self.store)
        audit.record(self.store, f"contact:{req['contact_id']}", "request.meeting_cancelled", req["code"],
                     outcome="ok" if ok else "failed", start=req["event_start"], result=(out or "")[:300])
        if ok:
            who = req.get("contact_name") or "A contact"
            conv = self.conversation(req.get("channel_conversation_id") or "") or {}
            await self.notify_owner(f"{who} cancelled the meeting",
                                    f"{contact_requests.fmt_local(req['event_start'])}: {req['summary']}\n"
                                    "It's been removed from your calendar.", conversation_id=conv.get("conversation_id"))
        return ok, out or ""

    def _inbound(self, conv: dict, event_id: str, msg: IncomingMessage) -> str:
        existing = self.store.row("SELECT id FROM channel_messages WHERE event_id=?", (event_id,))
        if existing:  # a retried event keeps its message
            return existing["id"]
        mid = uuid.uuid4().hex[:12]
        self.store.execute(
            "INSERT INTO channel_messages (id, channel_conversation_id, event_id, external_id, direction, author, message_type, "
            "content, attachments, status, created_at) VALUES (?,?,?,?,'in','contact',?,?,?,'received',?)",
            (mid, conv["id"], event_id, msg.message_id, msg.message_type, msg.text or "", json.dumps(msg.attachments), time.time()))
        self.store.execute("UPDATE channel_conversations SET unread=unread+1, last_message_at=?, status='active', updated_at=? "
                           "WHERE id=?", (time.time(), time.time(), conv["id"]))
        return mid

    @staticmethod
    def _agent_text(msg: IncomingMessage) -> str:
        text = (msg.text or "").strip()
        if msg.message_type in ("text", "choice"):
            return text
        name = next((a.get("filename") for a in msg.attachments if a.get("filename")), "")
        note = (f"[They sent {_a(msg.message_type)}{f' ({name})' if name else ''}, which Donna can't open yet; "
                "say so if it matters.]")
        return f"{note} {text}".strip()

    def _over_rate(self, contact_id: str, settings: dict) -> bool:
        since = time.time() - 3600
        # Only replies to contacts count: alerts and answers in the owner's own chat don't use up the limit.
        sql = ("SELECT COUNT(*) AS n FROM channel_messages m JOIN channel_conversations c ON c.id=m.channel_conversation_id "
               "JOIN contacts k ON k.id=c.contact_id WHERE m.direction='out' AND m.author='donna' AND k.is_owner=0 "
               "AND m.created_at>?")
        mine = self.store.row(sql + " AND c.contact_id=?", (since, contact_id))["n"]
        total = self.store.row(sql, (since,))["n"]
        return mine >= int(settings.get("contact_rate_per_hour") or 10) or total >= int(settings.get("global_rate_per_hour") or 60)

    @staticmethod
    def _summary_block(conv: dict) -> str:
        return f"\nEarlier in this conversation (summary):\n{conv['summary']}" if conv.get("summary") else ""

    async def _summarize(self, channel_conversation_id: str, force: bool = False) -> str | None:
        conv = self.conversation(channel_conversation_id)
        if not conv:
            return None
        async with self._lock(conv["id"]), self._llm:
            try:
                return await memory.summarize(self.store, conv, force=force)
            except llm.LLMError as e:
                log.info("Summary skipped: %s", e)
                return None

    # ================================================================== conversations
    def conversation(self, channel_conversation_id: str) -> dict | None:
        return self.store.row("SELECT * FROM channel_conversations WHERE id=?", (channel_conversation_id,))

    def conversation_for(self, channel_id: str, contact: dict, chat_id: str) -> dict:
        conv = self.store.row("SELECT * FROM channel_conversations WHERE channel_id=? AND external_chat_id=?", (channel_id, chat_id))
        if conv:
            if conv["contact_id"] != contact["id"]:
                self.store.execute("UPDATE channel_conversations SET contact_id=? WHERE id=?", (contact["id"], conv["id"]))
                conv["contact_id"] = contact["id"]
            return conv
        now = time.time()
        title = f"{self.label(channel_id)} · {contact['display_name']}"[:120]
        cid = self.store.create_conversation(title, source=channel_id)["id"]
        ccid = uuid.uuid4().hex[:12]
        self.store.execute("INSERT INTO channel_conversations (id, channel_id, contact_id, external_chat_id, conversation_id, "
                           "created_at, updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(channel_id, external_chat_id) DO NOTHING",
                           (ccid, channel_id, contact["id"], chat_id, cid, now, now))
        conv = self.store.row("SELECT * FROM channel_conversations WHERE channel_id=? AND external_chat_id=?", (channel_id, chat_id))
        if conv["id"] != ccid:  # created concurrently: drop our spare conversation row
            self.store.delete_conversation(cid)
        return conv

    # ================================================================== outbound
    async def send_text(self, conv: dict, text: str, author: str = "donna") -> SendResult:
        """Send one reply into a channel conversation (split when long) and record its delivery."""
        settings = self.store.settings()
        channel = self.row(conv["channel_id"])
        block = ("Messaging is switched off (kill switch)." if not settings.get("messaging_enabled", True)
                 else f"{self.label(conv['channel_id'])} isn't connected." if not channel or not channel["enabled"]
                 else f"{self.label(conv['channel_id'])} is paused." if channel["paused"] else "")
        adapter = self.adapter(conv["channel_id"])
        if author == "donna":
            text = identity.clean(text, identity.owner_name(settings, self.store), settings.get("owner_pronouns", ""))
            who = contacts.get(self.store, conv["contact_id"]) or {}
            intro = identity.introduction(who, settings, self.store) if who else ""
            if identity.introduces_herself(text, intro):  # remembered, so the next reply doesn't introduce again
                self.store.execute("UPDATE channel_conversations SET introduced_at=? WHERE id=?", (time.time(), conv["id"]))
        body = adapter.format_text(text)
        chunks = [body[i:i + adapter.max_text] for i in range(0, len(body), adapter.max_text)] or [""]
        result = SendResult(ok=True)
        for chunk in chunks:
            mid = uuid.uuid4().hex[:12]
            self.store.execute(
                "INSERT INTO channel_messages (id, channel_conversation_id, direction, author, content, status, error, created_at) "
                "VALUES (?,?,'out',?,?,?,?,?)", (mid, conv["id"], author, chunk, "failed" if block else "queued", block, time.time()))
            if block:
                return SendResult(ok=False, error=block)
            out = OutgoingMessage(channel_id=conv["channel_id"], recipient_id=conv["external_chat_id"], text=chunk, idempotency_key=mid)
            result = await adapter.send(out)
            if not result.ok and result.retryable:
                await asyncio.sleep(2)
                result = await adapter.send(out)  # same idempotency key: never delivered twice
            status = "sent" if result.ok else "unconfirmed" if result.uncertain else "failed"
            self.store.execute("UPDATE channel_messages SET status=?, external_id=?, error=? WHERE id=?",
                               (status, result.external_id, result.error[:500], mid))
            if not result.ok:
                audit.record(self.store, f"channel:{conv['channel_id']}", "message.send", conv["id"], outcome="failed",
                             error=result.error)
                break
        self.store.execute("UPDATE channel_conversations SET last_message_at=?, updated_at=? WHERE id=?",
                           (time.time(), time.time(), conv["id"]))
        return result

    def _identity_for_sending(self, contact: dict, prefer: str = "") -> dict | None:
        idents = contacts.identities(self.store, contact["id"])
        usable = [i for i in idents if (self.row(i["channel_id"]) or {}).get("enabled")]
        usable.sort(key=lambda i: i["channel_id"] != prefer)
        return usable[0] if usable else None

    async def send_to_contact(self, contact_id: str, text: str, author: str = "donna", prefer: str = "") -> dict:
        """Start or continue a conversation with a contact (owner tool, automations, owner alerts)."""
        contact = contacts.get(self.store, contact_id)
        if not contact:
            return {"ok": False, "detail": "Error: unknown contact."}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "detail": "Error: the message is empty."}
        ident = self._identity_for_sending(contact, prefer or self.store.settings().get("owner_channel", ""))
        if not ident:
            return {"ok": False, "detail": f"Error: {contact['display_name']} has no identity on a connected channel."}
        conv = self.conversation_for(ident["channel_id"], contact, ident["external_id"])
        if author == "donna" and not contact["is_owner"] and identity.needs_introduction(self.store, conv):
            text = identity.with_introduction(text, identity.introduction(contact, self.store.settings(), self.store))
        self.store.add_message(conv["conversation_id"], "assistant", text)
        sent = await self.send_text(conv, text, author=author)
        if sent.uncertain:
            # Not "ok", but never say it failed: retrying would send it twice.
            return {"ok": True, "detail": f"Probably sent to {contact['display_name']}: WhatsApp didn't confirm it "
                                          f"({sent.error}). Do NOT send it again; ask the user to check the chat."}
        if not sent.ok:
            return {"ok": False, "detail": f"Error: not sent ({sent.error})."}
        return {"ok": True, "detail": f"Sent to {contact['display_name']} on {self.label(ident['channel_id'])}."}

    async def send_to_owner(self, text: str) -> bool:
        owner = contacts.owner(self.store)
        if not owner:
            return False
        return (await self.send_to_contact(owner["id"], text, prefer=self.store.settings().get("owner_channel", "")))["ok"]

    async def notify_owner(self, title: str, body: str = "", level: str = "info", conversation_id: str | None = None,
                           chat: bool = True) -> None:
        """Toast + in-app notification, and (chat=True) a message in the owner's own chat."""
        if self.notifier:
            try:
                await self.notifier.send(Note(title, body, level, conversation_id=conversation_id), ["windows", "in_app"])
            except Exception as e:  # noqa: BLE001
                log.warning("Notification failed: %s", e)
        if chat:
            try:
                await self.send_to_owner(f"{title}\n{body}".strip())
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't message the owner: %s", e)

    async def _alert_approval(self, code: str) -> None:
        a = approvals.get(self.store, code)
        if not a:
            return
        who = (contacts.get(self.store, a["contact_id"]) or {}).get("display_name", "Someone") if a["contact_id"] else "You"
        hours = max(1, round((a["expires_at"] - time.time()) / 3600))
        await self.notify_owner(f"Approval needed ({code})",
                                f"{who} on {self.label(a['channel_id'])}: {a['description']}\n"
                                f"Reply \"approve {code}\" or \"reject {code}\" (expires in {hours}h).", level="warn")

    # ================================================================== approvals
    async def approve(self, code: str, decided_by: str = "owner:dashboard", edited_text: str | None = None) -> dict:
        """Run an approved action exactly once, then tell whoever asked. Raises approvals.ApprovalError."""
        row = approvals.claim(self.store, code, decided_by)
        code = row["id"]
        try:
            if row["kind"] == "reply":
                return await self._send_approved_reply(row, decided_by, edited_text)
            tool = find_tool(self.hub, row["tool"], self.store.settings())
            why = approvals.still_allowed(self.store, row, tool) or ("" if tool else f"{row['tool']} isn't available any more.")
            if why:
                final = approvals.finish(self.store, code, "failed", why, decided_by)
                self.spawn(self._after_decision(final, decided_by))
                return final
            ok, out = await call_tool(self.hub, tool, row["args"], self._ctx_for(row), self.store)
            final = approvals.finish(self.store, code, "executed" if ok else "failed", out, decided_by)
        except Exception as e:  # noqa: BLE001 - never leave an approval stuck in "executing"
            log.exception("Approval %s failed", code)
            final = approvals.finish(self.store, code, "failed", f"Error: {e}", decided_by)
        self.spawn(self._after_decision(final, decided_by))
        return final

    async def reject(self, code: str, decided_by: str = "owner:dashboard") -> dict:
        row = approvals.reject(self.store, code, decided_by)
        self.spawn(self._after_decision(row, decided_by))
        return row

    async def _send_approved_reply(self, row: dict, decided_by: str, edited_text: str | None) -> dict:
        draft = str(row["args"].get("text", ""))
        text = (edited_text or draft).strip()
        why = approvals.still_allowed(self.store, row, None)
        conv = self.conversation(row["channel_conversation_id"] or "")
        if why or not conv or not text:
            return approvals.finish(self.store, row["id"], "failed", why or "The conversation no longer exists.", decided_by)
        edited = text != draft.strip()
        self.store.add_message(conv["conversation_id"], "assistant", text)
        sent = await self.send_text(conv, text, author="owner" if edited else "donna")
        return approvals.finish(self.store, row["id"], "executed" if sent.ok else "failed",
                                ("Sent your edited reply." if edited else "Sent.") if sent.ok else sent.error, decided_by)

    def _ctx_for(self, row: dict) -> TurnContext:
        contact = contacts.get(self.store, row["contact_id"]) if row["contact_id"] else None
        return TurnContext(audience=row["audience"], channel_id=row["channel_id"], channel_label=self.label(row["channel_id"]),
                           contact=contact, channel_conversation_id=row["channel_conversation_id"] or "",
                           capabilities=contacts.effective_capabilities(self.store, contact["id"], row["channel_id"]) if contact else {})

    async def book_for_request(self, req: dict, start: str, end: str, title: str = "", add_meet: bool = True) -> dict:
        """The owner booked a meeting request (the owner's own action, so no further approval).

        A request that already has an event moves that event (same Meet link) instead of creating another; the
        result is read back from the calendar and checked against what was asked. Returns
        {"ok", "result", "meet", "start", "end", "moved", "event_id"}."""
        settings = self.store.settings()
        fail = {"ok": False, "meet": "", "start": "", "end": "", "moved": False, "event_id": ""}
        try:
            s, e = datetime.fromisoformat(start[:16]), datetime.fromisoformat(end[:16])
        except ValueError:
            return {**fail, "result": "Give the start and end as YYYY-MM-DDTHH:MM."}
        try:
            now = datetime.now(ZoneInfo(settings.get("timezone") or "UTC")).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            now = datetime.now()
        if e <= s:
            return {**fail, "result": "The end must be after the start."}
        if s < now - timedelta(minutes=5):
            return {**fail, "result": f"{contact_requests.fmt_local(start)} has already passed."}
        moving = bool(req.get("event_id"))
        tool = find_tool(self.hub, "update_event" if moving else "create_event", settings)
        if not tool:
            return {**fail, "result": "Google Calendar isn't connected (Connections → Google)."}
        who = req.get("contact_name") or "contact"
        if moving:
            args = {"event_id": req["event_id"], "start_time": start[:16], "end_time": end[:16]}
            if title:
                args["summary"] = title[:200]
        else:
            args = {"summary": (title or f"Meeting with {who}")[:200], "start_time": start[:16], "end_time": end[:16],
                    "description": f"Requested by {who} via Donna ({req['code']}): {req['summary']}", "add_meet": add_meet}
            email = str(req["details"].get("email") or "").strip()
            if re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", email):  # the contact gets a calendar invite too
                args["attendees"] = email
        ok, out = await call_tool(self.hub, tool, args, None, self.store)
        out = out or ""
        # Read back what the calendar actually saved: "<start> → <end> | title | ID: <id>".
        when = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\S*\s*→\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", out)
        event_id = (re.search(r"ID: (\S+)", out) or [None, req.get("event_id", "")])[1]
        meet = (re.search(r"https://meet\.google\.com/[\w-]+", out) or [None, req.get("meet_link", "")])[0] or req.get("meet_link", "")
        got_start, got_end = (when.group(1), when.group(2)) if when else (start[:16], end[:16])
        if ok and got_start != start[:16]:
            ok, out = False, f"The calendar saved {contact_requests.fmt_local(got_start)} instead of {contact_requests.fmt_local(start)}. Check it in Google Calendar."
        if ok:
            contact_requests.set_booking(self.store, req["id"], event_id or "", got_start, got_end, meet or "")
        audit.record(self.store, "owner", "request.moved" if moving else "request.booked", req["code"],
                     outcome="ok" if ok else "failed", start=start, end=end, result=out[:300])
        return {"ok": ok, "result": out, "meet": meet or "", "start": got_start, "end": got_end, "moved": moving,
                "event_id": event_id or ""}

    async def inform_about_request(self, req: dict, must_include: str = "") -> bool:
        """Tell a contact what happened to their request, in Donna's voice, using only the owner's own outcome note."""
        conv = self.conversation(req.get("channel_conversation_id") or "")
        contact = contacts.get(self.store, req["contact_id"]) if req.get("contact_id") else None
        if not conv or not contact:
            return False
        settings = self.store.settings()
        owner = identity.owner_name(settings, self.store)
        ctx = TurnContext(audience="contact", channel_id=conv["channel_id"], channel_label=self.label(conv["channel_id"]),
                          contact=contact, channel_conversation_id=conv["id"], no_tools=True, persist_user=False,
                          intro_needed=identity.needs_introduction(self.store, conv))
        status = {"approved": f"{owner} approved it", "in_progress": f"{owner} is working on it",
                  "completed": "it's done", "rejected": f"{owner} can't do it", "cancelled": "it was cancelled"}.get(
                      req["status"], req["status"].replace("_", " "))
        note = (f"[Update from {owner}, not a message from {contact['display_name']}: about their request {req['code']} "
                f"(\"{req['summary']}\"): {status}. {owner}'s note: {req['outcome'] or 'none'}. Tell them briefly and "
                "naturally using only this information; don't add promises, times or details that aren't in it."
                + (" The exact date, time and link are added below your message automatically: don't write them yourself."
                   if must_include else "") + "]")
        async with self._lock(conv["id"]), self._llm:
            res = await run_agent_turn(self.store, self.hub, conv["conversation_id"], note, ctx)
        if res.error or not res.text.strip():
            return False
        text = res.text.strip()
        if must_include and must_include not in text:  # e.g. the Meet link: never left to the model
            text = f"{text}\n{must_include}"
        if ctx.intro_needed:
            text = identity.with_introduction(text, identity.introduction(contact, settings, self.store))
        return (await self.send_text(conv, text, author="donna")).ok

    async def _after_decision(self, row: dict, decided_by: str) -> None:
        """Tell the chat that asked: a natural follow-up for a contact, a short note for the owner."""
        conv = self.conversation(row.get("channel_conversation_id") or "")
        if not conv or row["kind"] == "reply":
            return
        owner = self.store.settings().get("owner_name") or "the owner"
        if row["audience"] == "owner":
            if decided_by != "owner:chat":  # the chat command already answered
                outcome = {"executed": "Done", "failed": "Couldn't do it", "rejected": "Rejected"}.get(row["status"], row["status"])
                await self.send_text(conv, f"{outcome} ({row['id']}): {row['description']}"
                                     + (f"\n{row['result'][:600]}" if row["status"] != "rejected" else ""))
            return
        ctx = self._ctx_for(row)
        if not ctx.contact:
            return
        ctx.no_tools, ctx.persist_user = True, False
        outcome = {"executed": f"{owner} approved it and it was done. Result: {row['result'][:800]}",
                   "failed": f"{owner} approved it but it failed: {row['result'][:300]}",
                   "rejected": f"{owner} declined it."}.get(row["status"], row["status"])
        note = (f"[Update for you, not a message from {ctx.contact['display_name']}: about the earlier request "
                f"\"{row['description']}\": {outcome} Tell them the outcome briefly and naturally.]")
        async with self._lock(conv["id"]), self._llm:
            res = await run_agent_turn(self.store, self.hub, conv["conversation_id"], note, ctx)
        if res.text.strip() and not res.error:
            await self.send_text(conv, res.text.strip(), author="donna")
