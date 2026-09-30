// The BullMQ worker: load the task, check it is still due, claim the occurrence, apply the catch-up
// policy, ask Donna to execute it, record the outcome and move the task to its next occurrence.
import { UnrecoverableError, Worker } from "bullmq";
import type { Redis } from "ioredis";
import { TERMINAL, type Db } from "./db.js";
import { ExecutorError, type Executor } from "./executor.js";
import { catchUp, type CatchUpLimits } from "./policy.js";
import { QUEUE_NAME } from "./queue.js";
import type { Scheduler } from "./scheduler.js";
import type { JobData, TaskStatus } from "./types.js";

const INACTIVE: ReadonlySet<TaskStatus> = new Set(["cancelled", "paused", "completed", "failed"]);

export interface Deps {
  db: Pick<Db, "getTask" | "claimExecution" | "startExecution" | "finishExecution" | "setStatus">;
  scheduler: Pick<Scheduler, "advance">;
  executor: Pick<Executor, "execute">;
  limits: CatchUpLimits;
  now?: () => Date;
}

export interface Attempt {
  jobId: string;
  attemptsMade: number; // failures so far
  attempts: number;     // allowed in total
}

/** Handles one job. Returns a short outcome; throws to make BullMQ retry (or UnrecoverableError to stop). */
export async function processJob(deps: Deps, data: JobData, attempt: Attempt): Promise<string> {
  const { db, scheduler, executor, limits } = deps;
  const now = deps.now ?? (() => new Date());
  const scheduledFor = new Date(data.scheduledFor);
  const manual = Boolean(data.manual);

  // 1-2. PostgreSQL decides whether this job is still wanted (cancel/snooze races land here).
  const task = await db.getTask(data.taskId);
  if (!task) return "skipped: task deleted";
  if (!manual) {
    if (INACTIVE.has(task.status)) return `skipped: task is ${task.status}`;
    if (!task.next_run_at || task.next_run_at.getTime() !== scheduledFor.getTime()) return "skipped: rescheduled";
  }

  // 3. One execution row per occurrence; a retried or recovered job finds it again.
  const exec = await db.claimExecution(task.id, scheduledFor, manual ? "manual" : "schedule", attempt.jobId);
  if (TERMINAL.has(exec.status)) {
    if (!manual) await scheduler.advance(task.id, scheduledFor, exec.status === "failed" ? "failed" : "ok");
    return `skipped: already ${exec.status}`;
  }

  // 4. Don't fire stale work after downtime.
  const decision = catchUp(task.type, scheduledFor, now(), limits, manual);
  if (decision !== "run") {
    let result = "Missed: Donna wasn't running at the scheduled time.";
    if (decision === "missed_notify") {
      try {
        result = (await executor.execute(task.id, exec.id, scheduledFor, "missed")).result;
      } catch {
        // the notice is best effort; the occurrence is recorded as missed either way
      }
    }
    await db.finishExecution(exec.id, "missed", result, null);
    await scheduler.advance(task.id, scheduledFor, "ok");
    return "missed";
  }

  // 5-8. Execute through Donna.
  await db.startExecution(exec.id);
  const once = task.recurrence_type === "once" && !manual;
  if (once) await db.setStatus(task.id, "running");
  try {
    const r = await executor.execute(task.id, exec.id, scheduledFor, "run");
    await db.finishExecution(exec.id, r.status, r.result, null, r.conversation_id ?? null);
    if (!manual) await scheduler.advance(task.id, scheduledFor, "ok");
    return r.status;
  } catch (e) {
    const message = (e as Error).message;
    const retryable = e instanceof ExecutorError ? e.retryable : true; // e.g. a dropped DB connection
    if (retryable && attempt.attemptsMade + 1 < attempt.attempts) {
      await db.finishExecution(exec.id, "pending", null, message);
      if (once) await db.setStatus(task.id, "scheduled");
      throw e; // BullMQ retries with exponential backoff
    }
    await db.finishExecution(exec.id, "failed", null, message);
    if (!manual) await scheduler.advance(task.id, scheduledFor, "failed"); // a series keeps going
    throw new UnrecoverableError(message);
  }
}

export function startWorker(deps: Deps, connection: Redis): Worker<JobData> {
  // concurrency 1: an 8 GB laptop and Groq's free tier (30 req/min) both favour one task at a time.
  const worker = new Worker<JobData>(QUEUE_NAME, async (job) => processJob(deps, job.data, {
    jobId: job.id!,
    attemptsMade: job.attemptsMade,
    attempts: job.opts.attempts ?? 1,
  }), { connection, concurrency: 1 });
  worker.on("completed", (job, outcome) => console.log(`[worker] task ${job.data.taskId}: ${outcome}`));
  worker.on("failed", (job, e) => console.warn(`[worker] task ${job?.data.taskId}: failed (${e.message})`));
  worker.on("error", (e) => console.error(`[worker] ${e.message}`));
  return worker;
}
