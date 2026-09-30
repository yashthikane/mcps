# AI Assistant Scheduled Tasks — Complete Implementation Specification

## 1. Goal

Implement a reliable scheduling system for the AI assistant.

The assistant must understand requests such as:

- "Remind me tomorrow at 8 AM to work on my GitHub bot."
- "Remind me in 30 minutes."
- "Every Monday at 9 AM remind me to check my PRs."
- "Cancel my GitHub reminder."
- "What do I have scheduled today?"
- "Snooze that reminder for 30 minutes."

The system must persist scheduled tasks, execute them at the correct time, and send notifications.

### Core principle

The AI must NOT stay alive waiting for a scheduled time.

Instead:

```text
User
  ↓
AI understands request
  ↓
Create scheduled task
  ↓
PostgreSQL stores task
  ↓
BullMQ schedules execution
  ↓
Redis manages queue state
  ↓
Worker executes when due
  ↓
Notification / AI action
  ↓
Execution recorded in PostgreSQL
```

---

# 2. Recommended Technology

Use:

- **Node.js + TypeScript** for backend
- **PostgreSQL** for persistent application data
- **Redis** for BullMQ
- **BullMQ** for delayed/recurring jobs and job execution
- Existing **Groq LLM** for natural-language understanding and AI tasks
- Existing notification system if one already exists
- Otherwise start with a simple notification adapter and add Web Push/ntfy/email later

Do not replace an existing project technology unnecessarily. Integrate this feature into the current architecture.

---

# 3. Responsibilities

Keep the responsibilities separate.

### PostgreSQL

Stores the permanent source of truth:

- scheduled tasks
- task status
- recurrence information
- timezone
- notification preferences
- execution history

### BullMQ / Redis

Handles execution scheduling:

- delayed jobs
- recurring jobs
- retries
- queue state
- worker coordination

### Worker

Actually executes a due job.

### AI

Understands natural language and performs intelligent work when required.

### Notification service

Delivers notifications.

---

# 4. Database Design

Create a table named:

```text
scheduled_tasks
```

Suggested schema:

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL,

    title TEXT NOT NULL,
    description TEXT,

    type TEXT NOT NULL DEFAULT 'reminder',
    status TEXT NOT NULL DEFAULT 'scheduled',

    run_at TIMESTAMPTZ,

    timezone TEXT NOT NULL DEFAULT 'UTC',

    recurrence_type TEXT,
    recurrence_rule TEXT,

    payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    notification_channels JSONB NOT NULL DEFAULT '["push"]'::jsonb,

    bullmq_job_id TEXT,

    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Recommended status values:

```text
scheduled
running
completed
cancelled
failed
paused
```

Recommended task types:

```text
reminder
ai_task
calendar_event
conditional_task
```

Recommended recurrence types:

```text
once
interval
cron
```

---

# 5. Execution History

Create:

```text
task_executions
```

Schema:

```sql
CREATE TABLE task_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,

    scheduled_for TIMESTAMPTZ NOT NULL,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    status TEXT NOT NULL,

    attempts INTEGER NOT NULL DEFAULT 0,

    result JSONB,
    error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Status:

```text
pending
running
success
failed
```

This allows the system to know what happened historically.

---

# 6. Important Database Indexes

Add indexes for common queries:

```sql
CREATE INDEX idx_scheduled_tasks_user_id
ON scheduled_tasks(user_id);

CREATE INDEX idx_scheduled_tasks_status
ON scheduled_tasks(status);

CREATE INDEX idx_scheduled_tasks_next_run_at
ON scheduled_tasks(next_run_at);

CREATE INDEX idx_task_executions_task_id
ON task_executions(task_id);
```

---

# 7. Scheduling Architecture

Use a BullMQ queue.

Suggested structure:

```text
src/
  scheduler/
    queues/
      scheduledTaskQueue.ts

    workers/
      scheduledTaskWorker.ts

    services/
      scheduledTaskService.ts

    tools/
      createScheduledTask.ts
      listScheduledTasks.ts
      cancelScheduledTask.ts
      updateScheduledTask.ts
      snoozeScheduledTask.ts

    notifications/
      notificationService.ts

    types/
      scheduledTask.ts
