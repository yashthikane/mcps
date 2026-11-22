"""Tools that need the turn's context, so they run inside Donna instead of through MCP.

`remember` stores to the memory the turn may write (global for the owner, the contact's own memory
for a contact). The messaging tools let the owner read and send channel messages; they are never
offered to contacts (permissions.OWNER_ONLY).
"""
from __future__ import annotations

import asyncio
import json

from . import availability, contact_requests, contacts, identity, memory
from .hub import Tool


def _tool(name: str, group: str, description: str, props: dict, required: list[str], confirm: bool = False,
          writes: bool = False) -> Tool:
    schema = {"type": "object", "properties": props, "required": required}
    return Tool(name=name, remote=name, server="runtime", group=group, description=description, schema=schema,
                confirm=confirm, writes=writes or confirm)


TOOLS = [
    _tool("remember", "memory",
          "Save a short, durable fact worth remembering for later conversations (a preference, a commitment, "
          "a date). One fact per call; don't store secrets or one-off small talk.",
          {"note": {"type": "string", "description": "The fact, in one sentence."}}, ["note"], writes=True),
    _tool("list_memories", "memory", "List what Donna remembers about the user, with memory IDs.", {}, []),
    _tool("forget_memory", "memory", "Delete one remembered fact by its memory ID (from list_memories).",
          {"memory_id": {"type": "string"}}, ["memory_id"], writes=True),
    _tool("list_contacts", "messaging",
          "Contacts who can message Donna on WhatsApp/Telegram, with their mode (disabled, suggest, auto, human_first) and IDs.",
          {"query": {"type": "string", "description": "Optional name or number filter."}}, []),
    _tool("read_chat", "messaging",
          "Recent WhatsApp/Telegram messages with a contact (newest last). `contact` is a name or contact ID.",
          {"contact": {"type": "string"}, "limit": {"type": "integer", "description": "Messages to return, default 15, max 50."}},
          ["contact"]),
    _tool("send_message", "messaging",
          "Have Donna pass a message from the user to a contact on WhatsApp/Telegram. Donna sends it as herself (the "
          "user's AI assistant), relaying the user's words, never pretending to be the user. `contact` is a name or contact ID; "
          "`text` is what the user wants them to know.",
          {"contact": {"type": "string"}, "text": {"type": "string"}}, ["contact", "text"], confirm=True),
    _tool("list_requests", "requests",
          "Requests people made through Donna (callbacks, meetings, enquiries, messages) with their status and IDs like R12. "
          "status: open (default), completed, rejected, cancelled or all.",
          {"status": {"type": "string"}}, []),
    _tool("resolve_request", "requests",
          "Update a request's status: approved, in_progress, completed, rejected or cancelled, with an optional outcome note.",
          {"request_id": {"type": "string"}, "status": {"type": "string"}, "outcome": {"type": "string"}},
          ["request_id", "status"], writes=True),
    # Contact turns only (permissions.CONTACT_ONLY): the conversation and contact come from the turn.
    _tool("record_request", "requests",
          "Record why this person is contacting the owner, so the owner is told: type is one of callback, meeting, "
          "business_enquiry, general_enquiry, information_request, personal_message, product_enquiry, document_request, "
          "urgent, task, other. One request per thing they want: a call AND a meeting are two requests. summary: one "
          "line. details: what you collected, e.g. {\"topic\": \"website\", \"organization\": \"ABC Ltd\"}. "
          "start (meetings and timed callbacks): the exact local time as YYYY-MM-DDTHH:MM, worked out from today's date "
          "(\"tomorrow 7 PM\" → the next day at 19:00); duration_minutes if they said. urgency: low, normal, high or urgent. "
          "missing: details you still need to ask them for (empty when you have enough).",
          {"type": {"type": "string"}, "summary": {"type": "string"}, "details": {"type": "object"},
           "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, local time"},
           "duration_minutes": {"type": "integer"},
           "urgency": {"type": "string"}, "required_action": {"type": "string"}, "missing": {"type": "string"}},
          ["type", "summary"], writes=True),
    _tool("update_request", "requests",
          "Change a request in this conversation (ID like R12): add details, a corrected start (YYYY-MM-DDTHH:MM) or "
          "duration_minutes, what's still missing, or cancel it if they no longer need it (status: cancelled). Use this "
          "for corrections, including a meeting that's already booked: it goes back to the owner to move the same event.",
          {"request_id": {"type": "string"}, "details": {"type": "object"}, "summary": {"type": "string"},
           "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM, local time"},
           "duration_minutes": {"type": "integer"},
           "missing": {"type": "string"}, "status": {"type": "string"}},
          ["request_id"], writes=True),
    _tool("suggest_meeting_slots", "requests",
          "Free slots on the owner's calendar for a meeting request (free/busy only, never what the owner is doing). "
          "No arguments: a few slots over the next working days. day (YYYY-MM-DD): that day's free slots. "
          "at (YYYY-MM-DDTHH:MM, local): whether that exact time is free. duration_minutes: the meeting length.",
          {"day": {"type": "string"}, "at": {"type": "string"}, "duration_minutes": {"type": "integer"}}, []),
]
BY_NAME = {t.name: t for t in TOOLS}


