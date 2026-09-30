// Shapes shared by the scheduler modules. They mirror migrations/postgres/*.sql.

export type TaskType = "reminder" | "ai_task";
export type TaskStatus = "scheduled" | "running" | "completed" | "cancelled" | "failed" | "paused";
export type RecurrenceType = "once" | "interval" | "cron";
export type ExecutionStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "missed" | "approval_required";

export interface Task {
  id: string;
  title: string;
  type: TaskType;
  status: TaskStatus;
  run_at: Date | null;
  timezone: string;
  recurrence_type: RecurrenceType;
  recurrence_rule: string | null;
  bullmq_job_id: string | null;
  last_run_at: Date | null;
  next_run_at: Date | null;
}

/** What the schedule of a task depends on (also the body of POST /preview). */
export type Schedule = Pick<Task, "run_at" | "timezone" | "recurrence_type" | "recurrence_rule">;

export interface Execution {
  id: string;
  task_id: string;
  scheduled_for: Date;
  status: ExecutionStatus;
  attempts: number;
}

/** BullMQ job data: kept small; the worker loads the task from PostgreSQL. */
export interface JobData {
  taskId: string;
  scheduledFor: string; // ISO timestamp of the occurrence (idempotency key with taskId)
  manual?: boolean;
}

/** Response of FastAPI's internal execute endpoint. */
export interface ExecuteResult {
  success: boolean;
  status: ExecutionStatus;
  result: string;
  retryable?: boolean;
  conversation_id?: string | null;
}

export type CatchUp = "run" | "missed_notify" | "missed_silent";
