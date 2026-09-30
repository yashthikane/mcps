// Scheduler HTTP routes, called only by FastAPI (Bearer token). The React UI never talks to them.
import type { FastifyInstance } from "fastify";
import { within } from "../queue.js";
import { preview, ScheduleError } from "../recurrence.js";
import type { Scheduler } from "../scheduler.js";
import type { RecurrenceType } from "../types.js";

const REDIS_TIMEOUT_MS = 5_000;

interface PreviewBody {
  run_at?: string | null;
  timezone: string;
  recurrence_type: RecurrenceType;
  recurrence_rule?: string | null;
  count?: number;
}

export function taskRoutes(app: FastifyInstance, scheduler: Scheduler): void {
  app.post<{ Body: PreviewBody }>("/preview", async (req, reply) => {
    const b = req.body ?? ({} as PreviewBody);
    const runAt = b.run_at ? new Date(b.run_at) : null;
    if (runAt && Number.isNaN(runAt.getTime())) return reply.code(400).send({ error: "run_at isn't a valid timestamp." });
    try {
      const runs = preview({ run_at: runAt, timezone: b.timezone, recurrence_type: b.recurrence_type, recurrence_rule: b.recurrence_rule ?? null },
        new Date(), Math.min(Math.max(b.count ?? 3, 1), 10));
      return { runs: runs.map((d) => d.toISOString()) };
    } catch (e) {
      if (e instanceof ScheduleError) return reply.code(400).send({ error: e.message });
      throw e;
    }
  });

  app.post<{ Params: { id: string } }>("/tasks/:id/sync", async (req, reply) => {
    try {
      const task = await within(scheduler.sync(req.params.id), REDIS_TIMEOUT_MS, "Scheduling");
      if (!task) return reply.code(404).send({ error: "Task not found." });
      return { id: task.id, next_run_at: task.next_run_at, bullmq_job_id: task.bullmq_job_id };
    } catch (e) {
      if (e instanceof ScheduleError) return reply.code(400).send({ error: e.message });
      return reply.code(503).send({ error: (e as Error).message });
    }
  });

  app.post<{ Params: { id: string } }>("/tasks/:id/unschedule", async (req, reply) => {
    try {
      await within(scheduler.unschedule(req.params.id), REDIS_TIMEOUT_MS, "Unscheduling");
      return { ok: true };
    } catch (e) {
      return reply.code(503).send({ error: (e as Error).message });
    }
  });

  app.post<{ Params: { id: string } }>("/tasks/:id/run", async (req, reply) => {
    try {
      const jobId = await within(scheduler.runNow(req.params.id), REDIS_TIMEOUT_MS, "Queueing the run");
      if (!jobId) return reply.code(404).send({ error: "Task not found." });
      return { job_id: jobId };
    } catch (e) {
      return reply.code(503).send({ error: (e as Error).message });
    }
  });
}
