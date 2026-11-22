"""The owner's availability ("status"), and the greeting + quick menu contacts get while the owner is away.

The effective status is, in order: a status the owner set by hand (until it expires), the event on the owner's
Google Calendar right now (when calendar_auto_status is on), the sleep hours, else "available". While the owner
isn't available, a saved contact who writes without the trigger word gets one greeting per status period that
says why the owner is busy, plus a menu (a WhatsApp poll; numbered text elsewhere). Picking an option starts a
guided flow that ends in a request for the owner (contact_requests), so details are collected one at a time and
confirmed instead of guessed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import identity

log = logging.getLogger("donna.availability")

MODES: dict[str, dict] = {
    "available": {"emoji": "🟢", "label": "Available"},
    "sleeping": {"emoji": "😴", "label": "Sleeping"},
    "working": {"emoji": "💻", "label": "Working on a task"},
    "dnd": {"emoji": "🔕", "label": "Do not disturb"},
    "office": {"emoji": "🏢", "label": "At office"},
    "leave": {"emoji": "🌴", "label": "On leave"},
    "custom": {"emoji": "✏️", "label": "Custom"},
    "meeting": {"emoji": "📅", "label": "In a meeting"},  # from the calendar only
}
MANUAL_MODES = tuple(m for m in MODES if m != "meeting")
# Words the owner may use in "status …" from their own chat.
ALIASES = {"available": "available", "free": "available", "back": "available", "online": "available",
           "sleeping": "sleeping", "sleep": "sleeping", "asleep": "sleeping",
           "working": "working", "work": "working", "busy": "working",
           "dnd": "dnd", "do not disturb": "dnd", "office": "office", "at office": "office",
           "leave": "leave", "on leave": "leave", "vacation": "leave", "holiday": "leave"}

MENU: dict[str, dict] = {
    "message": {"emoji": "📩", "label": "Pass a message", "request": "personal_message"},
    "meeting": {"emoji": "📅", "label": "Schedule a meeting", "request": "meeting"},
    "callback": {"emoji": "📞", "label": "Request a call back", "request": "callback"},
    "urgent": {"emoji": "⚡", "label": "It's urgent", "request": "urgent"},
}
DEFAULT_MENU = [{"id": k, "enabled": True} for k in MENU]
MENU_OPEN_SECONDS = 2 * 3600   # a reply of "2" counts as a menu choice this long after the greeting
FLOW_SECONDS = 30 * 60         # a guided flow ends after this long without a message
CALENDAR_CACHE_SECONDS = 120


class AvailabilityError(ValueError):
    """A user-facing validation error."""


# ------------------------------------------------------------------------------ time helpers
def _tz(settings: dict) -> ZoneInfo:
    try:
        return ZoneInfo(settings.get("timezone") or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _hhmm(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise AvailabilityError(f"Use HH:MM (24-hour) for times, not {value!r}.")
    return int(m.group(1)), int(m.group(2))


def until_text(until: float | None, settings: dict, now: float | None = None) -> str:
    """" until 6:00 PM" / " until tomorrow 9:00 AM" / " until Mon 24 Nov" (empty when open-ended)."""
    if not until:
        return ""
    tz = _tz(settings)
    end = datetime.fromtimestamp(until, tz)
    today = datetime.fromtimestamp(now or time.time(), tz).date()
    clock = end.strftime("%I:%M %p").lstrip("0")
    if end.date() == today:
        return f" until {clock}"
    if end.date() == today + timedelta(days=1):
        return f" until tomorrow {clock}"
    return f" until {end.strftime('%a %d %b').replace(' 0', ' ')}"


# ------------------------------------------------------------------------------ the effective status
def _status(mode: str, detail: str, until: float | None, source: str, key: str) -> dict:
    meta = MODES.get(mode, MODES["custom"])
    return {"mode": mode, "detail": detail, "until": until, "source": source, "key": key,
            "emoji": meta["emoji"], "label": meta["label"], "away": mode != "available"}


def _sleep_window(settings: dict, now: float) -> tuple[float, str] | None:
    """(end of the sleep window, key) when `now` is inside the owner's sleep hours."""
    sh = settings.get("sleep_hours") or {}
    if not sh.get("enabled"):
        return None
    try:
        (sh_h, sh_m), (eh, em) = _hhmm(sh.get("start", "23:00")), _hhmm(sh.get("end", "07:00"))
    except AvailabilityError:
        return None
    tz = _tz(settings)
    local = datetime.fromtimestamp(now, tz)
    for day in (local.date() - timedelta(days=1), local.date()):  # a window that started yesterday evening, or today
        start = datetime(day.year, day.month, day.day, sh_h, sh_m, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
        if end <= start:
            end += timedelta(days=1)
        if start <= local < end:
            return end.timestamp(), f"s:{start.date().isoformat()}"
    return None


def compute(settings: dict, now: float | None = None, event: dict | None = None) -> dict:
    """The effective status from settings and the calendar event happening now ({"id", "end"} or None)."""
    now = now or time.time()
    manual = settings.get("availability") or {}
    mode, until = manual.get("mode") or "available", manual.get("until")
    if mode in MODES and (not until or until > now) and (mode != "available" or until):
        return _status(mode, str(manual.get("detail") or ""), until, "manual", f"m:{mode}:{manual.get('set_at', 0)}")
    if event and settings.get("calendar_auto_status", True):
        return _status("meeting", "", event["end"], "calendar", f"c:{event['id']}")
    if window := _sleep_window(settings, now):
        return _status("sleeping", "", window[0], "sleep_hours", window[1])
    return _status("available", "", None, "default", "available")


_event_cache: dict = {"t": 0.0, "value": None}


def _calendar_now() -> dict | None:
    """The busy, timed event on the owner's primary calendar right now (blocking; Google API)."""
    from tools.google_auth import service
    svc = service("calendar", "v3")
    now = datetime.now(ZoneInfo("UTC"))
    items = svc.events().list(calendarId="primary", timeMin=now.isoformat(), timeMax=(now + timedelta(seconds=60)).isoformat(),
                              singleEvents=True, orderBy="startTime", maxResults=10).execute().get("items", [])
    for e in items:
        start, end = (e.get("start") or {}).get("dateTime"), (e.get("end") or {}).get("dateTime")
        if not start or not end or e.get("transparency") == "transparent" or e.get("status") == "cancelled":
            continue  # all-day events and events marked "free" don't make the owner busy
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees", [])):
            continue
        s, en = datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(end.replace("Z", "+00:00"))
        if s <= now < en:
            return {"id": e.get("id", ""), "end": en.timestamp()}
    return None


async def current_event(settings: dict) -> dict | None:
    if not settings.get("calendar_auto_status", True):
        return None
    if time.time() - _event_cache["t"] < CALENDAR_CACHE_SECONDS:
        ev = _event_cache["value"]
        return ev if not ev or ev["end"] > time.time() else None
    try:
        ev = await asyncio.to_thread(_calendar_now)
    except Exception as e:  # noqa: BLE001 - Google not connected or offline: no calendar status
        log.info("Calendar status unavailable: %s", e)
        ev = None
    _event_cache.update(t=time.time(), value=ev)
    return ev


async def effective(store, now: float | None = None) -> dict:
    settings = store.settings()
    return compute(settings, now, await current_event(settings))


# ------------------------------------------------------------------------------ setting it
def set_status(store, mode: str, detail: str = "", until: float | None = None) -> dict:
    """The owner sets a status by hand (dashboard or chat). Returns the saved manual status."""
    mode = (mode or "").strip().lower()
    if mode not in MANUAL_MODES:
        raise AvailabilityError(f"Status must be one of: {', '.join(MANUAL_MODES)}.")
    detail = " ".join(str(detail or "").split())[:120]
    if mode == "custom" and not detail:
        raise AvailabilityError("Describe a custom status, e.g. \"travelling to Pune\".")
    if until is not None:
        until = float(until)
        if until <= time.time():
            raise AvailabilityError("\"Until\" must be in the future.")
    value = {"mode": mode, "detail": detail if mode in ("working", "custom") else "", "until": until, "set_at": time.time()}
    store.set_settings(availability=value)
    return value


def _parse_until(text: str, settings: dict, now: float) -> float | None:
    """"7am", "7:30 pm", "19:00", "tomorrow 9am", "30 min", "2 hours" → epoch seconds."""
    text = text.strip().lower()
    if m := re.fullmatch(r"(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)", text):
        n = int(m.group(1))
        return now + n * (3600 if m.group(2).startswith("h") else 60)
    tz = _tz(settings)
    local = datetime.fromtimestamp(now, tz)
    tomorrow = text.startswith("tomorrow")
    text = text.removeprefix("tomorrow").strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        raise AvailabilityError(f"I couldn't read the time \"{text}\". Try 7am, 18:30, tomorrow 9am or 2 hours.")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        raise AvailabilityError(f"\"{text}\" isn't a valid time.")
    end = local.replace(hour=h, minute=mi, second=0, microsecond=0)
    if tomorrow:
        end += timedelta(days=1)
    elif end <= local:
        end += timedelta(days=1)  # "until 7am" said at night means tomorrow morning
    return end.timestamp()


STATUS_CMD = re.compile(r"^\s*status\b\s*(.*)$", re.I | re.S)


def parse_command(text: str, settings: dict, now: float | None = None) -> tuple[str, str, float | None] | None:
    """"status sleeping until 7am" / "status working on the launch until 6pm" / "status available" →
    (mode, detail, until). None when the text isn't a status command; raises AvailabilityError when it's malformed."""
    m = STATUS_CMD.match(text or "")
    if not m:
        return None
    now = now or time.time()
    rest = m.group(1).strip().rstrip(".!")
    until = None
    if u := re.search(r"\s+(?:until|till|for)\s+(.+)$", rest, re.I):
        until = _parse_until(u.group(1), settings, now)
        rest = rest[:u.start()].strip()
    if not rest:
        raise AvailabilityError("Say which status, e.g. \"status sleeping until 7am\" or \"status available\".")
    low = rest.lower()
    if low.startswith("working on "):
        return "working", rest[len("working on "):].strip(), until
    for word, mode in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if low == word:
            return mode, "", until
    return "custom", rest, until


# ------------------------------------------------------------------------------ what contacts are told
def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2].isalpha() and text[1:2].islower() else text


