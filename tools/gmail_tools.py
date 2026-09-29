# tools/gmail_tools.py — Gmail management tools
#
# Google API calls are blocking, so each tool runs its work in a thread
# (asyncio.to_thread) to keep the server responsive while streaming.

import asyncio
import base64
from email.message import EmailMessage

from mcp_instance import mcp
from tools.google_auth import GoogleAuthError, service


def _gmail():
    return service("gmail", "v1")


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _raw(m: EmailMessage) -> str:
    return base64.urlsafe_b64encode(m.as_bytes()).decode("utf-8")


def _build(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> EmailMessage:
    m = EmailMessage()
    m.set_content(body)
    m["To"] = to
    m["Subject"] = subject
    if cc:
        m["Cc"] = cc
    if bcc:
        m["Bcc"] = bcc
    return m


def _reply_message(svc, email_id: str, body: str, reply_all: bool) -> tuple[EmailMessage, str]:
    """Build a reply that Gmail threads correctly: threadId + In-Reply-To + References + Re: subject."""
    orig = svc.users().messages().get(
        userId="me", id=email_id, format="metadata",
        metadataHeaders=["Message-ID", "References", "Subject", "From", "Reply-To", "To", "Cc"],
    ).execute()
    h = _headers(orig)
    subject = h.get("subject", "")
    m = EmailMessage()
    m.set_content(body)
    m["To"] = h.get("reply-to") or h.get("from", "")
    if reply_all:
        me = svc.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
        others = [a.strip() for a in f"{h.get('to', '')},{h.get('cc', '')}".split(",") if a.strip() and me not in a.lower()]
        if others:
            m["Cc"] = ", ".join(others)
    m["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if h.get("message-id"):
        m["In-Reply-To"] = h["message-id"]
        m["References"] = f"{h.get('references', '')} {h['message-id']}".strip()
    return m, orig["threadId"]


def _extract_body(payload: dict) -> str:
    """Recursively extract the text body from an email payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace") if data else ""

    parts = payload.get("parts", [])
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif part_mime.startswith("multipart/"):
            text = _extract_body(part)
            if text:
                return text

    # Fallback: HTML if there is no plain-text part
    for part in parts:
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                return "[HTML content]\n" + base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return "(No readable body content)"


async def _run(fn, what: str) -> str:
    try:
        return await asyncio.to_thread(fn)
    except GoogleAuthError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error {what}: {e}"


@mcp.tool()
async def list_emails(query: str = "", max_results: int = 10) -> str:
    """
    List or search emails. `query` uses Gmail search syntax, e.g. "is:unread",
    "from:someone@example.com", "subject:meeting", "newer_than:1d", or "" for the latest.
    max_results: 1-20 (default 10). Each result includes the email ID used by other tools.
    """
    def work():
        svc = _gmail()
        res = svc.users().messages().list(userId="me", q=query, maxResults=max(1, min(max_results, 20))).execute()
        messages = res.get("messages", [])
        if not messages:
            return "No emails found matching your query."
        out = []
        for info in messages:
            msg = svc.users().messages().get(userId="me", id=info["id"], format="metadata",
                                             metadataHeaders=["From", "Subject", "Date"]).execute()
            h = _headers(msg)
            state = "UNREAD" if "UNREAD" in msg.get("labelIds", []) else "read"
            out.append(f"ID: {info['id']} | {state}\n  From: {h.get('from', 'N/A')}\n  Subject: {h.get('subject', '(no subject)')}\n"
                       f"  Date: {h.get('date', 'N/A')}\n  Preview: {msg.get('snippet', '')[:100]}")
        return "\n\n".join(out)
    return await _run(work, "listing emails")


@mcp.tool()
async def read_email(email_id: str) -> str:
    """Read the full content of one email by its ID (get IDs from list_emails or search_emails)."""
    def work():
        msg = _gmail().users().messages().get(userId="me", id=email_id, format="full").execute()
        h = _headers(msg)
        state = "UNREAD" if "UNREAD" in msg.get("labelIds", []) else "read"
        return (f"ID: {email_id} | Thread: {msg.get('threadId')} | {state}\nFrom: {h.get('from', 'N/A')}\n"
                f"To: {h.get('to', 'N/A')}\nSubject: {h.get('subject', '(no subject)')}\nDate: {h.get('date', 'N/A')}\n\n"
                f"{_extract_body(msg.get('payload', {}))[:6000]}")
    return await _run(work, "reading email")


@mcp.tool()
async def send_email(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Compose and send a new email. `to`, `cc`, `bcc` accept comma-separated addresses."""
    def work():
        sent = _gmail().users().messages().send(userId="me", body={"raw": _raw(_build(to, subject, body, cc, bcc))}).execute()
        return f"Email sent to {to}. Message ID: {sent.get('id')}"
    return await _run(work, "sending email")


@mcp.tool()
async def reply_email(email_id: str, body: str, reply_all: bool = False) -> str:
    """Reply in the same thread to the email with this ID. Set reply_all=true to include everyone on the thread."""
    def work():
        svc = _gmail()
        m, thread_id = _reply_message(svc, email_id, body, reply_all)
        sent = svc.users().messages().send(userId="me", body={"raw": _raw(m), "threadId": thread_id}).execute()
        return f"Reply sent to {m['To']} in thread {thread_id}. Message ID: {sent.get('id')}"
    return await _run(work, "replying")


@mcp.tool()
async def forward_email(email_id: str, to: str, note: str = "") -> str:
    """Forward the email with this ID to `to`, with an optional note above the original message."""
    def work():
        svc = _gmail()
        orig = svc.users().messages().get(userId="me", id=email_id, format="full").execute()
        h = _headers(orig)
        subject = h.get("subject", "")
        quoted = (f"{note}\n\n---------- Forwarded message ---------\nFrom: {h.get('from', '')}\nDate: {h.get('date', '')}\n"
                  f"Subject: {subject}\nTo: {h.get('to', '')}\n\n{_extract_body(orig.get('payload', {}))}")
        m = _build(to, subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}", quoted.strip())
        sent = svc.users().messages().send(userId="me", body={"raw": _raw(m)}).execute()
        return f"Forwarded to {to}. Message ID: {sent.get('id')}"
    return await _run(work, "forwarding")


@mcp.tool()
async def get_unread_emails(max_results: int = 10) -> str:
    """Latest unread emails in the inbox (1-20, default 10)."""
    return await list_emails(query="is:unread in:inbox", max_results=max_results)


@mcp.tool()
async def search_emails(query: str, max_results: int = 10) -> str:
    """
    Search emails with Gmail search syntax, e.g. "from:boss@company.com subject:urgent",
    "has:attachment newer_than:7d", "label:important", "invoice after:2026/09/01".
    """
    return await list_emails(query=query, max_results=max_results)


@mcp.tool()
async def mark_email(email_id: str, read: bool = True) -> str:
    """Mark an email as read (read=true) or unread (read=false)."""
    def work():
        body = {"removeLabelIds": ["UNREAD"]} if read else {"addLabelIds": ["UNREAD"]}
        _gmail().users().messages().modify(userId="me", id=email_id, body=body).execute()
        return f"Email {email_id} marked as {'read' if read else 'unread'}."
    return await _run(work, "updating email")


@mcp.tool()
async def delete_email(email_id: str) -> str:
    """Move an email to the trash (it can be restored from Gmail's Trash for 30 days)."""
    def work():
        _gmail().users().messages().trash(userId="me", id=email_id).execute()
        return f"Email {email_id} moved to trash."
    return await _run(work, "deleting email")


@mcp.tool()
async def create_draft(to: str, subject: str = "", body: str = "", reply_to_email_id: str = "") -> str:
    """
    Save an email draft without sending it. To draft a reply, pass reply_to_email_id
    (subject and threading are filled in automatically). Returns the draft ID for send_draft.
    """
    def work():
        svc = _gmail()
        if reply_to_email_id:
            m, thread_id = _reply_message(svc, reply_to_email_id, body, reply_all=False)
            if to:
                del m["To"]
                m["To"] = to
            message = {"raw": _raw(m), "threadId": thread_id}
        else:
            message = {"raw": _raw(_build(to, subject, body))}
        d = svc.users().drafts().create(userId="me", body={"message": message}).execute()
        return f"Draft saved. Draft ID: {d['id']}"
    return await _run(work, "creating draft")


@mcp.tool()
async def list_drafts(max_results: int = 10) -> str:
    """List saved drafts with their draft IDs, recipients and subjects."""
    def work():
        svc = _gmail()
        drafts = svc.users().drafts().list(userId="me", maxResults=max(1, min(max_results, 20))).execute().get("drafts", [])
        if not drafts:
            return "No drafts."
        out = []
        for d in drafts:
            full = svc.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
            h = _headers(full.get("message", {}))
            out.append(f"Draft ID: {d['id']} | To: {h.get('to', '—')} | Subject: {h.get('subject', '(no subject)')}")
        return "\n".join(out)
    return await _run(work, "listing drafts")


@mcp.tool()
async def send_draft(draft_id: str) -> str:
    """Send a saved draft by its draft ID."""
    def work():
        sent = _gmail().users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return f"Draft sent. Message ID: {sent.get('id')}"
    return await _run(work, "sending draft")
