"""Scheduled tasks: CRUD in PostgreSQL, scheduling through the Node scheduler, and execution.

FastAPI owns what a task is and how it runs (reminder → notification, ai_task → the agent).
The scheduler service owns when it runs: recurrence math, next_run_at and the BullMQ job.
Every change here ends with `sync`, which makes the scheduler's queue match PostgreSQL.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import llm, vault
from .agent import MODES, RUNS, Run, run_chat
from .notify import DEFAULT_CHANNELS, Note, Notifier
from .pg import Postgres
from .scheduler_client import SchedulerClient, SchedulerUnavailable

log = logging.getLogger("donna.tasks")

TYPES = ("reminder", "ai_task")
RECURRENCE = ("once", "interval", "cron")
CHANNELS = ("windows", "in_app")
EDITABLE = ("title", "description", "message", "prompt", "run_at", "timezone", "recurrence_type",
            "recurrence_rule", "notification_channels", "permission_mode")
SCHEDULE_FIELDS = ("run_at", "timezone", "recurrence_type", "recurrence_rule")
RESULT_CHARS = 4000

_RELATIVE = re.compile(r"^\s*in\s+(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\s*$", re.I)


class TaskError(ValueError):
    """A user-facing validation error (HTTP 400)."""


# ------------------------------------------------------------------------------ parsing
def tz_or_error(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as e:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError
        raise TaskError(f"Unknown timezone: {name}") from e


def parse_when(value: str, tz_name: str, now: datetime | None = None) -> datetime:
    """A timezone-aware instant from "2026-10-01T09:00" (local to `tz_name`), an ISO timestamp
    with an offset (kept as given), or "in 30 minutes" / "in 2 hours". Never assumes UTC."""
    now = now or datetime.now(timezone.utc)
    value = (value or "").strip()
    if not value:
        raise TaskError("Give a time, for example 2026-10-01T09:00 or 'in 30 minutes'.")
    if m := _RELATIVE.match(value):
        n, unit = int(m.group(1)), m.group(2)[0].lower()
        return now + timedelta(**{"m": {"minutes": n}, "h": {"hours": n}, "d": {"days": n}}[unit])
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise TaskError(f"Couldn't read the time '{value}'. Use YYYY-MM-DDTHH:MM (local time).") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_or_error(tz_name))
    return dt


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _out(r: dict) -> dict:
    return {k: (str(v) if isinstance(v, uuid.UUID) else _iso(v) if isinstance(v, datetime) else v) for k, v in r.items()}


def _task_out(r: dict) -> dict:
    out = _out(r)
    payload = out.get("payload") or {}
    out["message"] = payload.get("message", "")
    out["prompt"] = payload.get("prompt", "")
    out["conversation_id"] = payload.get("conversation_id")
    out["permission_mode"] = payload.get("permission_mode", "manual")
    return out


def _uuid(task_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(task_id))
    except ValueError as e:
        raise LookupError("Task not found.") from e


def local_time(dt: datetime | None, tz_name: str) -> str:
    if not dt:
        return "—"
    try:
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%a %d %b %Y, %H:%M")
    except Exception:  # noqa: BLE001
        return dt.isoformat()


# ------------------------------------------------------------------------------ service
class TaskService:
    def __init__(self, pg: Postgres, scheduler: SchedulerClient, notifier: Notifier, store, hub):
        self.pg = pg
        self.scheduler = scheduler
        self.notifier = notifier
        self.store = store
        self.hub = hub

    # ------------------------------------------------------------------ helpers
    def default_timezone(self) -> str:
        return self.store.settings().get("timezone") or "UTC"

    async def _row(self, task_id: str) -> dict:
        row = await self.pg.one("SELECT * FROM scheduled_tasks WHERE id = %s", (_uuid(task_id),))
        if not row:
            raise LookupError("Task not found.")
        return row

    async def _sync(self, task_id: str) -> str | None:
        """Ask the scheduler to (re)queue the task. Returns a warning when it's unreachable; the
        task stays in PostgreSQL and the scheduler's reconcile loop queues it when it's back."""
        try:
            await self.scheduler.sync(task_id)
            return None
        except SchedulerUnavailable as e:
            log.warning("Task %s saved but not queued: %s", task_id, e)
            return f"{e} The task is saved and will be queued when the scheduler is back."

    def _normalise(self, data: dict, current: dict | None = None) -> dict:
        """Validate a create body or a patch merged over the current row. Raises TaskError."""
        cur = current or {}
        payload = dict(cur.get("payload") or {})
        title = str(data.get("title", cur.get("title", "")) or "").strip()
        if not title:
            raise TaskError("Give the task a title.")
        ttype = data.get("type", cur.get("type", "reminder"))
        if ttype not in TYPES:
            raise TaskError("Type must be reminder or ai_task.")
        tz_name = str(data.get("timezone") or cur.get("timezone") or self.default_timezone())
        tz_or_error(tz_name)
        rtype = data.get("recurrence_type", cur.get("recurrence_type", "once"))
        if rtype not in RECURRENCE:
            raise TaskError("Recurrence must be once, interval or cron.")
        rule = data.get("recurrence_rule", cur.get("recurrence_rule"))
        rule = (str(rule).strip() or None) if rule is not None else None
        if rtype == "once":
            rule = None
        elif not rule:
            raise TaskError("Recurring tasks need a rule: an interval like 30m / 2h / 1d, or a cron like 0 9 * * *.")
        if "run_at" in data:
            run_at = parse_when(data["run_at"], tz_name) if data["run_at"] else None
        else:
            run_at = cur.get("run_at")
        if rtype in ("once", "interval") and not run_at:
            raise TaskError("Set when it should run.")
        if "message" in data:
            payload["message"] = str(data["message"] or "").strip()
        if "prompt" in data:
            payload["prompt"] = str(data["prompt"] or "").strip()
        if "permission_mode" in data:
            mode = str(data["permission_mode"] or "manual").lower()
            if mode not in MODES:
                raise TaskError("Permission mode must be plan, manual or auto.")
            payload["permission_mode"] = mode
        if ttype == "ai_task" and not payload.get("prompt"):
            raise TaskError("An AI task needs a prompt: what should Donna do?")
        channels = data.get("notification_channels", cur.get("notification_channels")) or list(DEFAULT_CHANNELS)
        if not isinstance(channels, list) or any(c not in CHANNELS for c in channels):
            raise TaskError("Notification channels must be windows and/or in_app.")
        description = data.get("description", cur.get("description"))
        return {"title": title[:200], "description": (str(description).strip()[:2000] or None) if description else None,
                "type": ttype, "timezone": tz_name, "recurrence_type": rtype, "recurrence_rule": rule,
                "run_at": run_at, "payload": payload, "notification_channels": channels}

    async def preview(self, data: dict, count: int = 3) -> list[str]:
        v = self._normalise({"title": "preview", "prompt": "preview", **data})
        return await self.scheduler.preview({"run_at": _iso(v["run_at"]), "timezone": v["timezone"],
                                             "recurrence_type": v["recurrence_type"],
                                             "recurrence_rule": v["recurrence_rule"]}, count)

    # ------------------------------------------------------------------ CRUD
    async def create(self, data: dict) -> dict:
        await self.pg.ensure()
        v = self._normalise(data)
        if v["recurrence_type"] == "once" and v["run_at"] < datetime.now(timezone.utc) - timedelta(minutes=1):
            raise TaskError(f"That time has already passed ({local_time(v['run_at'], v['timezone'])}).")
        await self.preview(data)  # the scheduler validates interval/cron rules; 503 if it's down
        task_id = uuid.uuid4()
        await self.pg.run(
            "INSERT INTO scheduled_tasks (id, title, description, type, status, run_at, timezone, recurrence_type, "
            "recurrence_rule, payload, notification_channels) VALUES (%s,%s,%s,%s,'scheduled',%s,%s,%s,%s,%s,%s)",
            (task_id, v["title"], v["description"], v["type"], v["run_at"], v["timezone"], v["recurrence_type"],
             v["recurrence_rule"], Jsonb(v["payload"]), Jsonb(v["notification_channels"])))
        warning = await self._sync(str(task_id))
        task = await self.get(str(task_id))
        return {**task, "warning": warning} if warning else task

    async def list(self, status: str | None = None) -> list[dict]:
        await self.pg.ensure()
        rows = await self.pg.all(
            "SELECT t.*, e.status AS last_status, e.result AS last_result, e.error AS last_error, "
            "e.completed_at AS last_completed_at FROM scheduled_tasks t "
            "LEFT JOIN LATERAL (SELECT status, result, error, completed_at FROM task_executions "
            "  WHERE task_id = t.id ORDER BY created_at DESC LIMIT 1) e ON TRUE "
            "WHERE (%(status)s::text IS NULL OR t.status = %(status)s) "
            "ORDER BY (t.status IN ('scheduled', 'running', 'paused')) DESC, t.next_run_at NULLS LAST, t.created_at DESC",
            {"status": status or None})
        return [_task_out(r) for r in rows]

    async def get(self, task_id: str, executions: int = 20) -> dict:
        await self.pg.ensure()
        task = _task_out(await self._row(task_id))
        rows = await self.pg.all(
            "SELECT id, scheduled_for, trigger, status, attempts, started_at, completed_at, result, error, conversation_id "
            "FROM task_executions WHERE task_id = %s ORDER BY scheduled_for DESC LIMIT %s", (_uuid(task_id), executions))
        task["executions"] = [_out(r) for r in rows]
        return task

    async def update(self, task_id: str, patch: dict) -> dict:
        await self.pg.ensure()
        patch = {k: v for k, v in patch.items() if k in EDITABLE}
        current = await self._row(task_id)
        v = self._normalise(patch, current)
        schedule_changed = any(k in patch for k in SCHEDULE_FIELDS)
        if schedule_changed:
            await self.preview({**patch, "type": v["type"], "timezone": v["timezone"],
                                "recurrence_type": v["recurrence_type"], "recurrence_rule": v["recurrence_rule"],
                                "run_at": _iso(v["run_at"])})
        status = current["status"]
        if schedule_changed and status in ("completed", "failed", "cancelled"):
            status = "scheduled"  # a new time reactivates a finished task
        await self.pg.run(
            "UPDATE scheduled_tasks SET title=%s, description=%s, type=%s, status=%s, run_at=%s, timezone=%s, "
            "recurrence_type=%s, recurrence_rule=%s, payload=%s, notification_channels=%s, "
            "next_run_at = CASE WHEN %s THEN NULL ELSE next_run_at END, updated_at=NOW() WHERE id=%s",
            (v["title"], v["description"], v["type"], status, v["run_at"], v["timezone"], v["recurrence_type"],
             v["recurrence_rule"], Jsonb(v["payload"]), Jsonb(v["notification_channels"]), schedule_changed,
             _uuid(task_id)))
        warning = await self._sync(task_id) if schedule_changed or status != current["status"] else None
        task = await self.get(task_id)
        return {**task, "warning": warning} if warning else task

    async def _set_status(self, task_id: str, status: str, clear_next: bool) -> dict:
        await self.pg.ensure()
        await self._row(task_id)
        await self.pg.run(
            "UPDATE scheduled_tasks SET status=%s, next_run_at = CASE WHEN %s THEN NULL ELSE next_run_at END, "
            "updated_at=NOW() WHERE id=%s", (status, clear_next, _uuid(task_id)))
        warning = await self._sync(task_id)
        task = await self.get(task_id)
        return {**task, "warning": warning} if warning else task

    async def cancel(self, task_id: str) -> dict:
        return await self._set_status(task_id, "cancelled", True)

    async def pause(self, task_id: str) -> dict:
        return await self._set_status(task_id, "paused", True)

    async def resume(self, task_id: str) -> dict:
        return await self._set_status(task_id, "scheduled", True)  # next run is recomputed from now

    async def snooze(self, task_id: str, minutes: int) -> dict:
        """Push the upcoming occurrence back. A recurring task returns to its rule afterwards."""
        if not 1 <= int(minutes) <= 7 * 24 * 60:
            raise TaskError("Snooze between 1 minute and 7 days.")
        await self.pg.ensure()
        now = datetime.now(timezone.utc)
        row = await self._row(task_id)
        base = row["next_run_at"] if row["next_run_at"] and row["next_run_at"] > now else now
        new = base + timedelta(minutes=int(minutes))
        await self.pg.run(
            "UPDATE scheduled_tasks SET status='scheduled', next_run_at=%s, "
            "run_at = CASE WHEN recurrence_type='once' THEN %s ELSE run_at END, updated_at=NOW() WHERE id=%s",
            (new, new, _uuid(task_id)))
        warning = await self._sync(task_id)
        task = await self.get(task_id)
        return {**task, "warning": warning} if warning else task

    async def delete(self, task_id: str) -> None:
        await self.pg.ensure()
        try:
            await self.scheduler.unschedule(task_id)
        except (SchedulerUnavailable, LookupError):
            pass  # the worker skips jobs whose task no longer exists
        await self._row(task_id)
        await self.pg.run("DELETE FROM scheduled_tasks WHERE id=%s", (_uuid(task_id),))

    async def run_now(self, task_id: str) -> dict:
        """Queue an immediate run. It goes through BullMQ and the worker like a scheduled one."""
        await self.pg.ensure()
        await self._row(task_id)
        return {"ok": True, "job_id": await self.scheduler.run(task_id)}

    # ------------------------------------------------------------------ execution
    async def execute(self, task_id: str, execution_id: str, scheduled_for: datetime, mode: str = "run") -> dict:
        """Called by the scheduler's worker. Returns {success, status, result, retryable, conversation_id}."""
        await self.pg.ensure()
        task = _task_out(await self._row(task_id))
        when = local_time(scheduled_for, task["timezone"])
        channels = task["notification_channels"] or list(DEFAULT_CHANNELS)
        note = {"task_id": task["id"], "execution_id": execution_id}

        if mode == "missed":
            await self.notifier.send(Note(f"Missed reminder: {task['title']}",
                                          f"It was due {when}, while Donna wasn't running.", "warn", **note), channels)
            return {"success": True, "status": "missed", "result": f"Missed-reminder notice sent (was due {when})."}

        if task["type"] == "reminder":  # deterministic: no Groq call
            delivered = await self.notifier.send(Note(task["title"], task["message"] or f"Reminder · {when}", **note), channels)
            if not delivered:
                return {"success": False, "status": "failed", "retryable": True, "result": "No notification channel worked."}
            return {"success": True, "status": "succeeded", "result": f"Reminder delivered ({', '.join(delivered)})."}

        if task["type"] == "ai_task":
            return await self._run_ai(task, when, channels, note)
        return {"success": False, "status": "failed", "retryable": False, "result": f"Unsupported task type {task['type']}."}

    async def _run_ai(self, task: dict, when: str, channels: list[str], note: dict) -> dict:
        if not task["prompt"]:
            return {"success": False, "status": "failed", "retryable": False, "result": "This AI task has no prompt."}
        if not vault.get(vault.GROQ_KEY) and not llm.FAKE:
            return {"success": False, "status": "failed", "retryable": False, "result": "Add your Groq API key in Settings."}

        # One conversation per task, so every run's reply is in the chat history.
        cid = task["conversation_id"]
        if not cid or not self.store.get_conversation(cid):
            cid = self.store.create_conversation(f"⏰ {task['title']}"[:120])["id"]
            await self.pg.run("UPDATE scheduled_tasks SET payload = payload || %s WHERE id = %s",
                              (Jsonb({"conversation_id": cid}), _uuid(task["id"])))
        if any(r.conversation_id == cid for r in RUNS.values()):
            return {"success": False, "status": "failed", "retryable": True, "result": "That conversation is busy."}

        run = Run(cid, unattended=True, mode=task["permission_mode"])
        RUNS[run.id] = run
        reply, error = "", None
        try:
            async for ev in run_chat(self.store, self.hub, run, f"[Scheduled task · {when}] {task['prompt']}", set()):
                if ev["type"] == "error":
                    error = ev["message"]
                elif ev["type"] == "done":
                    reply = ev["message"]["content"]
        finally:
            RUNS.pop(run.id, None)

        if error:
            transient = any(w in error.lower() for w in ("limit", "can't reach", "server error", "try again"))
            return {"success": False, "status": "failed", "retryable": transient, "result": error, "conversation_id": cid}
        status = "approval_required" if run.approval_needed else "succeeded"
        title = f"{task['title']} · needs your approval" if run.approval_needed else task["title"]
        await self.notifier.send(Note(title, _plain(reply)[:200] or "Done.", "warn" if run.approval_needed else "info",
                                      conversation_id=cid, **note), channels)
        return {"success": True, "status": status, "result": reply[:RESULT_CHARS], "conversation_id": cid}


def _plain(markdown: str) -> str:
    return re.sub(r"[*_`#>\[\]]", "", re.sub(r"\s+", " ", markdown)).strip()


def describe(task: dict) -> str:
    """One line per task for the chat tools."""
    rule = {"once": "once", "interval": f"every {task.get('recurrence_rule')}", "cron": f"cron {task.get('recurrence_rule')}"}
    nxt = task.get("next_run_at")
    nxt_txt = local_time(datetime.fromisoformat(nxt), task["timezone"]) if nxt else "—"
    mode = f", {task.get('permission_mode', 'manual')} mode" if task["type"] == "ai_task" else ""
    return (f"- {task['title']} ({task['type']}{mode}, {rule.get(task['recurrence_type'], '')}, {task['status']}) · "
            f"next: {nxt_txt} ({task['timezone']}) · ID: {task['id']}")


service: TaskService | None = None  # set by the app's lifespan; used by tools/schedule_tools.py