```

Adapt the structure to the existing project instead of creating unnecessary duplicate architecture.

---

# 8. BullMQ Queue

Create a queue:

```text
scheduled-tasks
```

Example:

```ts
import { Queue } from "bullmq";

export const scheduledTaskQueue = new Queue("scheduled-tasks", {
  connection: redisConnection
});
```

The Redis connection should come from the project's existing configuration if one already exists.

Do not hardcode credentials.

Use environment variables.

Example:

```env
REDIS_URL=redis://localhost:6379
```

---

# 9. Creating a One-Time Scheduled Task

When the AI decides to create a reminder:

1. Validate the request.
2. Convert the requested time to an absolute timestamp.
3. Store the task in PostgreSQL.
4. Calculate the delay.
5. Add a delayed BullMQ job.
6. Store the BullMQ job ID in PostgreSQL.
7. Return the task to the AI.

Conceptually:

```ts
const task = await db.scheduledTasks.create({
  userId,
  title,
  type: "reminder",
  status: "scheduled",
  runAt,
  nextRunAt: runAt,
  timezone,
  payload
});

const job = await scheduledTaskQueue.add(
  "execute-scheduled-task",
  {
    taskId: task.id
  },
  {
    delay: Math.max(0, runAt.getTime() - Date.now()),
    jobId: `scheduled-task:${task.id}`
  }
);

await db.scheduledTasks.update(task.id, {
  bullmqJobId: job.id
});
```

Do NOT put the entire task as the source of truth inside Redis.

PostgreSQL remains the source of truth.

The BullMQ job should primarily reference the task ID.

---

# 10. Worker

Create a BullMQ worker.

Conceptual implementation:

```ts
const worker = new Worker(
  "scheduled-tasks",
  async (job) => {
    const { taskId } = job.data;

    await executeScheduledTask(taskId);
  },
  {
    connection: redisConnection,
    concurrency: 5
  }
);
```

The worker must:

1. Load the task from PostgreSQL.
2. Verify that it still exists.
3. Verify it is not cancelled.
4. Create an execution record.
5. Mark task as running.
6. Execute the task.
7. Send notification or perform AI action.
8. Record success/failure.
9. Schedule the next occurrence if recurring.
10. Update task status.

---

# 11. Worker Execution Logic

Use approximately:

```text
job starts
   ↓
load task
   ↓
task exists?
   ├── NO → stop
   │
   └── YES
        ↓
cancelled?
   ├── YES → stop
   │
   └── NO
        ↓
create task_execution
        ↓
status = running
        ↓
execute task
        ↓
success?
   ├── YES → record success
   │
   └── NO → record failure
        ↓
if recurring:
    calculate next occurrence
    schedule next occurrence
```

---

# 12. Recurring Tasks

Support recurring tasks after one-time reminders work.

Examples:

```text
Every day at 8 AM
Every Monday at 9 AM
Every weekday at 6 PM
Every 3 hours
```

Use BullMQ Job Schedulers / recurring job functionality rather than creating an uncontrolled custom polling loop.

The recurrence rule should also be stored in PostgreSQL so the application knows what the task means.

Example:

```text
recurrence_type = cron
recurrence_rule = "0 9 * * 1"
timezone = "Asia/Kolkata"
```

For interval scheduling:

```text
recurrence_type = interval
recurrence_rule = "10800"
```

The exact representation may be adjusted to the selected implementation.

---

# 13. Timezones

Timezone handling is mandatory.

Never assume UTC when the user says:

> "8 AM"

Store the user's timezone.

Example:

```text
timezone = Asia/Kolkata
```

Use a proper timezone library such as Luxon or the project's existing date/time library.

Convert natural-language time into an absolute timestamp while retaining the timezone.

For example:

```text
User:
"Tomorrow at 8 AM"

Timezone:
Asia/Kolkata

↓