def phrase(status: dict, settings: dict, store=None, now: float | None = None) -> str:
    """"Yashraj is working on the Q4 launch until 6:00 PM"."""
    owner = identity.owner_name(settings, store)
    detail = _lower_first(status.get("detail") or "")
    what = {"sleeping": "is sleeping right now", "working": f"is working on {detail}" if detail else "is busy with work",
            "dnd": "isn't taking messages right now", "office": "is at the office", "leave": "is on leave",
            "meeting": "is in a meeting", "custom": f"is {detail}" if detail else "is unavailable right now",
            "available": "is available"}.get(status["mode"], "is unavailable right now")
    return f"{owner} {what}{until_text(status.get('until'), settings, now)}"


def menu_options(settings: dict) -> list[dict]:
    """The enabled menu options, in the owner's order: [{"id", "emoji", "label", "request"}]."""
    out, seen = [], set()
    for item in settings.get("menu_options") or DEFAULT_MENU:
        oid = item.get("id") if isinstance(item, dict) else None
        if oid in MENU and oid not in seen and item.get("enabled", True):
            seen.add(oid)
            out.append({"id": oid, **MENU[oid]})
    return out


def option_text(opt: dict) -> str:
    return f"{opt['emoji']} {opt['label']}"


def greeting(contact: dict, settings: dict, store, status: dict, now: float | None = None) -> str:
    """The away greeting: who Donna is, why the owner is busy, and how to reach her."""
    addr = identity.profile(contact, settings)["address_as"]
    owner = identity.owner_name(settings, store)
    lines = [f"Hi {addr}! I'm {identity.ASSISTANT}, {owner}'s personal AI assistant 👋",
             f"{phrase(status, settings, store, now)}."]
    tip = f" or type {identity.trigger_of(settings)} anytime to talk to me" if settings.get("greeting_mention_trigger", True) \
        and settings.get("require_trigger", True) else ""
    if menu_options(settings):
        lines.append(f"How can I help? Tap an option below{tip}.")
    elif tip:
        lines.append(f"You can type {identity.trigger_of(settings)} anytime to talk to me.")
    return "\n".join(lines)


