-- Scheduled tasks: the durable source of truth for what is scheduled.
-- next_run_at and bullmq_job_id are written by the scheduler service (recurrence math lives there).
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    title TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL CHECK (type IN ('reminder', 'ai_task')),
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'running', 'completed', 'cancelled', 'failed', 'paused')),
    run_at TIMESTAMPTZ,
    timezone TEXT NOT NULL,
    recurrence_type TEXT NOT NULL DEFAULT 'once' CHECK (recurrence_type IN ('once', 'interval', 'cron')),
    recurrence_rule TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    notification_channels JSONB NOT NULL DEFAULT '["windows", "in_app"]'::jsonb,
    bullmq_job_id TEXT,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scheduled_tasks_user_id ON scheduled_tasks(user_id);
CREATE INDEX idx_scheduled_tasks_status ON scheduled_tasks(status);
CREATE INDEX idx_scheduled_tasks_next_run_at ON scheduled_tasks(next_run_at);
CREATE INDEX idx_scheduled_tasks_status_next_run ON scheduled_tasks(status, next_run_at);
