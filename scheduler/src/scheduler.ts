// Scheduling mechanics: keep exactly one delayed BullMQ job per active task, pointing at the task's
// next_run_at. PostgreSQL decides what should exist; Redis is rebuilt from it by reconcile().
import type { Job, Queue } from "bullmq";
import type { Db } from "./db.js";
import { firstOccurrence, nextOccurrence } from "./recurrence.js";
import type { JobData, Task } from "./types.js";

const FINISHED = new Set(["completed", "failed", "unknown"]);

/** Deterministic, so adding the same occurrence twice is a no-op in BullMQ. */
export const jobIdFor = (taskId: string, at: Date) => `task-${taskId}-${at.getTime()}`;

export class Scheduler {
  constructor(private db: Db, private queue: Queue<JobData>) {}

  /** Remove a job unless a worker is running it right now (the worker re-checks PostgreSQL anyway). */
  async removeJob(jobId: string | null): Promise<void> {
    if (!jobId) return;
    const job = await this.queue.getJob(jobId);
    if (!job) return;
    if ((await job.getState()) === "active") return;
    try {
      await job.remove();
    } catch {
      // it became active between the two calls; the worker will skip it as stale
    }
  }

  private async enqueue(taskId: string, at: Date, manual = false): Promise<Job<JobData>> {
    const jobId = manual ? `manual-${taskId}-${Date.now()}` : jobIdFor(taskId, at);
    return this.queue.add("run", { taskId, scheduledFor: at.toISOString(), manual }, {
      jobId,
      delay: Math.max(0, at.getTime() - Date.now()),
    });
  }

  /**
   * Make Redis match PostgreSQL for one task. A task that isn't `scheduled` loses its job; a
   * scheduled one without next_run_at gets its first occurrence computed. An existing next_run_at
   * is kept (that's how a snooze survives).
   */
  async sync(taskId: string): Promise<Task | null> {
    const task = await this.db.getTask(taskId);
    if (!task) return null;
    if (task.status === "running") return task; // the worker owns it until the run finishes
    if (task.status !== "scheduled") {
      await this.removeJob(task.bullmq_job_id);
      await this.db.setSchedule(task.id, null, null);
      return { ...task, next_run_at: null, bullmq_job_id: null };
    }
    const next = task.next_run_at ?? firstOccurrence(task, new Date());
    const jobId = jobIdFor(task.id, next);
    if (task.bullmq_job_id && task.bullmq_job_id !== jobId) await this.removeJob(task.bullmq_job_id);
    let existing = await this.queue.getJob(jobId);
    // A finished job with the same ID would make add() a no-op, so replace it. The worker's
    // idempotency check still stops the occurrence from running twice.
    if (existing && FINISHED.has(await existing.getState())) {
      await existing.remove();
      existing = undefined;
    }
    if (!existing) await this.enqueue(task.id, next);
    await this.db.setSchedule(task.id, next, jobId);
    return { ...task, next_run_at: next, bullmq_job_id: jobId };
  }

  async unschedule(taskId: string): Promise<void> {
    const task = await this.db.getTask(taskId);
    if (task) await this.removeJob(task.bullmq_job_id);
  }

  /** Manual runs go through the queue and worker like any other run; they don't move the schedule. */
  async runNow(taskId: string): Promise<string | null> {
    if (!(await this.db.getTask(taskId))) return null;
    const job = await this.enqueue(taskId, new Date(), true);
    return job.id!;
  }

  /**
   * After an occurrence has been handled: a one-time task is done; a recurring one moves to its
   * next occurrence. Skipped when the user changed the schedule meanwhile (snooze, edit, pause).
   */
  async advance(taskId: string, scheduledFor: Date, outcome: "ok" | "failed"): Promise<void> {
    const task = await this.db.getTask(taskId);
    if (!task) return;
    await this.db.markRan(task.id, scheduledFor);
    if (task.next_run_at && task.next_run_at.getTime() !== scheduledFor.getTime()) return;
    if (task.status !== "scheduled" && task.status !== "running") return;
    if (task.recurrence_type === "once") {
      await this.db.setStatus(task.id, outcome === "failed" ? "failed" : "completed");
      await this.db.setSchedule(task.id, null, null);
      return;
    }
    const after = new Date(Math.max(scheduledFor.getTime(), Date.now()));
    const next = nextOccurrence(task, after);
    if (task.status === "running") await this.db.setStatus(task.id, "scheduled");
    await this.db.setSchedule(task.id, next, null);
    await this.sync(task.id);
  }

  /**
   * Rebuild missing queue state from PostgreSQL: after a Redis restart, a crash between the
   * database write and queue.add, or a job that finished without advancing its task.
   */
  async reconcile(): Promise<{ checked: number; fixed: number }> {
    let fixed = 0;
    const tasks = await this.db.listActive();
    for (const task of tasks) {
      try {
        const job = task.bullmq_job_id ? await this.queue.getJob(task.bullmq_job_id) : null;
        const state = job ? await job.getState() : "missing";
        const healthy = job && !FINISHED.has(state)
          && (task.status === "running" || (task.next_run_at && task.bullmq_job_id === jobIdFor(task.id, task.next_run_at)));
        if (healthy) continue;
        if (task.status === "running") await this.db.setStatus(task.id, "scheduled"); // its run was lost
        await this.sync(task.id);
        fixed++;
      } catch (e) {
        console.error(`[reconcile] task ${task.id}: ${(e as Error).message}`);
      }
    }
    return { checked: tasks.length, fixed };
  }
}
