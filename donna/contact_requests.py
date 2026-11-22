"""Requests people make of the owner through Donna (callbacks, meetings, enquiries, messages…).

Donna records each request with the details she collected and tells the owner; the owner moves it
through its statuses on the dashboard. A contact's turn can only create or update requests in its own
conversation (enforced here from the turn, never from tool arguments), and only of the types the owner
allows for that contact.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import audit, identity

TYPES = {
    "callback": "Callback request",
    "meeting": "Meeting request",
    "business_enquiry": "Business enquiry",
    "general_enquiry": "General enquiry",
    "information_request": "Information request",
    "personal_message": "Personal message",
    "product_enquiry": "Product enquiry",
    "document_request": "Document request",
    "urgent": "Urgent communication",
    "task": "Task or reminder request",
    "other": "Other",
}
STATUSES = ("new", "information_required", "waiting_for_owner", "approved", "in_progress", "completed", "rejected", "cancelled")
OPEN = ("new", "information_required", "waiting_for_owner", "approved", "in_progress")
URGENCY = ("low", "normal", "high", "urgent")
CONTACT_STATUSES = ("information_required", "waiting_for_owner", "cancelled")  # what Donna may set during a chat
# Requests the owner should hear about at once, even while Donna is still collecting optional details.
READY_TYPES = ("callback", "personal_message", "urgent")
# Types where a second request in the same chat updates the open one; everything else is its own request.
MERGE_TYPES = ("callback", "urgent")


def _status(rtype: str, missing: str, urgency: str) -> str:
    if rtype in READY_TYPES or urgency == "urgent" or not (missing or "").strip():
        return "waiting_for_owner"
    return "information_required"


class RequestError(ValueError):
    """A user-facing validation error."""


def code(rid: int) -> str:
    return f"R{rid}"


def _id(value) -> int:
    try:
        return int(str(value).strip().upper().lstrip("R"))
    except ValueError as e:
        raise RequestError(f"Unknown request {value}.") from e


def _out(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["details"] = json.loads(r.get("details") or "{}")
    r["code"] = code(r["id"])
    r["owner_notified"] = bool(r["owner_notified"])
    r["type_label"] = TYPES.get(r["type"], r["type"])
    return r


def get(store, rid) -> dict | None:
    return _out(store.row("SELECT r.*, c.display_name AS contact_name FROM requests r LEFT JOIN contacts c ON c.id=r.contact_id "
                          "WHERE r.id=?", (_id(rid),)))


def list_all(store, contact_id: str = "", channel_id: str = "", rtype: str = "", status: str = "", limit: int = 300) -> list[dict]:
    sql = "SELECT r.*, c.display_name AS contact_name FROM requests r LEFT JOIN contacts c ON c.id=r.contact_id WHERE 1=1"
    params: list = []
    for col, val in (("r.contact_id", contact_id), ("r.channel_id", channel_id), ("r.type", rtype)):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    if status == "open":
        sql += f" AND r.status IN ({','.join('?' * len(OPEN))})"
        params += list(OPEN)
    elif status:
        sql += " AND r.status=?"
        params.append(status)
    sql += " ORDER BY CASE r.urgency WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, r.updated_at DESC LIMIT ?"
    params.append(limit)
    return [_out(r) for r in store.rows(sql, params)]


def open_for_conversation(store, channel_conversation_id: str) -> list[dict]:
    if not channel_conversation_id:
        return []
    marks = ",".join("?" * len(OPEN))
    return [_out(r) for r in store.rows(f"SELECT * FROM requests WHERE channel_conversation_id=? AND status IN ({marks}) "
                                        "ORDER BY created_at", (channel_conversation_id, *OPEN))]


def booked_for_conversation(store, channel_conversation_id: str, days: int = 14) -> list[dict]:
    """Meetings already booked in this chat recently: a correction should move these, not start new ones."""
    if not channel_conversation_id:
        return []
    return [_out(r) for r in store.rows("SELECT * FROM requests WHERE channel_conversation_id=? AND event_id<>'' "
                                        "AND status NOT IN ('cancelled', 'rejected') AND updated_at>? ORDER BY created_at",
                                        (channel_conversation_id, time.time() - days * 86400))]


def fmt_local(value: str) -> str:
    """"2026-11-22T19:00" → "Sun 22 Nov, 7:00 PM" (wall-clock, as the contact would say it)."""
    try:
        dt = datetime.fromisoformat(value[:16])
    except (TypeError, ValueError):
        return value or ""
    return dt.strftime("%a %d %b, %I:%M %p").replace(" 0", " ").replace(", 0", ", ")


def prompt_block(store, channel_conversation_id: str) -> str:
    """Open requests and recent bookings in this chat, so Donna continues them instead of asking again
    or opening duplicates."""
    lines = []
    for r in open_for_conversation(store, channel_conversation_id):
        details = "; ".join(f"{k}: {v}" for k, v in r["details"].items()) or "no details yet"
        missing = f"; still needed: {r['missing']}" if r["missing"] else ""
        lines.append(f"- {r['code']} {TYPES.get(r['type'], r['type'])} ({r['status'].replace('_', ' ')}): {r['summary']}. "
                     f"Details: {details}{missing}")
    for r in booked_for_conversation(store, channel_conversation_id):
        if r["status"] in OPEN:
            continue
        lines.append(f"- {r['code']} meeting BOOKED for {fmt_local(r['event_start'])}"
                     + (f", Google Meet {r['meet_link']}" if r["meet_link"] else "")
                     + ". If they want to change or fix it, call update_request on this one (with the new start); "
                       "never record a new meeting for it.")
    if not lines:
        return ""
    return ("Requests in this conversation (continue these with update_request, don't ask again for details you have):\n"
            + "\n".join(lines))


def _clean_details(details) -> dict:
    if not isinstance(details, dict):
        return {}
    return {str(k)[:60]: str(v)[:500] for k, v in list(details.items())[:20] if str(v).strip()}


def _time_fields(store, start: str | None, duration: int | str | None) -> dict:
    """Validate a meeting's exact time: local "YYYY-MM-DDTHH:MM" in the owner's timezone, not in the past."""
    out: dict = {}
    if start:
        start = str(start).strip().replace(" ", "T")[:16]
        try:
            dt = datetime.fromisoformat(start)
        except ValueError as e:
            raise RequestError("start must be the exact local time as YYYY-MM-DDTHH:MM, e.g. 2026-11-22T19:00.") from e
        tz = store.settings().get("timezone") or "UTC"
        try:
            now = datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            now = datetime.now()
        if dt < now - timedelta(minutes=5):
            raise RequestError(f"{fmt_local(start)} has already passed (it's now {fmt_local(now.isoformat())}). "
                               "Check the date with them and record the right one.")
        out["start"] = start
    if duration not in (None, ""):
        try:
            minutes = int(duration)
        except (TypeError, ValueError) as e:
            raise RequestError("duration_minutes must be a number of minutes.") from e
        if not 5 <= minutes <= 480:
            raise RequestError("duration_minutes must be between 5 and 480.")
        out["duration_minutes"] = str(minutes)
    return out


def record(store, ctx, rtype: str, summary: str, details=None, urgency: str = "normal", required_action: str = "",
           missing: str = "", start: str | None = None, duration_minutes=None) -> tuple[dict, bool]:
    """Create a request for the turn's contact and conversation. A callback or urgent request updates the open
    one of the same type (one call-back at a time); other types are separate requests, because merging by type
    mixed up unrelated questions. Returns (request, created)."""
    if not ctx or ctx.audience != "contact" or not ctx.contact:
        raise RequestError("Requests are recorded for contacts messaging Donna.")
    rtype = (rtype or "other").strip().lower()
    if rtype not in TYPES:
        rtype = "other"
    allowed = identity.allowed_request_types(ctx.contact)
    if allowed and rtype not in allowed:
        raise RequestError(f"Donna doesn't take {TYPES[rtype].lower()}s for this person. Offer to pass on a short "
                           "message instead, without promising anything.")
    urgency = urgency if urgency in URGENCY else "normal"
    summary = " ".join(str(summary or "").split())[:300]
    if not summary:
        raise RequestError("Give the request a one-line summary.")
    details = {**_clean_details(details), **_time_fields(store, start, duration_minutes)}
    if rtype == "meeting" and "start" not in details and not (missing or "").strip():
        missing = "the exact day and time"  # a meeting can't be booked without one
    status = _status(rtype, missing, urgency)
    now = time.time()
    existing = next((r for r in open_for_conversation(store, ctx.channel_conversation_id)
                     if r["type"] == rtype and rtype in MERGE_TYPES), None)
    if existing:
        merged = {**existing["details"], **details}
        store.execute("UPDATE requests SET summary=?, details=?, urgency=?, required_action=?, missing=?, status=?, updated_at=? "
                      "WHERE id=?", (summary, json.dumps(merged), urgency, (required_action or existing["required_action"])[:300],
                                    (missing or "")[:300], status, now, existing["id"]))
        return get(store, existing["id"]), False
    cur = store.execute(
        "INSERT INTO requests (contact_id, channel_id, channel_conversation_id, type, summary, details, urgency, status, "
        "required_action, missing, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ctx.contact["id"], ctx.channel_id, ctx.channel_conversation_id or None, rtype, summary,
         json.dumps(details), urgency, status, (required_action or "")[:300], (missing or "")[:300], now, now))
    req = get(store, cur.lastrowid)
    audit.record(store, f"contact:{ctx.contact['id']}", "request.created", req["code"], type=rtype, urgency=urgency)
    return req, True


def update_from_contact(store, ctx, rid, details=None, summary: str = "", status: str = "", missing: str | None = None,
                        start: str | None = None, duration_minutes=None) -> dict:
    """Donna updating a request during the same chat: more details, what's still missing, cancelled by them, or a
    change to a meeting that's already booked (which reopens it for the owner, keeping its calendar event)."""
    req = get(store, rid)
    if not req or not ctx or not ctx.contact or req["contact_id"] != ctx.contact["id"] \
            or req["channel_conversation_id"] != ctx.channel_conversation_id:
        raise RequestError("There's no such request in this conversation.")
    reopening = req["status"] not in OPEN and req["type"] == "meeting" and bool(req["event_id"]) \
        and req["status"] not in ("cancelled", "rejected")
    if req["status"] not in OPEN and not reopening:
        raise RequestError(f"{req['code']} is already {req['status']}.")
    if status and status not in CONTACT_STATUSES:
        raise RequestError("Donna can only mark a request as needing information, waiting for the owner, or cancelled.")
    merged = {**req["details"], **_clean_details(details), **_time_fields(store, start, duration_minutes)}
    missing = req["missing"] if missing is None else missing
    new_status = status or ("waiting_for_owner" if reopening else _status(req["type"], missing, req["urgency"]))
    store.execute("UPDATE requests SET details=?, summary=?, missing=?, status=?, owner_notified=?, updated_at=? WHERE id=?",
                  (json.dumps(merged), (summary.strip() or req["summary"])[:300], missing[:300], new_status,
                   0 if reopening else req["owner_notified"], time.time(), req["id"]))
    if reopening:
        audit.record(store, f"contact:{ctx.contact['id']}", "request.change_requested", req["code"],
                     booked=req["event_start"], requested=merged.get("start", ""))
    return get(store, req["id"])


