"""Notification service: one `send()` that fans out to the channels a task asked for.

Channels:
- windows: a Windows toast (WinRT via PowerShell, no extra dependency). Best effort.
- in_app:  a row in PostgreSQL `notifications`, which the UI polls and shows as a toast.

Add a channel by writing an async `send(note)` class and registering it in `Notifier.channels`.
"""
import asyncio
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from xml.sax.saxutils import escape

from .pg import Postgres

log = logging.getLogger("donna.notify")
DEFAULT_CHANNELS = ["windows", "in_app"]

# PowerShell's own AppUserModelID: toasts need a registered app ID and this one always exists.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
# The toast XML arrives through an environment variable, so task text is never parsed as script.
_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:DONNA_TOAST_XML);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:DONNA_TOAST_APP)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


@dataclass
class Note:
    title: str
    body: str = ""
    level: str = "info"  # info | warn | error
    task_id: str | None = None
    execution_id: str | None = None
    conversation_id: str | None = None


class WindowsToast:
    async def send(self, note: Note) -> None:
        if os.name != "nt":
            return
        xml = ("<toast><visual><binding template='ToastGeneric'>"
               f"<text>{escape(note.title[:120])}</text><text>{escape(note.body[:400])}</text>"
               "</binding></visual></toast>")
        env = {**os.environ, "DONNA_TOAST_XML": xml, "DONNA_TOAST_APP": _APP_ID}

        def show() -> None:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS], env=env,
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        await asyncio.to_thread(show)


class InApp:
    def __init__(self, pg: Postgres):
        self.pg = pg

    async def send(self, note: Note) -> None:
        await self.pg.run(
            "INSERT INTO notifications (id, task_id, execution_id, title, body, level, conversation_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), note.task_id, note.execution_id, note.title, note.body, note.level, note.conversation_id))


class Notifier:
    def __init__(self, pg: Postgres):
        self.pg = pg
        self.channels = {"windows": WindowsToast(), "in_app": InApp(pg)}

    async def send(self, note: Note, channels: list[str] | None = None) -> list[str]:
        """Deliver to each channel; one failing channel doesn't stop the others. Returns the ones that worked."""
        delivered = []
        for name in channels or DEFAULT_CHANNELS:
            channel = self.channels.get(name)
            if not channel:
                continue
            try:
                await channel.send(note)
                delivered.append(name)
            except Exception as e:  # noqa: BLE001
                log.warning("Notification channel %s failed: %s", name, type(e).__name__)
        return delivered

    # ------------------------------------------------------------------ in-app feed
    async def unread(self, limit: int = 20) -> list[dict]:
        rows = await self.pg.all(
            "SELECT id, task_id, execution_id, title, body, level, conversation_id, created_at FROM notifications "
            "WHERE read_at IS NULL ORDER BY created_at LIMIT %s", (limit,))
        return [_row(r) for r in rows]

    async def mark_read(self, ids: list[str] | None = None) -> int:
        """Mark the given notifications read, or all unread ones when `ids` is None."""
        if ids is None:
            return await self.pg.run("UPDATE notifications SET read_at = NOW() WHERE read_at IS NULL")
        try:
            uuids = [uuid.UUID(i) for i in ids]
        except ValueError:
            return 0
        return await self.pg.run("UPDATE notifications SET read_at = NOW() WHERE id = ANY(%s) AND read_at IS NULL", (uuids,))


def _row(r: dict) -> dict:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