2026-10-01 08:00 Asia/Kolkata
```

Do not manually add/subtract hours.

---

# 14. AI Tool Interface

The LLM should not directly access PostgreSQL or Redis.

Expose controlled application tools.

Minimum tools:

```text
create_scheduled_task
list_scheduled_tasks
get_scheduled_task
cancel_scheduled_task
update_scheduled_task
snooze_scheduled_task
```

---

# 15. create_scheduled_task Tool

Tool input:

```json
{
  "title": "Work on GitHub bot",
  "description": "Continue implementing the PR automation",
  "schedule": {
    "type": "once",
    "datetime": "2026-10-01T08:00:00",
    "timezone": "Asia/Kolkata"
  },
  "notification": {
    "channels": ["push"]
  }
}
```

The application validates this input and creates the database + BullMQ job.

The LLM should not generate SQL.

---

# 16. Example AI Interaction

User:

```text
Remind me tomorrow at 8 AM to work on my GitHub bot.
```

LLM understands:

```text
action = create_scheduled_task

title = Work on my GitHub bot

time = tomorrow 8 AM

schedule = once
```

The application calls:

```text
create_scheduled_task()
```

The user should receive:

```text
Done. I'll remind you tomorrow at 8:00 AM.
```

---

# 17. Listing Tasks

User:

```text
What do I have scheduled today?
```

AI calls:

```text
list_scheduled_tasks()
```

The backend queries:

```sql
SELECT *
FROM scheduled_tasks
WHERE user_id = ?
  AND status = 'scheduled'
ORDER BY next_run_at ASC;
```

The AI formats the result naturally.

Example:

```text
You have 3 things scheduled today:

8:00 AM — Work on GitHub bot
1:00 PM — Team meeting
8:00 PM — Review today's tasks
```

---

# 18. Cancelling Tasks

User:

```text
Cancel my GitHub reminder.
```

AI:

```text
list_scheduled_tasks()
```

Finds the relevant task.

Then:

```text
cancel_scheduled_task(taskId)
```

Backend:

1. Update PostgreSQL status to `cancelled`.
2. Remove/cancel the corresponding BullMQ job where applicable.
3. Return confirmation.

Important: cancellation must be checked again by the worker in case a job is already in the queue.

---

# 19. Snoozing

User:

```text
Remind me again in 30 minutes.
```

Backend:

1. Find the task.
2. Calculate new execution time.
3. Cancel/replace the old scheduled job.
4. Update `run_at` / `next_run_at`.
5. Create a new delayed BullMQ job.

Example:

```text
Current reminder
     ↓
Snooze 30 minutes
     ↓
New run_at = current time + 30 minutes
```

---

# 20. Notification Service

Create a separate abstraction:

```ts
interface NotificationService {
  send(notification: Notification): Promise<void>;
}
```

Do not make the scheduler depend directly on one notification provider.

Example:

```text
NotificationService
       │
       ├── WebPush
       ├── Ntfy
       ├── Email
       └── SMS
```

Start with whichever notification mechanism the project already has.

If none exists, implement one simple provider first.

---

# 21. Simple Reminder vs AI Task

Not every scheduled task needs an LLM call.

### Simple reminder

```text
8 AM
 ↓
Worker
 ↓
sendNotification("Work on GitHub bot")
```

### AI task

Example:

```text
Every morning at 9 AM:
"Check my GitHub activity and summarize it."
```

Execution:

```text
9 AM
 ↓
Worker
 ↓
GitHub tool
 ↓
Get activity
 ↓
Groq
 ↓
Generate summary
 ↓
Notification
```

This distinction reduces unnecessary LLM usage and cost.

---

# 22. AI Task Payload

For AI tasks, use `payload`.

Example:

```json
{
  "action": "github_daily_summary",
  "instructions": "Summarize important GitHub activity from the last 24 hours"
}
```

The worker reads this payload and executes the appropriate application action.

Never allow arbitrary code execution from an LLM-generated payload.

Only allow registered actions.

Example:

```text
github_daily_summary
send_notification
calendar_summary
daily_briefing
```

---

# 23. Retry Handling

Transient failures should be retried.

Example:

```text
Attempt 1 → failed
     ↓
wait
     ↓
Attempt 2 → failed
     ↓
wait
     ↓
