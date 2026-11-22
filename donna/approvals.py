"""Persisted approvals for actions that come in through a channel (SQLite `approvals`).

The web chat approves live, inside the run (agent.Run.pending). A channel turn can't wait for a
click, so a gated action becomes an approval record instead, and the owner decides later in the
dashboard or by replying "approve A7K2" / "reject A7K2" in their own chat.

Guarantees:
- bound to the exact payload: kind + tool + arguments are hashed when the request is made and
  checked again before anything runs;
- single use: pending → executing is one conditional UPDATE, so a second approve (a double click,
  a retried request, the dashboard and WhatsApp at once) finds nothing to claim;
- expiry: an approval past expires_at can't be claimed and is marked expired;
- revocation: a contact's current mode and capabilities are checked again at execution time.
"""
from __future__ import annotations

import hashlib
import json
import random
import time

from . import audit, contacts, permissions

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: the owner may type the code
DONE = ("executed", "failed", "rejected", "expired")


class ApprovalError(ValueError):
    """The approval can't be used (unknown, expired, already decided, revoked)."""


def payload_hash(kind: str, tool: str, args: dict) -> str:
    raw = json.dumps({"kind": kind, "tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def describe(tool: str, args: dict) -> str:
    """A one-line, human description of a tool call for the owner."""
    keys = ("summary", "title", "to", "subject", "start_time", "end_time", "attendees", "event_id", "contact", "when", "text", "note")
    parts = [f"{k}: {str(args[k])[:80]}" for k in keys if args.get(k) not in (None, "", [])]
    if not parts:
        parts = [f"{k}: {str(v)[:60]}" for k, v in list(args.items())[:4]]
    return f"{tool} ({'; '.join(parts)})" if parts else tool


def _code(store) -> str:
    for _ in range(50):
        code = "".join(random.choice(ALPHABET) for _ in range(4))
        if not store.row("SELECT 1 FROM approvals WHERE id=?", (code,)):
            return code
    raise RuntimeError("Couldn't allocate an approval code.")


def _out(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["args"] = json.loads(r.get("args") or "{}")
    return r


def create(store, ctx, kind: str, tool: str = "", args: dict | None = None, description: str = "") -> dict:
    args = args or {}
    ttl = float(store.settings().get("approval_ttl_hours") or 24)
    now = time.time()
    code = _code(store)
    contact = (ctx.contact or {}) if ctx else {}
    store.execute(
        "INSERT INTO approvals (id, kind, audience, channel_id, contact_id, channel_conversation_id, tool, args, payload_hash, "
        "description, status, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
        (code, kind, ctx.audience if ctx else "owner", ctx.channel_id if ctx else "", contact.get("id"),
         ctx.channel_conversation_id if ctx else None, tool, json.dumps(args, default=str), payload_hash(kind, tool, args),
         (description or describe(tool, args))[:500], now + ttl * 3600, now))
    actor = f"contact:{contact['id']}" if contact and ctx.audience == "contact" else "owner"
    audit.record(store, actor, "approval.requested", code, kind=kind, tool=tool, args=args)
    return get(store, code)


def expire(store) -> int:
    cur = store.execute("UPDATE approvals SET status='expired', decided_at=? WHERE status='pending' AND expires_at<=?",
                        (time.time(), time.time()))
    return cur.rowcount


def get(store, code: str) -> dict | None:
    return _out(store.row("SELECT * FROM approvals WHERE id=?", ((code or "").upper(),)))


def list_all(store, status: str | None = None, limit: int = 100) -> list[dict]:
    expire(store)
    sql = ("SELECT a.*, c.display_name AS contact_name FROM approvals a LEFT JOIN contacts c ON c.id=a.contact_id"
           + (" WHERE a.status=?" if status else "") + " ORDER BY a.created_at DESC LIMIT ?")
    return [_out(r) for r in store.rows(sql, (status, limit) if status else (limit,))]


def pending_count(store) -> int:
    expire(store)
    return store.row("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")["n"]


def claim(store, code: str, decided_by: str) -> dict:
    """pending → executing, atomically. Raises ApprovalError when it can't be used."""
    expire(store)
    code = (code or "").upper()
    cur = store.execute("UPDATE approvals SET status='executing', decided_at=?, decided_by=? "
                        "WHERE id=? AND status='pending' AND expires_at>?", (time.time(), decided_by, code, time.time()))
    row = get(store, code)
    if cur.rowcount != 1:
        if not row:
            raise ApprovalError(f"There's no approval {code}.")
        raise ApprovalError(f"Approval {code} is {row['status']}; it can't be approved again.")
    if payload_hash(row["kind"], row["tool"], row["args"]) != row["payload_hash"]:
        finish(store, code, "failed", "The request changed after it was made, so it wasn't run.", decided_by)
        raise ApprovalError(f"Approval {code} doesn't match the action that was requested.")
    return row


def still_allowed(store, row: dict, tool) -> str | None:
    """None when the contact may still do this; otherwise why not (revoked since the request)."""
    if row["audience"] != "contact":
        return None
    contact = contacts.get(store, row["contact_id"]) if row["contact_id"] else None
    if not contact:
        return "The contact was deleted."
    if contact["paused"] or contact["mode"] == "disabled":
        return f"{contact['display_name']} is disabled or paused."
    if row["kind"] == "reply":
        return None
    ctx = permissions.TurnContext(audience="contact", channel_id=row["channel_id"], contact=contact,
                                  capabilities=contacts.effective_capabilities(store, contact["id"], row["channel_id"]))
    if tool is None or permissions.decide(ctx, tool, row["args"]) == "deny":
        return f"{contact['display_name']} isn't allowed to do that any more."
    return None


def finish(store, code: str, status: str, result: str, decided_by: str = "") -> dict:
    store.execute("UPDATE approvals SET status=?, result=?, decided_at=COALESCE(decided_at, ?), "
                  "decided_by=CASE WHEN decided_by='' THEN ? ELSE decided_by END WHERE id=?",
                  (status, (result or "")[:4000], time.time(), decided_by, code))
    row = get(store, code)
    audit.record(store, decided_by or "system", f"approval.{status}", code, tool=row["tool"] if row else "",
                 outcome="ok" if status == "executed" else "failed" if status == "failed" else "rejected",
                 result=(result or "")[:500])
    return row


def reject(store, code: str, decided_by: str) -> dict:
    expire(store)
    code = (code or "").upper()
    cur = store.execute("UPDATE approvals SET status='rejected', decided_at=?, decided_by=? WHERE id=? AND status='pending'",
                        (time.time(), decided_by, code))
    row = get(store, code)
    if cur.rowcount != 1:
        if not row:
            raise ApprovalError(f"There's no approval {code}.")
        raise ApprovalError(f"Approval {code} is already {row['status']}.")
    audit.record(store, decided_by, "approval.rejected", code, tool=row["tool"], outcome="rejected")
    return row