MENU_QUESTION = "How can I help?"


def menu_fallback(options: list[dict]) -> str:
    """The numbered text version (channels without polls, and the "reply 1-4" hint)."""
    return f"{MENU_QUESTION}\n" + "\n".join(f"{i}. {option_text(o)}" for i, o in enumerate(options, 1)) + \
        f"\nReply with a number (1–{len(options)})."


def parse_choice(text: str, options: list[dict]) -> dict | None:
    """A menu choice typed as a reply: "2", "2.", "meeting", "📅 Schedule a meeting"."""
    t = re.sub(r"[^\w\s']", " ", (text or "").lower()).strip()
    if not t or len(t) > 40:
        return None
    if t.isdigit():
        n = int(t)
        return options[n - 1] if 1 <= n <= len(options) else None
    words = {"message": ("message", "pass a message", "pass message"), "meeting": ("meeting", "schedule a meeting", "meet"),
             "callback": ("callback", "call back", "call", "request a call back"), "urgent": ("urgent", "it's urgent", "its urgent")}
    for o in options:
        if t in words.get(o["id"], ()) or t == o["label"].lower():
            return o
    return None


def option_by_vote(selected: str, options: list[dict]) -> dict | None:
    """A poll vote carries the option's text: match it back to the menu."""
    s = (selected or "").strip().lower()
    return next((o for o in options if s in (option_text(o).lower(), o["label"].lower())), None)