Attempt 3 → success
```

Configure BullMQ retry/backoff.

Do not retry forever.

Example:

```ts
attempts: 3,
backoff: {
  type: "exponential",
  delay: 5000
}
```

Adjust values according to the project.

---

# 24. Idempotency

The same task must not accidentally send multiple notifications because of a worker restart or retry.

Before performing an irreversible action:

1. Check execution state.
2. Use a unique execution/task key.
3. Record the result.
4. Make notification delivery idempotent where possible.

For example:

```text
task_id + scheduled_for
```

can represent one logical execution.

---

# 25. Server Restart Behavior

This system must survive:

- Node.js restart
- server deployment
- worker crash
- Redis restart where possible
- temporary notification failure

Do NOT use:

```js
setTimeout(...)
```

as the source of truth.

Do NOT keep scheduled tasks only in application memory.

After restarting the server, tasks must still exist in PostgreSQL and be recoverable by the scheduler.

---

# 26. API Endpoints

If the project has a REST API, implement endpoints similar to:

```text
POST   /api/scheduled-tasks
GET    /api/scheduled-tasks
GET    /api/scheduled-tasks/:id
PATCH  /api/scheduled-tasks/:id
DELETE /api/scheduled-tasks/:id

POST   /api/scheduled-tasks/:id/snooze
POST   /api/scheduled-tasks/:id/run
```

The AI tools should call application services rather than duplicating business logic.

Example architecture:

```text
REST API ───────┐
                │
AI Tools ───────┼──→ ScheduledTaskService
                │
UI ─────────────┘
                       │
              ┌────────┴────────┐
              ↓                 ↓
         PostgreSQL          BullMQ
```

---

# 27. ScheduledTaskService

Create one central service responsible for task lifecycle.

Suggested methods:

```ts
createTask()
getTask()
listTasks()
updateTask()
cancelTask()
snoozeTask()
executeTask()
scheduleTask()
```

Do not put scheduling logic directly inside route handlers.

---

# 28. Security

Every task must belong to a user.

Never allow:

```text
user A → access user B's scheduled task
```

Every database query must scope by authenticated `user_id` where appropriate.

Do not allow the LLM to choose arbitrary:

- SQL
- shell commands
- URLs
- filesystem operations
- code execution

Use allowlisted application tools.

---

# 29. Validation

Validate:

- title is not empty
- datetime is valid
- timezone is valid
- recurrence rule is valid
- task type is allowed
- notification channels are allowed
- user owns the task

Reject invalid schedules before creating BullMQ jobs.

---

# 30. UI

If the assistant has a frontend, add a Scheduled Tasks page.

Show:

```text
Scheduled
────────────────────────────

Tomorrow

08:00 AM
Work on GitHub bot
[Edit] [Snooze] [Cancel]

09:00 AM
Daily briefing
[Edit] [Snooze] [Cancel]


Recurring
────────────────────────────

Every Monday — 09:00 AM
Check GitHub PRs
[Edit] [Pause] [Cancel]
```

Also show execution history if practical.

---

# 31. Suggested Environment Variables

Use the project's existing configuration system.

Potential values:

```env
DATABASE_URL=
REDIS_URL=
DEFAULT_TIMEZONE=UTC
```

Notification provider credentials should also be environment variables.

Never commit secrets.

---

# 32. Local Development

Use Docker Compose if the project already uses Docker, or add it if appropriate.

Minimum services:

```text
PostgreSQL
Redis
Backend
Worker
```

Conceptually:

```text
docker compose

postgres
redis
api
worker
```

The API and worker may use the same codebase but run as different processes.

Example:

```text
npm run dev
npm run worker
```

---

# 33. Recommended Process Separation

Do not make the API process responsible for executing every scheduled task.

Run:

```text
API process
Worker process
```

Example:

```text
Terminal 1:
npm run dev

Terminal 2:
npm run worker
```

In production:

```text
API container
Worker container
PostgreSQL
Redis
```

Scale workers independently when needed.

---

# 34. Testing

Implement tests for:

### Creation

```text
create one-time reminder
create recurring reminder
```

### Time

```text
correct timezone
future time
past time
```

### Worker

```text
executes task
records success
records failure
```

### Cancellation

```text
cancel before execution
cancel while queued
```

### Snooze

```text
snooze task
old schedule removed
new schedule created
```

### Restart

```text
restart worker
scheduled task remains available
```

### Authorization

```text
user cannot access another user's task
```

---

# 35. Example End-to-End Flow

User says:

```text
Every Monday at 9 AM remind me to check my GitHub PRs.
```

### Step 1 — AI

AI determines:

```text
action = create_scheduled_task
type = reminder
recurrence = weekly
day = Monday
time = 09:00
timezone = user's timezone
title = Check my GitHub PRs
```

### Step 2 — Application

`ScheduledTaskService.createTask()`:

```text
Validate
 ↓
