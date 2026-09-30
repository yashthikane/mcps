// Worker decision rules with in-memory fakes (no Redis or PostgreSQL needed).
import assert from "node:assert/strict";
import { test } from "node:test";
import { UnrecoverableError } from "bullmq";
import { ExecutorError } from "./executor.js";
import { processJob, type Deps } from "./worker.js";
import type { Execution, ExecutionStatus, ExecuteResult, Task } from "./types.js";

const AT = new Date("2026-10-01T09:00:00Z");

function setup(taskPatch: Partial<Task> = {}, execStatus: ExecutionStatus = "pending", execute?: () => Promise<ExecuteResult>) {
  const task: Task = {
    id: "t1", title: "Stretch", type: "reminder", status: "scheduled", run_at: AT, timezone: "UTC",
    recurrence_type: "once", recurrence_rule: null, bullmq_job_id: null, last_run_at: null, next_run_at: AT, ...taskPatch,
  };
  const log: string[] = [];
  const deps: Deps = {
    db: {
      getTask: async (id) => (id === task.id ? task : null),
      claimExecution: async (): Promise<Execution> => ({ id: "e1", task_id: task.id, scheduled_for: AT, status: execStatus, attempts: 1 }),
      startExecution: async () => void log.push("start"),
      finishExecution: async (_id, status) => void log.push(`finish:${status}`),
      setStatus: async (_id, status) => void log.push(`status:${status}`),
    },
    scheduler: { advance: async (_id, _at, outcome) => void log.push(`advance:${outcome}`) },
    executor: {
      execute: async (_t, _e, _at, mode) => {
        log.push(`execute:${mode}`);
        return execute ? execute() : { success: true, status: "succeeded", result: "Reminder sent." };
      },
    },
    limits: { reminderGraceMin: 5, reminderMissedNotifyH: 12, aiCatchupMin: 60 },
    now: () => new Date(AT.getTime() + 60_000),
  };
  return { deps, log, data: { taskId: task.id, scheduledFor: AT.toISOString() } };
}
const first = { jobId: "j", attemptsMade: 0, attempts: 3 };

test("runs a due task, records it and advances", async () => {
  const { deps, log, data } = setup();
  assert.equal(await processJob(deps, data, first), "succeeded");
  assert.deepEqual(log, ["start", "status:running", "execute:run", "finish:succeeded", "advance:ok"]);
});

test("skips cancelled, paused and rescheduled tasks without executing", async () => {
  for (const patch of [{ status: "cancelled" as const }, { status: "paused" as const }, { next_run_at: new Date(AT.getTime() + 1_800_000) }]) {
    const { deps, log, data } = setup(patch);
    assert.match(await processJob(deps, data, first), /^skipped/);
    assert.deepEqual(log, []);
  }
});

test("a deleted task is skipped", async () => {
  const { deps, data } = setup();
  assert.equal(await processJob(deps, { ...data, taskId: "gone" }, first), "skipped: task deleted");
});

test("an occurrence that already finished is not executed again", async () => {
  const { deps, log, data } = setup({}, "succeeded");
  assert.match(await processJob(deps, data, first), /already succeeded/);
  assert.deepEqual(log, ["advance:ok"]);
});

test("late reminders become a missed notice; very late ones are silent", async () => {
  const late = setup();
  late.deps.now = () => new Date(AT.getTime() + 60 * 60_000);
  assert.equal(await processJob(late.deps, late.data, first), "missed");
  assert.deepEqual(late.log, ["execute:missed", "finish:missed", "advance:ok"]);

  const old = setup();
  old.deps.now = () => new Date(AT.getTime() + 24 * 3_600_000);
  assert.equal(await processJob(old.deps, old.data, first), "missed");
  assert.deepEqual(old.log, ["finish:missed", "advance:ok"]);
});

test("manual runs ignore status and lateness and don't move the schedule", async () => {
  const { deps, log, data } = setup({ status: "completed", next_run_at: null });
  deps.now = () => new Date(AT.getTime() + 24 * 3_600_000);
  assert.equal(await processJob(deps, { ...data, manual: true }, first), "succeeded");
  assert.deepEqual(log, ["start", "execute:run", "finish:succeeded"]);
});

test("retryable failures rethrow for BullMQ; the last attempt fails for good", async () => {
  const boom = async (): Promise<ExecuteResult> => { throw new ExecutorError("Donna's API isn't reachable.", true); };
  const a = setup({}, "pending", boom);
  await assert.rejects(processJob(a.deps, a.data, first), ExecutorError);
  assert.deepEqual(a.log, ["start", "status:running", "execute:run", "finish:pending", "status:scheduled"]);

  const b = setup({}, "pending", boom);
  await assert.rejects(processJob(b.deps, b.data, { ...first, attemptsMade: 2 }), UnrecoverableError);
  assert.deepEqual(b.log.slice(-2), ["finish:failed", "advance:failed"]);
});

test("permanent failures are not retried", async () => {
  const bad = async (): Promise<ExecuteResult> => { throw new ExecutorError("Unsupported task type.", false); };
  const { deps, log, data } = setup({ recurrence_type: "cron", recurrence_rule: "0 9 * * *" }, "pending", bad);
  await assert.rejects(processJob(deps, data, first), UnrecoverableError);
  assert.deepEqual(log, ["start", "execute:run", "finish:failed", "advance:failed"]);
});