class Services:
    """Set by the app's lifespan (and tests): what the runtime tools act on."""
    store = None
    channels = None  # channels.manager.ChannelManager


services = Services()


def find(name: str) -> Tool | None:
    return BY_NAME.get(name)


def available() -> list[Tool]:
    return list(TOOLS)


def _match_contact(store, query: str) -> dict | None:
    c = contacts.get(store, query)
    if c:
        return c
    found = contacts.list_all(store, query)
    exact = [x for x in found if x["display_name"].lower() == query.lower()]
    return (exact or found or [None])[0]


async def _notify_owner(store, req: dict, created: bool) -> None:
    """Tell the owner once a request is ready for them (and again if it turns urgent)."""
    ready = req["status"] == "waiting_for_owner" or req["urgency"] == "urgent"
    if not ready or req["owner_notified"] or not services.channels:
        return
    conv = services.channels.conversation(req["channel_conversation_id"] or "") or {}
    title, body = contact_requests.notification(req)
    ping = True
    if req["type"] == "urgent" or req["urgency"] == "urgent":
        # During Do not disturb / Sleeping an urgent request pings the phone only if the owner chose that;
        # it always reaches the Inbox and the in-app notifications.
        settings = store.settings()
        ping = availability.urgent_ping_allowed(settings, await availability.effective(store))
    await services.channels.notify_owner(title, body, level="warn" if req["urgency"] in ("high", "urgent") else "info",
                                         conversation_id=conv.get("conversation_id"), chat=ping)
    contact_requests.mark_notified(store, req["id"])