def set_booking(store, rid, event_id: str, start: str, end: str, meet_link: str = "") -> dict:
    req = get(store, rid)
    store.execute("UPDATE requests SET event_id=?, event_start=?, event_end=?, meet_link=?, updated_at=? WHERE id=?",
                  (event_id, start, end, meet_link or req["meet_link"], time.time(), req["id"]))
    return get(store, req["id"])


def set_status(store, rid, status: str, outcome: str | None = None, actor: str = "owner") -> dict:
    """The owner moving a request along (dashboard or chat)."""
    req = get(store, rid)
    if not req:
        raise LookupError(f"Unknown request {rid}.")
    if status not in STATUSES:
        raise RequestError(f"Status must be one of: {', '.join(STATUSES)}.")
    store.execute("UPDATE requests SET status=?, outcome=?, updated_at=? WHERE id=?",
                  (status, (outcome if outcome is not None else req["outcome"])[:1000], time.time(), req["id"]))
    audit.record(store, actor, "request.status", req["code"], status=status, note=(outcome or "")[:200])
    return get(store, req["id"])


def mark_notified(store, rid) -> None:
    store.execute("UPDATE requests SET owner_notified=1 WHERE id=?", (_id(rid),))


def notification(req: dict) -> tuple[str, str]:
    """Title and body for the owner, e.g. "Callback requested by Aai"."""
    who = req.get("contact_name") or "a contact"
    titles = {"callback": f"Callback requested by {who}", "meeting": f"Meeting request from {who}",
              "business_enquiry": f"New business enquiry from {who}", "urgent": f"URGENT: {who} needs you",
              "personal_message": f"Message from {who}", "document_request": f"Document request from {who}",
              "task": f"Task request from {who}"}
    title = titles.get(req["type"], f"{TYPES.get(req['type'], 'Request')} from {who}")
    if req["type"] == "meeting" and req.get("event_id"):
        title = f"{who} wants to change the meeting"
    lines = [req["summary"]]
    if req["type"] == "meeting" and req.get("event_id"):
        lines.append(f"Booked now: {fmt_local(req['event_start'])}")
    if req["details"].get("start"):
        lines.append(f"Asked for: {fmt_local(req['details']['start'])}")
    lines += [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in req["details"].items() if k not in ("start",)]
    if req["missing"]:
        lines.append(f"Donna is still asking for: {req['missing']}")
    lines.append(f"({req['code']} · see Inbox → Requests)")
    return title, "\n".join(lines)


def counts(store) -> int:
    return store.row("SELECT COUNT(*) AS n FROM requests WHERE status IN ('new', 'waiting_for_owner')")["n"]
