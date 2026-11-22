"""Read side of channel conversations for the dashboard Inbox (the ChannelManager writes them)."""
from __future__ import annotations

import json
import time


def conversations(store, channel_id: str = "", contact_id: str = "", status: str = "active", q: str = "",
                  ccid: str = "") -> list[dict]:
    sql = ("SELECT cc.*, c.display_name AS contact_name, c.mode AS contact_mode, c.is_owner AS contact_is_owner, "
           "(SELECT content FROM channel_messages m WHERE m.channel_conversation_id=cc.id ORDER BY created_at DESC LIMIT 1) AS preview, "
           "(SELECT COUNT(*) FROM channel_messages m JOIN channel_events e ON e.id=m.event_id "
           "  WHERE m.channel_conversation_id=cc.id AND e.status='failed') AS failed, "
           "(SELECT COUNT(*) FROM approvals a WHERE a.channel_conversation_id=cc.id AND a.status='pending') AS pending_approvals "
           "FROM channel_conversations cc JOIN contacts c ON c.id=cc.contact_id WHERE 1=1")
    params: list = []
    if ccid:
        sql += " AND cc.id=?"
        params.append(ccid)
    if channel_id:
        sql += " AND cc.channel_id=?"
        params.append(channel_id)
    if contact_id:
        sql += " AND cc.contact_id=?"
        params.append(contact_id)
    if status in ("active", "archived"):
        sql += " AND cc.status=?"
        params.append(status)
    if q:
        sql += " AND (c.display_name LIKE ? OR EXISTS (SELECT 1 FROM channel_messages m WHERE m.channel_conversation_id=cc.id AND m.content LIKE ?))"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY COALESCE(cc.last_message_at, cc.created_at) DESC LIMIT 300"
    rows = store.rows(sql, params)
    for r in rows:
        r["escalated"], r["contact_is_owner"] = bool(r["escalated"]), bool(r["contact_is_owner"])
    return rows


def conversation(store, ccid: str) -> dict | None:
    rows = conversations(store, status="any", ccid=ccid)
    return rows[0] if rows else None


def messages(store, ccid: str, limit: int = 300) -> list[dict]:
    rows = store.rows(
        "SELECT m.*, e.status AS event_status, e.result AS event_result FROM channel_messages m "
        "LEFT JOIN channel_events e ON e.id=m.event_id WHERE m.channel_conversation_id=? "
        "ORDER BY m.created_at DESC LIMIT ?", (ccid, limit))
    for r in rows:
        r["attachments"] = json.loads(r["attachments"] or "[]")
    return list(reversed(rows))


def mark_read(store, ccid: str) -> None:
    store.execute("UPDATE channel_conversations SET unread=0, escalated=0 WHERE id=?", (ccid,))


def set_status(store, ccid: str, status: str) -> None:
    store.execute("UPDATE channel_conversations SET status=?, updated_at=? WHERE id=?", (status, time.time(), ccid))


def delete(store, ccid: str) -> None:
    row = store.row("SELECT conversation_id FROM channel_conversations WHERE id=?", (ccid,))
    if row:
        store.delete_conversation(row["conversation_id"])  # cascades to the channel conversation and its messages


def counts(store) -> dict:
    return {
        "unread": store.row("SELECT COALESCE(SUM(unread), 0) AS n FROM channel_conversations WHERE status='active'")["n"],
        "escalated": store.row("SELECT COUNT(*) AS n FROM channel_conversations WHERE escalated=1")["n"],
        "failed": store.row("SELECT COUNT(*) AS n FROM channel_events WHERE status='failed'")["n"],
    }
