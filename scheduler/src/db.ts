// PostgreSQL access for the scheduler. PostgreSQL is the source of truth; the worker never acts on
// a job without reading the task first. Tables come from migrations/postgres (run by FastAPI).
import { randomUUID } from "node:crypto";
import pg from "pg";
import type { Execution, ExecutionStatus, Task, TaskStatus } from "./types.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TASK_COLUMNS =
  "id, title, type, status, run_at, timezone, recurrence_type, recurrence_rule, bullmq_job_id, last_run_at, next_run_at";

export const TERMINAL: ReadonlySet<ExecutionStatus> = new Set(["succeeded", "failed", "skipped", "missed", "approval_required"]);

export class Db {
  readonly pool: pg.Pool;

  constructor(url: string) {
    this.pool = new pg.Pool({ connectionString: url, max: 3, idleTimeoutMillis: 30_000, connectionTimeoutMillis: 5_000 });
    // An idle client losing its connection (e.g. PostgreSQL restarted) must not crash the process.
    this.pool.on("error", (e) => console.error(`[db] idle client error: ${e.message}`));
  }

  async ping(): Promise<boolean> {
    try {
      await this.pool.query("SELECT 1");
      return true;
    } catch {
      return false;
    }
  }

  async getTask(id: string): Promise<Task | null> {
    if (!UUID.test(id)) return null;
    const r = await this.pool.query<Task>(`SELECT ${TASK_COLUMNS} FROM scheduled_tasks WHERE id = $1`, [id]);
    return r.rows[0] ?? null;
  }

  /** Tasks the reconciler must keep queued. */
  async listActive(): Promise<Task[]> {
    const r = await this.pool.query<Task>(
      `SELECT ${TASK_COLUMNS} FROM scheduled_tasks WHERE status IN ('scheduled', 'running') ORDER BY next_run_at NULLS FIRST`);
    return r.rows;
  }

  async setSchedule(id: string, nextRunAt: Date | null, jobId: string | null): Promise<void> {
    await this.pool.query(
      "UPDATE scheduled_tasks SET next_run_at = $2, bullmq_job_id = $3, updated_at = NOW() WHERE id = $1",
      [id, nextRunAt, jobId]);
  }

  async setStatus(id: string, status: TaskStatus): Promise<void> {
    await this.pool.query("UPDATE scheduled_tasks SET status = $2, updated_at = NOW() WHERE id = $1", [id, status]);
  }

  async markRan(id: string, at: Date): Promise<void> {
    await this.pool.query(
      "UPDATE scheduled_tasks SET last_run_at = GREATEST(COALESCE(last_run_at, $2), $2), updated_at = NOW() WHERE id = $1",
      [id, at]);
  }

  /**
   * Find or create the execution row for one occurrence. (task_id, scheduled_for) is unique, so a
   * retried, stalled or re-queued job gets the same row back and can see that it already finished.
   */
  async claimExecution(taskId: string, scheduledFor: Date, trigger: "schedule" | "manual", jobId: string): Promise<Execution> {
    const r = await this.pool.query<Execution>(
      `INSERT INTO task_executions (id, task_id, scheduled_for, trigger, job_id, status, attempts)
       VALUES ($1, $2, $3, $4, $5, 'pending', 1)
       ON CONFLICT (task_id, scheduled_for)
       DO UPDATE SET attempts = task_executions.attempts + 1, job_id = EXCLUDED.job_id
       RETURNING id, task_id, scheduled_for, status, attempts`,
      [randomUUID(), taskId, scheduledFor, trigger, jobId]);
    return r.rows[0];
  }

  async startExecution(id: string): Promise<void> {
    await this.pool.query(
      "UPDATE task_executions SET status = 'running', started_at = NOW(), error = NULL WHERE id = $1", [id]);
  }

  async finishExecution(id: string, status: ExecutionStatus, result: string | null, error: string | null,
    conversationId: string | null = null): Promise<void> {
    const done = TERMINAL.has(status);
    await this.pool.query(
      `UPDATE task_executions SET status = $2, result = $3, error = $4,
         conversation_id = COALESCE($5, conversation_id),
         completed_at = CASE WHEN $6 THEN NOW() ELSE NULL END
       WHERE id = $1`,
      [id, status, result, error, conversationId, done]);
  }

  async close(): Promise<void> {
    await this.pool.end();
  }
}
