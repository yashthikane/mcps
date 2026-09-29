# tools/calendar_tools.py — Google Calendar tools
#
# Times are interpreted in the calendar's own timezone (read once from the
# Calendar settings API), so "09:00" means 09:00 where the user lives.

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from mcp_instance import mcp
from tools.google_auth import GoogleAuthError, service

_tz_cache: str | None = None


def _cal():
    return service("calendar", "v3")


def _timezone(svc) -> str:
    global _tz_cache
    if not _tz_cache:
        try:
            _tz_cache = svc.settings().get(setting="timezone").execute()["value"]
        except Exception:
            _tz_cache = "UTC"
    return _tz_cache


def _is_date(value: str) -> bool:
    return len(value.strip()) == 10


def _time_field(value: str, tz: str) -> dict:
    """'2026-10-01' → all-day; '2026-10-01T09:00' / '...:00' → timed in the calendar timezone."""
    value = value.strip()
    if _is_date(value):
        return {"date": value}
    return {"dateTime": datetime.fromisoformat(value).replace(tzinfo=None).isoformat(timespec="seconds"), "timeZone": tz}


def _rfc3339(value: str, tz: str, end_of_day: bool = False) -> str:
    """Date or datetime string → RFC3339 with offset, as events.list requires."""
    value = value.strip()
    if _is_date(value):
        d = date.fromisoformat(value) + (timedelta(days=1) if end_of_day else timedelta())
        dt = datetime(d.year, d.month, d.day)
    else:
        dt = datetime.fromisoformat(value).replace(tzinfo=None)
    return dt.replace(tzinfo=ZoneInfo(tz)).isoformat()


def _fmt(event: dict) -> str:
    start = event["start"].get("dateTime", event["start"].get("date"))
    end = event["end"].get("dateTime", event["end"].get("date"))
    parts = [f"{start} → {end} | {event.get('summary', '(no title)')} | ID: {event['id']}"]
    if event.get("location"):
        parts.append(f"  Location: {event['location']}")
    if event.get("attendees"):
        parts.append("  Attendees: " + ", ".join(a.get("email", "") for a in event["attendees"]))
    return "\n".join(parts)


async def _run(fn, what: str) -> str:
    try:
        return await asyncio.to_thread(fn)
    except GoogleAuthError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error {what}: {e}"


@mcp.tool()
async def get_events(max_results: int = 5) -> str:
    """Upcoming events on the primary calendar (default 5, max 25), each with its event ID."""
    def work():
        svc = _cal()
        items = svc.events().list(calendarId="primary", timeMin=datetime.now().astimezone().isoformat(),
                                  maxResults=max(1, min(max_results, 25)), singleEvents=True,
                                  orderBy="startTime").execute().get("items", [])
        if not items:
            return "No upcoming events found."
        return f"Calendar timezone: {_timezone(svc)}\n" + "\n".join(_fmt(e) for e in items)
    return await _run(work, "reading events")


@mcp.tool()
async def list_events(start_date: str, end_date: str = "", query: str = "") -> str:
    """
    Events between two dates (inclusive), e.g. start_date="2026-10-01", end_date="2026-10-07".
    Datetimes like "2026-10-01T14:00" also work. end_date defaults to the same day.
    `query` optionally filters by text in the title, description or location.
    """
    def work():
        svc = _cal()
        tz = _timezone(svc)
        kwargs = dict(calendarId="primary", timeMin=_rfc3339(start_date, tz),
                      timeMax=_rfc3339(end_date or start_date, tz, end_of_day=True),
                      singleEvents=True, orderBy="startTime", maxResults=100)
        if query:
            kwargs["q"] = query
        items = svc.events().list(**kwargs).execute().get("items", [])
        if not items:
            return f"No events between {start_date} and {end_date or start_date}."
        return f"Calendar timezone: {tz}\n" + "\n".join(_fmt(e) for e in items)
    return await _run(work, "listing events")


@mcp.tool()
async def create_event(summary: str, start_time: str, end_time: str, description: str = "",
                       location: str = "", attendees: str = "") -> str:
    """
    Create a calendar event. Times use the calendar's timezone:
    timed event "2026-10-01T09:00", all-day event "2026-10-01" (end_time is the day after the last day).
    attendees: comma-separated emails (they receive invitations).
    """
    def work():
        svc = _cal()
        tz = _timezone(svc)
        body = {"summary": summary, "start": _time_field(start_time, tz), "end": _time_field(end_time, tz)}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a.strip()} for a in attendees.split(",") if a.strip()]
        event = svc.events().insert(calendarId="primary", body=body,
                                    sendUpdates="all" if attendees else "none").execute()
        return f"Event created: {_fmt(event)}\nLink: {event.get('htmlLink')}"
    return await _run(work, "creating event")


@mcp.tool()
async def update_event(event_id: str, summary: str = "", start_time: str = "", end_time: str = "",
                       description: str = "", location: str = "") -> str:
    """Change an existing event. Only the fields you pass are changed (same time formats as create_event)."""
    def work():
        svc = _cal()
        tz = _timezone(svc)
        body = {}
        if summary:
            body["summary"] = summary
        if start_time:
            body["start"] = _time_field(start_time, tz)
        if end_time:
            body["end"] = _time_field(end_time, tz)
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if not body:
            return "Nothing to change: pass at least one field."
        event = svc.events().patch(calendarId="primary", eventId=event_id, body=body, sendUpdates="all").execute()
        return f"Event updated: {_fmt(event)}"
    return await _run(work, "updating event")


@mcp.tool()
async def delete_event(event_id: str) -> str:
    """Delete an event from the primary calendar by its event ID."""
    def work():
        _cal().events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
        return f"Event {event_id} deleted."
    return await _run(work, "deleting event")