def flow_instructions(option_id: str, settings: dict, store) -> str:
    """What Donna does after a menu choice: collect one detail at a time, confirm, then record the request."""
    owner = identity.owner_name(settings, store)
    minutes = int(settings.get("meeting_default_minutes") or 30)
    common = (f"Ask one short question at a time. Don't promise anything on {owner}'s behalf. "
              "Before recording, read back exactly what you'll pass on and ask them to reply yes; "
              "record it only after they confirm.")
    if option_id == "message":
        return (f"[They tapped \"Pass a message\" in Donna's menu. Ask what they'd like you to tell {owner}. Then repeat it back "
                f"word for word in quotes and ask them to reply yes to send it. Once they confirm, call record_request with "
                f"type personal_message (the message itself in details.message). {common}]")
    if option_id == "callback":
        return (f"[They tapped \"Request a call back\" in Donna's menu. Ask what it's about, then when suits them for {owner} "
                f"to call (as an exact local time if they give one). Then read it back and ask them to reply yes. Once they "
                f"confirm, call record_request with type callback. {common}]")
    if option_id == "urgent":
        return (f"[They tapped \"It's urgent\" in Donna's menu. Ask in one question what's urgent. As soon as they've said it, "
                f"call record_request with type urgent and urgency urgent (no confirmation step: speed matters). Then tell them "
                f"it's marked urgent and, if it can't wait, they can call {owner} directly.]")
    if option_id == "meeting":
        return (f"[They tapped \"Schedule a meeting\" in Donna's menu. Collect, one question at a time: (1) what the meeting is "
                f"about; (2) the day and time: call suggest_meeting_slots first and offer two or three of those free slots, "
                f"and if they want another time, check it with suggest_meeting_slots too; (3) how long (default {minutes} "
                f"minutes); (4) an email for the calendar invite (optional, they can say skip). Then read back a summary "
                f"(day, date, time, length, topic, email) and ask them to reply yes to send it to {owner} for approval. "
                f"Once they confirm, call record_request with type meeting, start (YYYY-MM-DDTHH:MM), duration_minutes, "
                f"and details {{\"topic\": …, \"email\": …}}. Tell them {owner} will approve it and you'll send the Google Meet "
                f"link here once it's booked. {common}]")
    return ""


def _busy(t_min: datetime, t_max: datetime) -> list[tuple[datetime, datetime]]:
    """Busy blocks on the owner's primary calendar (blocking; free/busy only, never event details)."""
    from tools.google_auth import service
    res = service("calendar", "v3").freebusy().query(body={"timeMin": t_min.isoformat(), "timeMax": t_max.isoformat(),
                                                           "items": [{"id": "primary"}]}).execute()
    return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")), datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            for b in res.get("calendars", {}).get("primary", {}).get("busy", [])]