Insert PostgreSQL
 ↓
Create BullMQ scheduled job
 ↓
Store BullMQ reference
```

### Step 3 — Wait

The AI request ends.

The AI does NOT remain active.

### Step 4 — Monday 9 AM

BullMQ makes the job available.

### Step 5 — Worker

Worker loads:

```text
task_id = XYZ
```

from the job.

Then loads the task from PostgreSQL.

### Step 6 — Execute

For a simple reminder:

```text
send notification
```

### Step 7 — Record

Create:

```text
task_execution
status = success
```

Update:

```text
last_run_at
next_run_at
```

### Step 8 — Next Monday

The recurring scheduler schedules the next occurrence.

---

# 36. Future Architecture: Trigger → Condition → Action

Do not implement this immediately unless the existing project is ready for it, but design the system so it can eventually support:

```text
TRIGGER
   ↓
CONDITION
   ↓
ACTION
```

Examples:

### Time trigger

```text
Every day at 9 AM
    ↓
Generate daily briefing
```

### GitHub trigger

```text
PR merged
    ↓
Check whether it belongs to user
    ↓
Notify user
```

### Email trigger

```text
New email
    ↓
Sender is important
    ↓
Summarize with AI
    ↓
Notify user
```

This can eventually turn the scheduler into an automation engine.

---

# 37. Important Design Rule

Keep these four concepts separate:

```text
Task
= What should happen?

Schedule
= When should it happen?

Execution
= What actually happened?

Notification
= How should the user be informed?
```

Do not mix all four into one piece of code.

---

# 38. Implementation Order

Implement in exactly this order unless the existing project requires a different dependency order.

### Phase 1

- PostgreSQL `scheduled_tasks`
- PostgreSQL `task_executions`
- ScheduledTaskService
- basic CRUD

### Phase 2

- Redis connection
- BullMQ queue
- BullMQ worker
- one-time delayed jobs

### Phase 3

- recurring schedules
- timezone support
- retries
- execution history

### Phase 4

- AI tools
- natural-language scheduling
- list/cancel/update/snooze tools

### Phase 5

- notification abstraction
- actual notification provider
- notification preferences

### Phase 6

- frontend scheduled-task UI

### Phase 7

- conditional/event-driven tasks
- GitHub integration
- AI workflows

---

# 39. Definition of Done

The implementation is complete when all of these work:

```text
[ ] User can say "remind me in 10 minutes"
[ ] Task is stored in PostgreSQL
[ ] BullMQ schedules it
[ ] Worker executes it
[ ] User receives notification
[ ] Execution is recorded
[ ] User can list scheduled tasks
[ ] User can cancel tasks
[ ] User can snooze tasks
[ ] Recurring tasks work
[ ] Timezones work
[ ] Failed jobs retry
[ ] Server restart does not lose tasks
[ ] User isolation/security works
[ ] AI can create/manage tasks using tools
```

---

# 40. Final Architecture

The target architecture should be:

```text
                         USER
                           │
                           ▼
                    AI ASSISTANT
                           │
                           │ tool call
                           ▼
                ScheduledTaskService
                    │            │
                    │            │
                    ▼            ▼
               PostgreSQL     BullMQ
                    │            │
                    │          Redis
                    │            │
                    │            ▼
                    │          Worker
                    │            │
                    │      ┌─────┴─────┐
                    │      │           │
                    │      ▼           ▼
                    │    Simple      AI Task
                    │    Reminder       │
                    │                   ▼
                    │                 Groq
                    │                   │
                    │                   ▼
                    │              Notification
                    │                   │
                    ▼                   ▼
              Execution History       USER
```

The key rule is:

**PostgreSQL remembers.  
BullMQ schedules.  
Redis supports the queue.  
Worker executes.  
Groq thinks.  
Notification service tells the user.**

Do not make the LLM wait for scheduled events.