async def call(tool: Tool, args: dict, ctx, store=None) -> tuple[bool, str]:
    store = store or services.store
    try:
        if tool.name == "remember":
            m = memory.remember(store, ctx, args.get("note", ""))
            return True, f"Remembered (memory ID {m['id']})."
        if tool.name == "list_memories":
            items = memory.list_global(store)
            return True, "\n".join(f"- {m['content']} · ID: {m['id']}" for m in items) or "Nothing remembered yet."
        if tool.name == "forget_memory":
            ok = memory.delete(store, str(args.get("memory_id", "")))
            return ok, "Forgotten." if ok else "Error: no memory with that ID."
        if tool.name == "list_contacts":
            rows = contacts.list_all(store, str(args.get("query", "")))
            if not rows:
                return True, "No contacts yet. Add them on the Contacts page."
            return True, "\n".join(
                f"- {c['display_name']}{' (you)' if c['is_owner'] else ''} · {c['mode']}"
                f"{' · paused' if c['paused'] else ''} · " + ", ".join(f"{i['channel_id']}:{i['external_id']}" for i in c["identities"])
                + f" · ID: {c['id']}" for c in rows[:40])
        if tool.name == "read_chat":
            c = _match_contact(store, str(args.get("contact", "")))
            if not c:
                return False, "Error: no contact matches that name."
            limit = max(1, min(int(args.get("limit") or 15), 50))
            rows = store.rows(
                "SELECT m.direction, m.author, m.content, m.created_at, cc.channel_id FROM channel_messages m "
                "JOIN channel_conversations cc ON cc.id=m.channel_conversation_id WHERE cc.contact_id=? "
                "ORDER BY m.created_at DESC LIMIT ?", (c["id"], limit))
            if not rows:
                return True, f"No messages with {c['display_name']} yet."
            return True, "\n".join(f"[{r['channel_id']}] {c['display_name'] if r['direction'] == 'in' else r['author'].title()}: "
                                   f"{r['content'][:400]}" for r in reversed(rows))
        if tool.name == "send_message":
            if not services.channels:
                return False, "Error: messaging channels aren't running."
            c = _match_contact(store, str(args.get("contact", "")))
            if not c:
                return False, "Error: no contact matches that name."
            # Donna relays; she never sends the owner's words as if the owner typed them.
            text = identity.relay(c, store.settings(), str(args.get("text", "")), store)
            sent = await services.channels.send_to_contact(c["id"], text, author="donna")
            return sent["ok"], sent["detail"] + (f" Sent as: {text}" if sent["ok"] else "")
        if tool.name == "list_requests":
            status = str(args.get("status") or "open").lower()
            rows = contact_requests.list_all(store, status="" if status == "all" else status)
            if not rows:
                return True, "No requests." if status == "all" else f"No {status} requests."
            return True, "\n".join(
                f"- {r['code']} {r['type_label']} from {r.get('contact_name') or '?'} ({r['status'].replace('_', ' ')}, "
                f"{r['urgency']}): {r['summary']}" + (f" · {json.dumps(r['details'], ensure_ascii=False)}" if r["details"] else "")
                for r in rows[:30])
        if tool.name == "resolve_request":
            r = contact_requests.set_status(store, args.get("request_id", ""), str(args.get("status", "")),
                                            args.get("outcome"), actor="owner")
            return True, f"{r['code']} is now {r['status']}."
        if tool.name == "record_request":
            req, created = contact_requests.record(store, ctx, str(args.get("type", "")), str(args.get("summary", "")),
                                                   args.get("details"), str(args.get("urgency") or "normal"),
                                                   str(args.get("required_action") or ""), str(args.get("missing") or ""),
                                                   args.get("start"), args.get("duration_minutes"))
            await _notify_owner(store, req, created)
            settings = store.settings()
            owner = identity.owner_name(settings, store)
            if req["type"] in contact_requests.READY_TYPES:
                # Nothing more to collect: confirm in their language without promising anything for the owner.
                nxt = (f"{owner} has been told. Confirm briefly that you'll pass it on, e.g. "
                       f"\"{identity.passed_on(ctx.contact, settings, req['type'], store)}\". Don't ask anything else, and "
                       f"never say {owner} will call, is ready, or when.")
            elif req["missing"]:
                nxt = f"Ask them for: {req['missing']}."
            else:
                nxt = f"{owner} has been told. Say you've passed it on; don't promise what {owner} will do or when."
            if req["details"].get("start"):  # read the resolved date back, so a wrong day is caught now, not after booking
                nxt += (f" Say the time back exactly as \"{contact_requests.fmt_local(req['details']['start'])}\" so they can "
                        "correct it if that's not what they meant.")
            return True, f"{'Recorded' if created else 'Updated'} {req['code']} ({req['type_label']}). {nxt}"
        if tool.name == "update_request":
            req = contact_requests.update_from_contact(store, ctx, args.get("request_id", ""), args.get("details"),
                                                       str(args.get("summary") or ""), str(args.get("status") or ""),
                                                       args.get("missing"), args.get("start"), args.get("duration_minutes"))
            if req["type"] == "meeting" and req["event_id"] and req["status"] == "cancelled":
                # The contact cancelled their own booked meeting: removed from the calendar without asking the owner.
                if not services.channels:
                    return False, "Error: messaging channels aren't running."
                ok, out = await services.channels.cancel_booking(req)
                if not ok:
                    return False, f"Error: couldn't remove it from the calendar ({out[:200]}). Tell them the owner will sort it out."
                owner = identity.owner_name(store.settings(), store)
                return True, (f"Cancelled {req['code']}: the meeting on {contact_requests.fmt_local(req['event_start'])} is off "
                              f"{owner}'s calendar and {owner} has been told. Confirm that to them briefly.")
            await _notify_owner(store, req, False)
            if req["type"] == "meeting" and req["event_id"] and req["status"] == "waiting_for_owner":
                owner = identity.owner_name(store.settings(), store)
                return True, (f"Updated {req['code']}: {owner} has been asked to move the meeting"
                              + (f" to {contact_requests.fmt_local(req['details']['start'])}" if req["details"].get("start") else "")
                              + f". It is still booked for {contact_requests.fmt_local(req['event_start'])} until {owner} "
                                "moves it; say so, and that you'll confirm the new time.")
            return True, f"Updated {req['code']} ({req['status'].replace('_', ' ')})."
        if tool.name == "suggest_meeting_slots":
            return True, await asyncio.to_thread(
                availability.free_slots, store.settings(), str(args.get("day") or ""), str(args.get("at") or ""),
                int(args.get("duration_minutes") or 0))
    except (ValueError, LookupError) as e:
        return False, f"Error: {e}"
    return False, f"Error: unknown tool {tool.name}"
