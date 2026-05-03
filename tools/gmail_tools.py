# tools/gmail_tools.py — Gmail management tools

import os
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from mcp_instance import mcp

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

GMAIL_TOKEN_FILE = "gmail_token.json"


def get_gmail_service():
    """Authenticate and return a Gmail API service object."""
    creds = None

    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    return service


@mcp.tool()
def list_emails(query: str = "", max_results: int = 10) -> str:
    """
    List or search emails from Gmail inbox.
    Use Gmail search syntax for the query, e.g.:
      - "is:unread" for unread emails
      - "from:someone@example.com" to filter by sender
      - "subject:meeting" to search by subject
      - "newer_than:1d" for emails from the last day
      - "" (empty) for the latest emails
    max_results: number of emails to return (default 10, max 20).
    """
    try:
        service = get_gmail_service()
        max_results = min(max_results, 20)

        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "No emails found matching your query."

        output = []
        for msg_info in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_info["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            snippet = msg.get("snippet", "")
            labels = msg.get("labelIds", [])
            unread = "📩 UNREAD" if "UNREAD" in labels else "✅ Read"

            output.append(
                f"ID: {msg_info['id']} | {unread}\n"
                f"  From: {headers.get('From', 'N/A')}\n"
                f"  Subject: {headers.get('Subject', '(no subject)')}\n"
                f"  Date: {headers.get('Date', 'N/A')}\n"
                f"  Preview: {snippet[:100]}..."
            )

        return "\n\n".join(output)
    except Exception as e:
        return f"Error listing emails: {str(e)}"


@mcp.tool()
def read_email(email_id: str) -> str:
    """
    Read the full content of a specific email by its ID.
    Use list_emails first to get the email ID.
    """
    try:
        service = get_gmail_service()
        msg = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body
        body = _extract_body(msg.get("payload", {}))

        labels = msg.get("labelIds", [])
        unread = "📩 UNREAD" if "UNREAD" in labels else "✅ Read"

        output = (
            f"--- EMAIL ---\n"
            f"Status: {unread}\n"
            f"From: {headers.get('From', 'N/A')}\n"
            f"To: {headers.get('To', 'N/A')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', 'N/A')}\n"
            f"\n--- BODY ---\n{body}"
        )

        return output
    except Exception as e:
        return f"Error reading email: {str(e)}"


def _extract_body(payload: dict) -> str:
    """Recursively extract the text body from an email payload."""
    body_text = ""

    mime_type = payload.get("mimeType", "")

    # Direct text body
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return body_text

    # Multipart — recurse into parts
    parts = payload.get("parts", [])
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                return body_text
        elif part_mime.startswith("multipart/"):
            body_text = _extract_body(part)
            if body_text:
                return body_text

    # Fallback: try HTML if no plain text found
    for part in parts:
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                return f"[HTML content]\n{body_text}"

    return body_text or "(No readable body content)"


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """
    Compose and send an email.
    to: recipient email address
    subject: email subject line
    body: email body text
    """
    try:
        service = get_gmail_service()

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return f"Email sent successfully! Message ID: {sent.get('id')}"
    except Exception as e:
        return f"Error sending email: {str(e)}"


@mcp.tool()
def get_unread_emails(max_results: int = 10) -> str:
    """
    Get the latest unread emails from your inbox.
    max_results: number of unread emails to return (default 10, max 20).
    """
    return list_emails(query="is:unread", max_results=max_results)


@mcp.tool()
def search_emails(query: str, max_results: int = 10) -> str:
    """
    Search emails using natural language or Gmail search syntax.
    Examples:
      - "invoices from last month"
      - "from:boss@company.com subject:urgent"
      - "has:attachment newer_than:7d"
      - "label:important"
    """
    return list_emails(query=query, max_results=max_results)


@mcp.tool()
def delete_email(email_id: str) -> str:
    """
    Move an email to trash by its ID.
    Use list_emails or search_emails first to get the email ID.
    """
    try:
        service = get_gmail_service()
        service.users().messages().trash(userId="me", id=email_id).execute()
        return f"Email {email_id} moved to trash successfully."
    except Exception as e:
        return f"Error deleting email: {str(e)}"
