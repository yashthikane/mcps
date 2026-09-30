"""Scheduled tasks on the FastAPI side: parsing, validation, the internal API guard, and execution
(reminders never call Groq; AI tasks run unattended). PostgreSQL-backed CRUD runs only when
DONNA_TEST_POSTGRES_URL points at a database the tests may create a schema in."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from donna import agent, llm, tasks
from donna.notify import Note
from donna.scheduler_client import ScheduleInvalid


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", True)


@pytest.fixture
async def hub(store):
    import server  # noqa: F401 - registers tools
    from donna.hub import MCPHub
    from mcp_instance import mcp
    h = MCPHub(store, mcp)
    await h.start()
    yield h
    await h.stop()


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[Note, list]] = []

    async def send(self, note, channels=None):
        self.sent.append((note, channels))
        return list(channels or ["in_app"])


class FakeScheduler:
    def __init__(self):
        self.synced, self.unscheduled, self.ran = [], [], []

    async def preview(self, schedule, count=3):
        if schedule["recurrence_type"] == "cron" and len((schedule["recurrence_rule"] or "").split()) != 5:
            raise ScheduleInvalid("Cron rules need 5 fields.")
        return [schedule["run_at"] or "2026-10-01T03:30:00+00:00"]

    async def sync(self, task_id):
        self.synced.append(task_id)
        return {}

    async def unschedule(self, task_id):
        self.unscheduled.append(task_id)

    async def run(self, task_id):
        self.ran.append(task_id)
        return "manual-job"


class RowPg:
    """Enough of donna.pg.Postgres for execute(): a fixed task row, writes recorded."""
    def __init__(self, row):
        self.row, self.writes = row, []

    async def ensure(self):
        pass

    async def one(self, sql, params=None):
        return self.row

    async def run(self, sql, params=None):
        self.writes.append((sql, params))
        return 1


def row(**kw):
    base = {"id": uuid.uuid4(), "title": "Stretch", "description": None, "type": "reminder", "status": "scheduled",
            "run_at": datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc), "timezone": "Asia/Kolkata",
            "recurrence_type": "once", "recurrence_rule": None, "payload": {"message": "Stand up and stretch"},
            "notification_channels": ["windows", "in_app"], "bullmq_job_id": None, "last_run_at": None,
            "next_run_at": None, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
    return {**base, **kw}


def service(store, pg=None, hub=None, notifier=None, scheduler=None):
    return tasks.TaskService(pg or RowPg(row()), scheduler or FakeScheduler(), notifier or FakeNotifier(), store, hub)


# ------------------------------------------------------------------------------ parsing / validation
def test_parse_when_uses_the_task_timezone_not_utc():
    ist = tasks.parse_when("2026-10-01T09:00", "Asia/Kolkata")
    assert ist.isoformat() == "2026-10-01T09:00:00+05:30"
    assert tasks.parse_when("2026-10-01T09:00:00Z", "Asia/Kolkata").utcoffset() == timedelta(0)  # explicit offset kept
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert tasks.parse_when("in 30 minutes", "UTC", now) == now + timedelta(minutes=30)
    assert tasks.parse_when("in 2 hours", "UTC", now) == now + timedelta(hours=2)
    with pytest.raises(tasks.TaskError):
        tasks.parse_when("tomorrow-ish", "UTC")
    with pytest.raises(tasks.TaskError):
        tasks.parse_when("2026-10-01T09:00", "Mars/Olympus")


def test_normalise_validates_input(store):
    svc = service(store)
    store.set_settings(timezone="Europe/Berlin")
    v = svc._normalise({"title": " Call Mom ", "run_at": "2026-10-01T18:00"})
    assert v["title"] == "Call Mom" and v["timezone"] == "Europe/Berlin" and v["type"] == "reminder"
    assert v["run_at"].isoformat() == "2026-10-01T18:00:00+02:00"
    assert v["notification_channels"] == ["windows", "in_app"]
    for bad, msg in [({"title": ""}, "title"), ({"title": "x", "type": "shell"}, "Type"),
                     ({"title": "x", "run_at": "2026-10-01T09:00", "recurrence_type": "interval"}, "rule"),
                     ({"title": "x", "type": "ai_task", "run_at": "2026-10-01T09:00"}, "prompt"),
                     ({"title": "x", "run_at": "2026-10-01T09:00", "notification_channels": ["sms"]}, "channels"),
                     ({"title": "x"}, "when")]:
        with pytest.raises(tasks.TaskError, match=msg):
            svc._normalise(bad)
    # a patch keeps the fields it doesn't mention
    cur = row()
    patched = svc._normalise({"title": "Stretch more"}, cur)
    assert patched["run_at"] == cur["run_at"] and patched["payload"]["message"] == "Stand up and stretch"


async def test_create_rejects_past_one_time_tasks(store):
    svc = service(store)
    with pytest.raises(tasks.TaskError, match="already passed"):
        await svc.create({"title": "Late", "run_at": "2020-01-01T09:00", "timezone": "UTC"})


# ------------------------------------------------------------------------------ execution
async def test_reminder_notifies_without_calling_groq(store, monkeypatch):
    async def no_llm(*a, **k):
        raise AssertionError("reminders must not call the model")
        yield  # pragma: no cover
    monkeypatch.setattr(llm, "stream_turn", no_llm)
    notifier = FakeNotifier()
    r = row()
    svc = service(store, pg=RowPg(r), notifier=notifier)
    out = await svc.execute(str(r["id"]), str(uuid.uuid4()), r["run_at"], "run")
    assert out["success"] and out["status"] == "succeeded"
    note, channels = notifier.sent[0]
    assert note.title == "Stretch" and note.body == "Stand up and stretch" and channels == ["windows", "in_app"]

    out = await svc.execute(str(r["id"]), str(uuid.uuid4()), r["run_at"], "missed")
    assert out["status"] == "missed" and notifier.sent[1][0].title.startswith("Missed reminder")
    assert "18:00" in notifier.sent[1][0].body  # 12:30 UTC shown in the task's timezone (IST)


async def test_ai_task_runs_unattended_and_never_executes_approval_tools(store, hub, fake_llm):
    notifier = FakeNotifier()
    r = row(type="ai_task", title="Prep block", payload={"prompt": "create event for prep"})
    pg = RowPg(r)
    svc = service(store, pg=pg, hub=hub, notifier=notifier)
    out = await svc.execute(str(r["id"]), str(uuid.uuid4()), datetime.now(timezone.utc), "run")
    assert out["success"] and out["status"] == "approval_required"
    cid = out["conversation_id"]
    assert store.get_conversation(cid)["title"] == "⏰ Prep block"
    assert any("conversation_id" in str(p) for _, p in pg.writes)  # remembered on the task
    ev = store.messages(cid)[-1]["tool_events"][0]
    assert ev["name"] == "create_event" and ev["status"] == "rejected"
    assert notifier.sent[0][0].level == "warn" and notifier.sent[0][0].conversation_id == cid
    assert not agent.RUNS  # the run was unregistered


async def test_auto_mode_ai_task_acts_without_approval(store, hub, fake_llm):
    r = row(type="ai_task", title="Prep block", payload={"prompt": "create event for prep", "permission_mode": "auto"})
    out = await service(store, pg=RowPg(r), hub=hub).execute(str(r["id"]), str(uuid.uuid4()), datetime.now(timezone.utc))
    assert out["success"] and out["status"] == "succeeded"
    ev = store.messages(out["conversation_id"])[-1]["tool_events"][0]
    assert ev["name"] == "create_event" and ev["approved"] == "auto" and ev["status"] != "rejected"


def test_permission_mode_is_validated(store):
    svc = service(store)
    v = svc._normalise({"title": "x", "type": "ai_task", "prompt": "p", "run_at": "2026-10-01T09:00", "permission_mode": "AUTO"})
    assert v["payload"]["permission_mode"] == "auto"
    with pytest.raises(tasks.TaskError, match="Permission mode"):
        svc._normalise({"title": "x", "run_at": "2026-10-01T09:00", "permission_mode": "yolo"})


async def test_ai_task_reports_a_busy_conversation_as_retryable(store, hub, fake_llm):
    conv = store.create_conversation("⏰ Busy")
    r = row(type="ai_task", payload={"prompt": "hi", "conversation_id": conv["id"]})
    other = agent.Run(conv["id"])
    agent.RUNS[other.id] = other
    try:
        out = await service(store, pg=RowPg(r), hub=hub).execute(str(r["id"]), "e", datetime.now(timezone.utc))
    finally:
        agent.RUNS.pop(other.id)
    assert not out["success"] and out["retryable"]


async def test_unattended_runs_get_no_scheduling_tools(store, hub, fake_llm, monkeypatch):
    seen = {}

    async def capture(api_key, model, messages, tools, effort="medium"):
        seen["tools"] = {t["function"]["name"] for t in tools}
        yield "delta", "ok"
    monkeypatch.setattr(llm, "stream_turn", capture)
    conv = store.create_conversation()
    async for _ in agent.run_chat(store, hub, agent.Run(conv["id"], unattended=True), "remind me to schedule a task", set()):
        pass
    assert not seen["tools"] & {"schedule_task", "cancel_scheduled_task"}
    async for _ in agent.run_chat(store, hub, agent.Run(conv["id"]), "remind me to schedule a task", set()):
        pass
    assert "schedule_task" in seen["tools"]


# ------------------------------------------------------------------------------ HTTP
def test_internal_api_requires_the_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from donna.app import app
    monkeypatch.setenv("DONNA_INTERNAL_API_TOKEN", "secret-token-for-tests")
    monkeypatch.delenv("DONNA_POSTGRES_URL", raising=False)
    app.state.db_path = tmp_path / "t.db"
    body = {"execution_id": str(uuid.uuid4()), "scheduled_for": "2026-10-01T09:00:00Z"}
    url = f"/api/v1/internal/scheduled-tasks/{uuid.uuid4()}/execute"
    with TestClient(app) as c:
        assert c.post(url, json=body).status_code == 401
        assert c.post(url, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
        # right token, but PostgreSQL isn't configured in tests → 503 (the scheduler will retry)
        assert c.post(url, json=body, headers={"Authorization": "Bearer secret-token-for-tests"}).status_code == 503
        # the public task API degrades the same way instead of crashing
        assert c.get("/api/v1/scheduled-tasks").status_code == 503
        assert c.get("/api/v1/notifications").json() == []
        # browsers from other sites are still blocked by the Origin check
        assert c.post(url, json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    app.state.db_path = None


# ------------------------------------------------------------------------------ PostgreSQL (optional)
PG_URL = os.getenv("DONNA_TEST_POSTGRES_URL")


@pytest.fixture
async def pg_db():
    if not PG_URL:
        pytest.skip("set DONNA_TEST_POSTGRES_URL to run the PostgreSQL tests")
    import psycopg
    from donna.pg import Postgres
    schema = "donna_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in PG_URL else "?"
    db = Postgres(f"{PG_URL}{sep}options=-csearch_path%3D{schema}")
    assert await db.open()
    assert await db.migrate() == ["001_scheduled_tasks", "002_task_executions", "003_notifications"]
    assert await db.migrate() == []  # idempotent
    yield db
    await db.close()
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


async def test_postgres_crud_snooze_cancel(store, pg_db):
    from donna.notify import Notifier
    sched = FakeScheduler()
    notifier = Notifier(pg_db)
    notifier.channels.pop("windows")
    svc = tasks.TaskService(pg_db, sched, notifier, store, None)

    when = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    t = await svc.create({"title": "Call Mom", "run_at": when.isoformat(), "timezone": "Asia/Kolkata"})
    assert t["status"] == "scheduled" and sched.synced == [t["id"]]
    assert [x["id"] for x in await svc.list()] == [t["id"]]

    await pg_db.run("UPDATE scheduled_tasks SET next_run_at = run_at WHERE id = %s", (uuid.UUID(t["id"]),))
    s = await svc.snooze(t["id"], 30)
    assert datetime.fromisoformat(s["next_run_at"]) == when + timedelta(minutes=30)
    assert datetime.fromisoformat(s["run_at"]) == when + timedelta(minutes=30)  # a one-time task moves

    c = await svc.cancel(t["id"])
    assert c["status"] == "cancelled" and c["next_run_at"] is None

    with pytest.raises(ValueError):  # TaskError or the scheduler's ScheduleInvalid
        await svc.create({"title": "Bad", "run_at": when.isoformat(), "recurrence_type": "cron", "recurrence_rule": "0 9"})

    out = await svc.execute(t["id"], str(uuid.uuid4()), when, "run")
    assert out["success"]
    assert [n["title"] for n in await notifier.unread()] == ["Call Mom"]
    assert await notifier.mark_read() == 1 and await notifier.unread() == []

    await svc.delete(t["id"])
    with pytest.raises(LookupError):
        await svc.get(t["id"])
