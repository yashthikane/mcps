# tools/schedule_tools.py — Scheduled tasks and reminders (PostgreSQL + the BullMQ scheduler)
#
# The tools call the app's TaskService (donna.tasks.service), which the FastAPI lifespan sets up,
# so creating a task from chat and from the Tasks page go through the same code.

from donna import tasks
from mcp_instance import mcp

NOT_READY = ("Error: scheduling isn't available. Donna's scheduler needs PostgreSQL and Redis: "
             "run scripts\\setup-scheduler.ps1 once, then start Donna with scripts\\start.ps1.")


async def _call(coro_fn):
    svc = tasks.service
    if svc is None:
        return NOT_READY
    try:
        return await coro_fn(svc)
    except (tasks.TaskError, ValueError) as e:
        return f"Error: {e}"
    except LookupError:
        return "Error: no scheduled task with that ID. Use list_scheduled_tasks to find it."
    except Exception as e:  # noqa: BLE001 - scheduler/PostgreSQL down
        return f"Error: scheduling isn't available right now ({str(e)[:200] or type(e).__name__})."


@mcp.tool()
async def schedule_task(title: str, when: str, kind: str = "reminder", prompt: str = "",
                        recurrence: str = "once", rule: str = "", permission_mode: str = "manual") -> str:
    """
    Schedule a reminder or an AI task.
    - title: short name, e.g. "Call Mom". For a reminder it is the notification text.
    - when: first run as LOCAL time without an offset, e.g. 2026-10-01T18:00, or relative: "in 30 minutes",
      "in 2 hours". For cron schedules it is the earliest start (use the current time for "from now on").
    - kind: "reminder" (just a notification, no AI) or "ai_task" (Donna runs `prompt` at that time,
      e.g. "Send the weekly report to ana@example.com").
    - permission_mode (ai_task only): "manual" (default: actions that send, change or delete are skipped and flagged),
      "auto" (the task may send/change/delete without asking; use it when the user says not to ask, and the user
      approves this once now) or "plan" (the task only writes a plan).
    - recurrence: "once", "interval" (rule like 30m, 2h, 1d) or "cron" (5-field rule in local time,
      e.g. "0 9 * * *" daily 09:00, "0 9 * * 1" Mondays 09:00, "30 18 * * 1-5" weekdays 18:30).
    Returns the task with its ID and next run.
    """
    async def go(svc):
        task = await svc.create({"title": title, "type": kind, "prompt": prompt, "message": "" if kind == "ai_task" else title,
                                 "run_at": when, "recurrence_type": recurrence, "recurrence_rule": rule or None,
                                 "permission_mode": permission_mode})
        note = f"\nNote: {task['warning']}" if task.get("warning") else ""
        return "Scheduled:\n" + tasks.describe(task) + note
    return await _call(go)


@mcp.tool()
async def list_scheduled_tasks(status: str = "") -> str:
    """
    List scheduled tasks and reminders with their IDs, schedule and next run (local time).
    status: optional filter: scheduled, paused, completed, cancelled or failed. Empty = all.
    """
    async def go(svc):
        items = await svc.list(status or None)
        if not items:
            return "No scheduled tasks."
        return "\n".join(tasks.describe(t) for t in items[:30])
    return await _call(go)


@mcp.tool()
async def cancel_scheduled_task(task_id: str) -> str:
    """Cancel a scheduled task or reminder so it never runs again. task_id comes from list_scheduled_tasks."""
    async def go(svc):
        task = await svc.cancel(task_id)
        return f"Cancelled: {task['title']}"
    return await _call(go)


@mcp.tool()
async def snooze_scheduled_task(task_id: str, minutes: int = 15) -> str:
    """
    Postpone a task's upcoming run by `minutes` (from its scheduled time, or from now if it already ran).
    For a recurring task only the next occurrence moves. task_id comes from list_scheduled_tasks.
    """
    async def go(svc):
        task = await svc.snooze(task_id, minutes)
        return "Snoozed:\n" + tasks.describe(task)
    return await _call(go)
