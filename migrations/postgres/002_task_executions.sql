-- One row per logical occurrence. UNIQUE (task_id, scheduled_for) is the idempotency key:
-- a retried or recovered job finds the existing row instead of running the occurrence twice.
CREATE TABLE task_executions (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'schedule' CHECK (trigger IN ('schedule', 'manual')),
    job_id TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'missed', 'approval_required')),
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    conversation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_executions_occurrence UNIQUE (task_id, scheduled_for)
);

CREATE INDEX idx_task_executions_task_id ON task_executions(task_id);
CREATE INDEX idx_task_executions_status ON task_executions(status);
CREATE INDEX idx_task_executions_scheduled_for ON task_executions(scheduled_for);
