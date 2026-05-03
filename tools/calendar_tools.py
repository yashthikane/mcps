# tools/calendar_tools.py — Google Calendar tools

import os
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from mcp_instance import mcp

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Authenticate and return a Google Calendar API service object."""
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    return service


@mcp.tool()
def get_events() -> str:
    """Get upcoming 5 calendar events"""

    service = get_calendar_service()
    now = datetime.now(timezone.utc).isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=5,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    if not events:
        return "No upcoming events found."

    result = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        result.append(f"{start} - {event['summary']}")

    return "\n".join(result)


@mcp.tool()
def create_event(summary: str, start_time: str, end_time: str) -> str:
    """
    Create a calendar event.
    Time format: YYYY-MM-DDTHH:MM:SS
    Example: 2026-05-01T10:00:00
    """

    service = get_calendar_service()

    event = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end_time, "timeZone": "Asia/Kolkata"},
    }

    event = service.events().insert(
        calendarId="primary",
        body=event
    ).execute()

    return f"Event created: {event.get('htmlLink')}"