def free_slots(settings: dict, day: str = "", at: str = "", minutes: int = 0, limit: int = 6,
               busy_fn=None, now: datetime | None = None) -> str:
    """Free meeting slots within the owner's meeting hours (blocking). `at` checks one exact time; `day` lists that
    day's free slots; neither lists the next few working days. Returns text for the model."""
    tz = _tz(settings)
    minutes = int(minutes or settings.get("meeting_default_minutes") or 30)
    if not 5 <= minutes <= 480:
        raise AvailabilityError("duration_minutes must be between 5 and 480.")
    hours = settings.get("meeting_hours") or {}
    (sh, sm), (eh, em) = _hhmm(hours.get("start", "10:00")), _hhmm(hours.get("end", "18:00"))
    days_ok = set(hours.get("days", [0, 1, 2, 3, 4]))
    now = now or datetime.now(tz)
    earliest = now + timedelta(hours=1)
    busy_fn = busy_fn or _busy

    def fmt(dt: datetime) -> str:
        return dt.strftime("%a %d %b, %I:%M %p").replace(" 0", " ").replace(", 0", ", ")

    if at:
        try:
            start = datetime.fromisoformat(at.strip().replace(" ", "T")[:16]).replace(tzinfo=tz)
        except ValueError as e:
            raise AvailabilityError("at must be YYYY-MM-DDTHH:MM (local time).") from e
        end = start + timedelta(minutes=minutes)
        if start < earliest:
            return f"{fmt(start)} is too soon or has passed; offer a later time."
        clash = [b for b in busy_fn(start, end) if b[0] < end and b[1] > start]
        inside = start.weekday() in days_ok and (sh, sm) <= (start.hour, start.minute) and \
            (end.hour, end.minute) <= (eh, em) and end.date() == start.date()
        if clash:
            return f"{fmt(start)} for {minutes} min is NOT free. Offer one of the free slots instead (call again without `at`)."
        return f"{fmt(start)} for {minutes} min is free" + ("." if inside else " (outside the usual meeting hours; that's OK if they insist).")
    if day:
        try:
            d = datetime.fromisoformat(day.strip()[:10]).date()
        except ValueError as e:
            raise AvailabilityError("day must be YYYY-MM-DD.") from e
        dates = [d]
    else:
        dates, d = [], now.date()
        while len(dates) < 5:
            if d.weekday() in days_ok:
                dates.append(d)
            d += timedelta(days=1)
    t_min = datetime(dates[0].year, dates[0].month, dates[0].day, sh, sm, tzinfo=tz)
    t_max = datetime(dates[-1].year, dates[-1].month, dates[-1].day, eh, em, tzinfo=tz)
    busy = busy_fn(max(t_min, earliest), t_max) if t_max > earliest else []
    slots: list[datetime] = []
    for d in dates:
        start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz)
        end_of_day = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz)
        day_slots = 0
        while start + timedelta(minutes=minutes) <= end_of_day and len(slots) < limit and day_slots < (limit if day else 2):
            end = start + timedelta(minutes=minutes)
            if start >= earliest and not any(b[0] < end and b[1] > start for b in busy):
                slots.append(start)
                day_slots += 1
                start += timedelta(minutes=max(minutes, 60) if not day else minutes)  # spread suggestions out
            else:
                start += timedelta(minutes=30)
    if not slots:
        return "No free slots in that window. Ask them for another day."
    return (f"Free {minutes}-minute slots ({settings.get('timezone') or 'UTC'}): " + "; ".join(fmt(s) for s in slots)
            + ". Offer two or three of these; use the exact YYYY-MM-DDTHH:MM when recording.")


def urgent_ping_allowed(settings: dict, status: dict) -> bool:
    """Whether an urgent request also pings the owner's phone (it always reaches the Inbox)."""
    if status["mode"] == "dnd":
        return bool(settings.get("urgent_notify_dnd", False))
    if status["mode"] == "sleeping":
        return bool(settings.get("urgent_notify_sleeping", False))
    return True
