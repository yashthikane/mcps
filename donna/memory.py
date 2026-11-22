"""Memory: global notes about the owner, per-contact notes, and channel conversation summaries.

Isolation rules (enforced here, not by the prompt):
- owner turns (web, the owner's own chat) see global memories;
- a contact's turn sees only that contact's memories (and, when the contact doesn't share memory
  across channels, only the ones learnt on the current channel), never global ones;
- what a contact says can only ever be written to that contact's memory.
"""
from __future__ import annotations

import time
import uuid

from . import llm

MAX_CHARS = 500
PROMPT_ITEMS = 30
SUMMARY_TRIGGER_CHARS = 12000  # unsummarised history beyond this is folded into the summary
SUMMARY_KEEP = 8               # the newest messages always stay verbatim


def _clean(content: str) -> str:
    content = " ".join(str(content or "").split())[:MAX_CHARS]
    if not content:
        raise ValueError("A memory can't be empty.")
    return content


def add(store, content: str, scope: str = "global", contact_id: str | None = None, channel_id: str = "",
        source: str = "owner") -> dict:
    if scope not in ("global", "contact") or (scope == "contact") != bool(contact_id):
        raise ValueError("Contact memories need a contact; global ones must not have one.")
    now = time.time()
    mid = uuid.uuid4().hex[:10]
    store.execute("INSERT INTO memories (id, scope, contact_id, channel_id, content, source, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (mid, scope, contact_id, channel_id, _clean(content), source, now, now))
    return store.row("SELECT * FROM memories WHERE id=?", (mid,))


def list_global(store) -> list[dict]:
    return store.rows("SELECT * FROM memories WHERE scope='global' ORDER BY created_at DESC")


def list_contact(store, contact_id: str, channel_id: str | None = None) -> list[dict]:
    if channel_id is None:
        return store.rows("SELECT * FROM memories WHERE scope='contact' AND contact_id=? ORDER BY created_at DESC", (contact_id,))
    return store.rows("SELECT * FROM memories WHERE scope='contact' AND contact_id=? AND channel_id IN ('', ?) "
                      "ORDER BY created_at DESC", (contact_id, channel_id))


def delete(store, memory_id: str, contact_id: str | None = None) -> bool:
    if contact_id:
        cur = store.execute("DELETE FROM memories WHERE id=? AND contact_id=?", (memory_id, contact_id))
    else:
        cur = store.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    return cur.rowcount > 0


def for_turn(store, ctx) -> list[dict]:
    """The memories a turn may see (see the module docstring)."""
    if ctx is None or ctx.audience == "owner":
        return list_global(store)[:PROMPT_ITEMS]
    contact = ctx.contact or {}
    channel = None if contact.get("share_memory", True) else ctx.channel_id
    return list_contact(store, contact["id"], channel)[:PROMPT_ITEMS]


def remember(store, ctx, note: str) -> dict:
    """The `remember` tool: the scope comes from the turn, never from the model's arguments."""
    if ctx is None or ctx.audience == "owner":
        return add(store, note, "global", source="owner" if ctx is None else "donna")
    contact = ctx.contact or {}
    channel = "" if contact.get("share_memory", True) else ctx.channel_id
    return add(store, note, "contact", contact["id"], channel, source="donna")


def prompt_block(items: list[dict], heading: str) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {m['content']}" for m in reversed(items))
    return f"\n{heading}\n{lines}"


# ------------------------------------------------------------------------------ summaries
async def summarize(store, channel_conversation: dict, force: bool = False) -> str | None:
    """Fold older messages of a channel conversation into its summary. Returns the new summary,
    or None when there wasn't enough new history (one small Groq call when there is)."""
    msgs = [m for m in store.messages(channel_conversation["conversation_id"])
            if m["created_at"] > channel_conversation.get("summary_upto", 0)]
    older = msgs[:-SUMMARY_KEEP] if len(msgs) > SUMMARY_KEEP else ([] if not force else msgs)
    if not older or (not force and sum(len(m["content"] or "") for m in msgs) < SUMMARY_TRIGGER_CHARS):
        return None
    transcript = "\n".join(f"{'Them' if m['role'] == 'user' else 'Donna'}: {m['content'][:800]}" for m in older)
    prompt = [
        {"role": "system", "content": "You summarise a chat so an assistant can continue it later. Write at most 8 short "
                                      "bullet points: open questions, commitments, dates, preferences. No preamble."},
        {"role": "user", "content": (f"Earlier summary:\n{channel_conversation.get('summary') or '(none)'}\n\n"
                                     f"New messages:\n{transcript}")},
    ]
    settings = store.settings()
    text = await llm.complete_with(settings, prompt)
    if not text.strip():
        return None
    store.execute("UPDATE channel_conversations SET summary=?, summary_upto=?, updated_at=? WHERE id=?",
                  (text.strip()[:3000], older[-1]["created_at"], time.time(), channel_conversation["id"]))
    return text.strip()
