-- In-app notification channel. The UI polls unread rows and marks them read.
CREATE TABLE notifications (
    id UUID PRIMARY KEY,
    task_id UUID REFERENCES scheduled_tasks(id) ON DELETE SET NULL,
    execution_id UUID REFERENCES task_executions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warn', 'error')),
    conversation_id TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_notifications_unread ON notifications(created_at) WHERE read_at IS NULL;
