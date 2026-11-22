"""Contacts: who may talk to Donna, how, and with which capabilities (SQLite).

Modes: disabled (ignored, the default for anyone unknown), suggest (Donna drafts a reply for the
owner to approve), auto (Donna replies within the contact's capabilities), human_first (the owner
is alerted and Donna stays silent). One contact can have identities on several channels; the owner
is the contact with is_owner = 1.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from . import audit
from .permissions import CONTACT_DEFAULTS, clean_capabilities

MODES = ("disabled", "suggest", "auto", "human_first")
EDITABLE = ("display_name", "mode", "paused", "language", "tone", "timezone", "working_hours", "share_memory", "notes",
            # communication profile (donna/identity.py)
            "relationship", "relationship_label", "address_as", "script", "formality", "greeting", "intro_message",
            "background", "special_instructions", "allowed_requests")
RELATIONSHIPS = ("", "family", "friend", "client", "colleague", "vendor", "other")
LONG_TEXT = ("intro_message", "background", "special_instructions")


class ContactError(ValueError):
    """A user-facing validation error (HTTP 400)."""


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _out(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["working_hours"] = json.loads(r.get("working_hours") or "{}")
    r["allowed_requests"] = json.loads(r.get("allowed_requests") or "[]")
    for k in ("is_owner", "paused", "discovered", "share_memory"):
        r[k] = bool(r[k])
    return r


def get(store, contact_id: str) -> dict | None:
    c = _out(store.row("SELECT * FROM contacts WHERE id=?", (contact_id,)))
    if c:
        c["identities"] = identities(store, contact_id)
    return c


def list_all(store, q: str = "", discovered: bool | None = None) -> list[dict]:
    sql = ("SELECT c.*, (SELECT COUNT(*) FROM channel_conversations cc WHERE cc.contact_id=c.id) AS conversations "
           "FROM contacts c WHERE 1=1")
    params: list = []
    if q:
        sql += (" AND (c.display_name LIKE ? OR EXISTS (SELECT 1 FROM contact_identities i WHERE i.contact_id=c.id "
                "AND (i.external_id LIKE ? OR i.phone LIKE ? OR i.display_name LIKE ?)))")
        params += [f"%{q}%"] * 4
    if discovered is not None:
        sql += " AND c.discovered=?"
        params.append(1 if discovered else 0)
    sql += " ORDER BY c.is_owner DESC, COALESCE(c.last_interaction_at, c.created_at) DESC"
    out = [_out(r) for r in store.rows(sql, params)]
    ids = {c["id"]: c for c in out}
    for c in out:
        c["identities"] = []
    if ids:
        marks = ",".join("?" * len(ids))
        for i in store.rows(f"SELECT * FROM contact_identities WHERE contact_id IN ({marks}) ORDER BY created_at", list(ids)):
            ids[i["contact_id"]]["identities"].append(_identity(i))
    return out


def _identity(r: dict) -> dict:
    r = dict(r)
    r["verified"] = bool(r["verified"])
    return r


def identities(store, contact_id: str) -> list[dict]:
    return [_identity(r) for r in store.rows("SELECT * FROM contact_identities WHERE contact_id=? ORDER BY created_at",
                                                (contact_id,))]


def owner(store) -> dict | None:
    r = store.row("SELECT id FROM contacts WHERE is_owner=1")
    return get(store, r["id"]) if r else None


def _validate(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k not in EDITABLE or v is None:
            continue
        if k == "display_name":
            v = str(v).strip()[:120]
            if not v:
                raise ContactError("Give the contact a name.")
        elif k == "mode":
            if v not in MODES:
                raise ContactError("Mode must be disabled, suggest, auto or human_first.")
        elif k in ("paused", "share_memory"):
            v = 1 if v else 0
        elif k == "timezone" and v:
            try:
                ZoneInfo(v)
            except Exception as e:  # noqa: BLE001
                raise ContactError(f"Unknown timezone: {v}") from e
        elif k == "working_hours":
            v = json.dumps(_hours(v))
        elif k == "relationship":
            if v not in RELATIONSHIPS:
                raise ContactError(f"Relationship must be one of: {', '.join(r for r in RELATIONSHIPS if r)}.")
        elif k == "script":
            if v not in ("", "latin", "devanagari"):
                raise ContactError("Script must be latin or devanagari (or empty to match theirs).")
        elif k == "formality":
            if v not in ("", "casual", "neutral", "formal"):
                raise ContactError("Formality must be casual, neutral or formal.")
        elif k == "allowed_requests":
            from .contact_requests import TYPES
            if not isinstance(v, list) or any(t not in TYPES for t in v):
                raise ContactError(f"Request types must be from: {', '.join(TYPES)}.")
            v = json.dumps(sorted(set(v)))
        elif k in LONG_TEXT:
            v = str(v).strip()[:2000]
        else:
            v = str(v).strip()[:500]
        out[k] = v
    return out


def _hours(v) -> dict:
    if not v:
        return {}
    if not isinstance(v, dict):
        raise ContactError("Working hours must be {days, start, end}.")
    days = sorted({int(d) for d in v.get("days", range(7)) if 0 <= int(d) <= 6})
    start, end = str(v.get("start", "00:00")), str(v.get("end", "23:59"))
    for t in (start, end):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ContactError(f"Times must be HH:MM, got {t}.")
    return {"days": days, "start": start, "end": end}


def create(store, display_name: str, mode: str = "disabled", discovered: bool = False, **fields) -> dict:
    data = _validate({"display_name": display_name, "mode": mode, **fields})
    now = time.time()
    cid = uuid.uuid4().hex[:12]
    cols = {"id": cid, "discovered": 1 if discovered else 0, "created_at": now, "updated_at": now, **data}
    store.execute(f"INSERT INTO contacts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))
    store.execute("INSERT INTO contact_policies (contact_id, channel_id, capabilities, updated_at) VALUES (?,?,?,?)",
                  (cid, "", json.dumps(CONTACT_DEFAULTS), now))
    return get(store, cid)


def update(store, contact_id: str, patch: dict, actor: str = "owner") -> dict:
    current = get(store, contact_id)
    if not current:
        raise LookupError("Contact not found.")
    data = _validate(patch)
    if data:
        if "mode" in data and data["mode"] != "disabled":
            data["discovered"] = 0  # enabling a discovered sender makes them a real contact
        sets = ", ".join(f"{k}=?" for k in data)
        store.execute(f"UPDATE contacts SET {sets}, updated_at=? WHERE id=?", [*data.values(), time.time(), contact_id])
        if "mode" in data or "paused" in data:
            audit.record(store, actor, "contact.access", contact_id,
                         mode=data.get("mode", current["mode"]), paused=bool(data.get("paused", current["paused"])))
    return get(store, contact_id)


def delete(store, contact_id: str) -> None:
    """Removes the contact, its identities, policies, memories, channel conversations and their messages."""
    convs = store.rows("SELECT conversation_id FROM channel_conversations WHERE contact_id=?", (contact_id,))
    for c in convs:
        store.delete_conversation(c["conversation_id"])
    store.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    audit.record(store, "owner", "contact.delete", contact_id)


def set_owner(store, contact_id: str) -> dict:
    if not get(store, contact_id):
        raise LookupError("Contact not found.")
    store.execute("UPDATE contacts SET is_owner=0 WHERE is_owner=1 AND id<>?", (contact_id,))
    store.execute("UPDATE contacts SET is_owner=1, discovered=0, updated_at=? WHERE id=?", (time.time(), contact_id))
    audit.record(store, "owner", "contact.set_owner", contact_id)
    return get(store, contact_id)


def touch(store, contact_id: str) -> None:
    store.execute("UPDATE contacts SET last_interaction_at=? WHERE id=?", (time.time(), contact_id))


# ------------------------------------------------------------------------------ identities
def add_identity(store, contact_id: str, channel_id: str, external_id: str, phone: str = "",
                 display_name: str = "", verified: bool = False) -> dict:
    external_id = external_id.strip()
    if not external_id:
        raise ContactError("Enter the contact's number or ID on that channel.")
    taken = store.row("SELECT contact_id FROM contact_identities WHERE channel_id=? AND external_id=?", (channel_id, external_id))
    if taken and taken["contact_id"] != contact_id:
        other = store.row("SELECT display_name FROM contacts WHERE id=?", (taken["contact_id"],))
        raise ContactError(f"That {channel_id} identity already belongs to {other['display_name'] if other else 'another contact'}.")
    if not taken:
        store.execute("INSERT INTO contact_identities (id, contact_id, channel_id, external_id, phone, display_name, verified, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:12], contact_id, channel_id, external_id, digits(phone),
                                                  display_name[:120], 1 if verified else 0, time.time()))
    return store.row("SELECT * FROM contact_identities WHERE channel_id=? AND external_id=?", (channel_id, external_id))


def remove_identity(store, contact_id: str, identity_id: str) -> None:
    store.execute("DELETE FROM contact_identities WHERE id=? AND contact_id=?", (identity_id, contact_id))


def resolve(store, channel_id: str, external_id: str, phone: str = "", display_name: str = "") -> tuple[dict, bool]:
    """The contact behind a sender, creating a disabled, discovered contact for anyone unknown.
    A sender matched by phone number (e.g. a WhatsApp @lid id) gets that id linked to the contact."""
    phone = digits(phone)
    ident = store.row("SELECT * FROM contact_identities WHERE channel_id=? AND external_id=?", (channel_id, external_id))
    if not ident and phone:
        ident = store.row("SELECT * FROM contact_identities WHERE channel_id=? AND phone=? ORDER BY created_at LIMIT 1",
                          (channel_id, phone))
        if ident:
            add_identity(store, ident["contact_id"], channel_id, external_id, phone, display_name)
    if ident:
        if display_name and not ident["display_name"]:
            store.execute("UPDATE contact_identities SET display_name=? WHERE id=?", (display_name[:120], ident["id"]))
        return get(store, ident["contact_id"]), False
    name = display_name or (f"+{phone}" if phone else external_id)
    contact = create(store, name, mode="disabled", discovered=True)
    add_identity(store, contact["id"], channel_id, external_id, phone, display_name)
    audit.record(store, f"channel:{channel_id}", "contact.discovered", contact["id"], external_id=external_id)
    return get(store, contact["id"]), True


# ------------------------------------------------------------------------------ policies
def policies(store, contact_id: str) -> dict:
    """{channel_id: capabilities}; "" is the default for every channel."""
    out = {"": dict(CONTACT_DEFAULTS)}
    for r in store.rows("SELECT channel_id, capabilities FROM contact_policies WHERE contact_id=?", (contact_id,)):
        caps = json.loads(r["capabilities"] or "{}")
        # Defaults sit under the saved default row, so capabilities added later (e.g. requests) apply to
        # existing contacts too; anything the owner set, including "off", wins.
        out[r["channel_id"]] = {**CONTACT_DEFAULTS, **caps} if r["channel_id"] == "" else caps
    return out


def set_policy(store, contact_id: str, channel_id: str, capabilities: dict, actor: str = "owner") -> dict:
    if not get(store, contact_id):
        raise LookupError("Contact not found.")
    try:
        caps = clean_capabilities(capabilities)
    except ValueError as e:
        raise ContactError(str(e)) from e
    store.execute("INSERT INTO contact_policies (contact_id, channel_id, capabilities, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(contact_id, channel_id) DO UPDATE SET capabilities=excluded.capabilities, updated_at=excluded.updated_at",
                  (contact_id, channel_id or "", json.dumps(caps), time.time()))
    audit.record(store, actor, "contact.policy", contact_id, channel=channel_id or "all", capabilities=caps)
    return policies(store, contact_id)


def clear_policy(store, contact_id: str, channel_id: str) -> None:
    if channel_id:  # the default ("") row always stays
        store.execute("DELETE FROM contact_policies WHERE contact_id=? AND channel_id=?", (contact_id, channel_id))


def effective_capabilities(store, contact_id: str, channel_id: str) -> dict:
    p = policies(store, contact_id)
    return {**p.get("", {}), **p.get(channel_id, {})}


# ------------------------------------------------------------------------------ working hours
def in_working_hours(contact: dict, default_tz: str, now: datetime | None = None) -> bool:
    hours = contact.get("working_hours") or {}
    if not hours:
        return True
    try:
        tz = ZoneInfo(contact.get("timezone") or default_tz or "UTC")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    local = (now or datetime.now(tz)).astimezone(tz)
    if local.weekday() not in hours.get("days", range(7)):
        return False
    hm = local.strftime("%H:%M")
    start, end = hours.get("start", "00:00"), hours.get("end", "23:59")
    return start <= hm <= end if start <= end else (hm >= start or hm <= end)  # windows may cross midnight
