# Donna Architecture Conversion — BullMQ Scheduler Migration

**Project:** Donna — Local AI Assistant  
**Purpose:** Convert Donna from the current single-process architecture into a hybrid Python + Node.js/BullMQ architecture while preserving the existing FastAPI, Groq, MCP, React, SQLite, and security design.

---

## 1. Objective

Donna currently runs its main application as a single Python/FastAPI process. This migration adds a durable scheduling subsystem based on:

- FastAPI / Python for Donna's existing application, agent, and MCP functionality
- Node.js + TypeScript for the scheduler
- BullMQ for scheduling, queueing, delayed jobs, retries, and workers
- Redis as BullMQ's queue backend
- PostgreSQL for scheduled-task persistence and execution history
- SQLite remains for existing Donna application data
- Groq remains responsible for AI reasoning when a scheduled task requires AI
- MCPHub remains the owner of MCP connections and tool execution

This is an architectural expansion, not a complete rewrite of Donna.

---

# 2. Current Architecture

```text
React UI
   ↓
FastAPI
   ↓
Agent
   ├── Groq
   └── MCPHub
          ├── Built-in MCP tools
          └── External MCP servers

FastAPI / Donna
   ↓
SQLite
   ↓
Windows Credential Manager / keyring
```

The existing application includes:

- React SPA
- FastAPI REST API
- SSE chat streaming
- Groq integration
- Agent/tool-calling loop
- MCPHub
- Built-in MCP tools
- External MCP servers
- SQLite WAL + FTS5
- Windows keyring
- Localhost-only operation
- Approval gates

The old architecture's "one process, no Redis, no background workers" constraint must be changed for the scheduling subsystem.

---

# 3. Target Architecture

```text
                              DONNA
┌───────────────────────────────────────────────────────────────┐
│                         React UI                              │
│                            │                                  │
│                            ▼                                  │
│                    FastAPI / Python                           │
│                                                               │
│   ┌───────────────┐   ┌──────────────┐   ┌────────────────┐  │
│   │ Donna Agent   │   │  Scheduler   │   │    MCPHub      │  │
│   │               │   │     API      │   │                │  │
│   │ Groq          │   │ Task CRUD    │   │ Built-in MCP   │  │
│   │ Tool Calling  │   │              │   │ External MCP   │  │
│   └───────┬───────┘   └──────┬───────┘   └───────┬────────┘  │
│           │                  │                   │           │
│           │                  ▼                   │           │
│           │             PostgreSQL              │           │
│           │          scheduled_tasks             │           │
│           │          task_executions             │           │
│           │                                      │           │
└───────────┼──────────────────────────────────────┼───────────┘
            │                                      │
            │                                      │
            │                    Internal execution│
            │                                      ▼
            │                                MCP tools
            ▼
       Groq when required


                    Scheduler Subsystem
┌───────────────────────────────────────────────────────────────┐
│ Node.js + TypeScript                                          │
│                                                               │
│ Scheduler API / Queue Manager                                 │
│                    │                                          │
│                    ▼                                          │
│                  Redis                                        │
│                    │                                          │
│                    ▼                                          │
│                 BullMQ                                        │
│                    │                                          │
│                    ▼                                          │
│              BullMQ Worker                                    │
│                    │                                          │
│                    ▼                                          │
│             Task Executor                                     │
│                    │                                          │
│                    ▼                                          │
│         FastAPI internal execution API                        │
│                    │                                          │
│                    ▼                                          │
│              Donna Agent / MCPHub                             │
└───────────────────────────────────────────────────────────────┘
```

---

# 4. Architectural Principles

## 4.1 Do not rewrite Donna

Keep the existing Python application as the main Donna application.

Do not migrate the entire project to Node.js.

Do not duplicate the existing MCP implementation in TypeScript.

Do not replace the existing Groq agent.

## 4.2 Scheduler is a separate subsystem

Scheduling is implemented as a dedicated Node.js/TypeScript service.

Its responsibilities are:

- scheduling
- queue management
- delayed jobs
- recurring jobs
- worker execution lifecycle
- BullMQ retries
- queue state
- communication with Donna's Python backend

It should not become a second implementation of Donna.

## 4.3 MCPHub remains the MCP owner

The Node.js worker must not independently implement MCP client management or tool execution.

Correct:

```text
BullMQ Worker
     ↓
FastAPI internal execution endpoint
     ↓
Donna Agent / MCPHub
     ↓
MCP tool
```

## 4.4 PostgreSQL is the scheduling source of truth

PostgreSQL stores durable scheduling state.

BullMQ/Redis is responsible for queue execution mechanics.

```text
PostgreSQL
    = What tasks exist and their business state

BullMQ / Redis
    = When work should be queued and executed
```

## 4.5 SQLite remains in place

Do not migrate all Donna data to PostgreSQL as part of this change.

Keep SQLite for:

- conversations
- messages
- FTS5
- MCP server configuration
- settings
- other existing Donna application state

Use PostgreSQL specifically for scheduling.

---

# 5. Service Boundaries

## FastAPI

Example:

```text
127.0.0.1:8765
```

Responsibilities:

- React/API serving
- chat
- SSE
- Groq agent
- MCPHub
- existing Donna functionality
- scheduled task CRUD API
- internal scheduled-task execution API

## Scheduler Service

Example:

```text
127.0.0.1:8766
```

Technology:

- Node.js
- TypeScript
- BullMQ

Responsibilities:

- scheduling
- BullMQ job creation/cancellation
- worker processing
- retries
- communication with FastAPI
- recovery/reconciliation

## Redis

Local Redis instance used by BullMQ.

## PostgreSQL

Local PostgreSQL instance used for:

- scheduled tasks
- execution history
- scheduling state

---

# 6. Suggested Project Structure

```text
Donna/
├── donna/
│   ├── app.py
│   ├── agent.py
│   ├── llm.py
│   ├── hub.py
│   ├── store.py
│   ├── vault.py
│   └── ...
│
├── scheduler/
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   └── src/
│       ├── server.ts
│       ├── config.ts
│       ├── db.ts
│       ├── queue.ts
│       ├── scheduler.ts
│       ├── worker.ts
│       ├── executor.ts
│       ├── types.ts
│       └── routes/
│           └── tasks.ts
│
├── migrations/
│   └── postgres/
│       ├── 001_scheduled_tasks.sql
│       └── 002_task_executions.sql
│
├── frontend/
│   └── ...
│
├── data/
│   └── donna.db
│
└── ...
```

Adjust names to match the repository.

---

# 7. PostgreSQL Data Model

## 7.1 scheduled_tasks

Suggested schema:

```sql
CREATE TABLE scheduled_tasks (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    run_at TIMESTAMPTZ,
    timezone TEXT NOT NULL,
    recurrence_type TEXT NOT NULL DEFAULT 'once',
    recurrence_rule TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    notification_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    bullmq_job_id TEXT,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Recommended indexes:

```sql
CREATE INDEX idx_scheduled_tasks_user_id
ON scheduled_tasks(user_id);

CREATE INDEX idx_scheduled_tasks_status
ON scheduled_tasks(status);

CREATE INDEX idx_scheduled_tasks_next_run_at
ON scheduled_tasks(next_run_at);

CREATE INDEX idx_scheduled_tasks_status_next_run
ON scheduled_tasks(status, next_run_at);
```

## 7.2 task_executions

```sql
CREATE TABLE task_executions (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES scheduled_tasks(id),
    scheduled_for TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Recommended indexes:

```sql
CREATE INDEX idx_task_executions_task_id
ON task_executions(task_id);

CREATE INDEX idx_task_executions_status
ON task_executions(status);

CREATE INDEX idx_task_executions_scheduled_for
ON task_executions(scheduled_for);
```

---

# 8. Task Types

Version 1:

```text
reminder
ai_task
```

Future:

```text
calendar_event
conditional_task
automation
```

Do not implement the complete automation engine during the first migration.

---

# 9. Task Statuses

```text
scheduled
running
completed
cancelled
failed
paused
```

For recurring tasks, completion of one occurrence must not permanently complete the recurring task.

---

# 10. Recurrence

Initial recurrence types:

```text
once
interval
cron
```

Examples:

```text
once:
2026-10-01T09:00:00+05:30

interval:
every 30 minutes

cron:
0 9 * * *
```

All timestamps must be timezone-aware.

---

# 11. Timezones

Every scheduled task should have an explicit timezone.

Example:

```text
timezone = Asia/Kolkata
```

Use a proper timezone-aware library.

Do not silently interpret user-local times as UTC.

For recurring schedules, preserve the timezone because local calendar behavior can differ from fixed UTC intervals.

---

# 12. BullMQ Job Payload

Keep BullMQ jobs small.

Prefer:

```json
{
  "taskId": "uuid"
}
```

The worker loads the authoritative task from PostgreSQL.

```text
BullMQ Job
    ↓
taskId
    ↓
PostgreSQL
    ↓
scheduled_tasks
```

Do not embed the complete task definition in every BullMQ job.

---

# 13. Execution Lifecycle

```text
scheduled
    ↓
BullMQ delayed job becomes ready
    ↓
worker receives job
    ↓
load task from PostgreSQL
    ↓
verify task is still valid
    ↓
create task_execution
    ↓
mark execution running
    ↓
execute task
    ↓
success ─────────────→ record result
    │
    └─────────────────→ calculate next occurrence
                          ↓
                        scheduled

failure
    ↓
record error
    ↓
BullMQ retry/backoff
    ↓
retry or final failure
```

---

# 14. Validation Before Execution

Before executing a job:

1. Load the task from PostgreSQL.
2. Verify it exists.
3. Verify it is not cancelled.
4. Verify it is not paused.
5. Verify the job is still relevant.
6. Create or locate the execution record.
7. Prevent duplicate execution where necessary.
8. Execute the task.

If the task was cancelled after the BullMQ job was created, the worker must safely skip it.

---

# 15. Idempotency

Idempotency is required because jobs can be repeated due to:

- retries
- worker restarts
- crashes
- network failures
- queue recovery

Use a stable execution identity, for example:

```text
task_id + scheduled_for
```

or a generated execution ID stored before execution.

The database should prevent multiple successful executions for the same logical scheduled occurrence.

---

# 16. BullMQ Responsibilities

BullMQ owns:

- delayed jobs
- retries
- backoff
- worker processing
- job state
- queue lifecycle
- recurring scheduling mechanics where used

PostgreSQL owns the durable business state.

---

# 17. Retry Policy

Example starting policy:

```text
attempts: 3

backoff:
  type: exponential
  delay: 5000
```

Classify errors.

### Retryable

- temporary network failure
- temporary MCP failure
- temporary service unavailable
- transient database connection failure

### Non-retryable

- cancelled task
- invalid task definition
- unsupported task type
- permanently invalid credentials
- approval required but unavailable

Do not retry every error blindly.

---

# 18. Reminder Execution

Deterministic reminders should not use Groq.

Example:

```text
"Remind me to drink water at 3 PM"
```

Flow:

```text
BullMQ Worker
    ↓
Reminder Executor
    ↓
Notification
```

This saves Groq quota.

---

# 19. AI Task Execution

AI tasks use the existing Donna Agent.

Example:

```text
"Every morning summarize my unread emails."
```

Flow:

```text
BullMQ Worker
  ↓
FastAPI internal execution endpoint
  ↓
Donna Agent
  ↓
Groq
  ↓
MCPHub
  ↓
Gmail MCP
  ↓
Agent
  ↓
Result
  ↓
Notification
```

The scheduler worker should not directly call Groq.

---

# 20. Internal FastAPI Execution API

Suggested endpoint:

```http
POST /api/v1/internal/scheduled-tasks/{task_id}/execute
```

Request:

```json
{
  "execution_id": "uuid",
  "scheduled_for": "2026-10-01T09:00:00+05:30"
}
```

Response:

```json
{
  "success": true,
  "result": "Reminder sent successfully."
}
```

This endpoint must remain localhost-only.

---

# 21. Internal API Authentication

Use a local shared secret or equivalent authentication mechanism.

Example:

```text
DONNA_INTERNAL_API_TOKEN=...
```

Never hardcode it.

Never log it.

Do not store it in task payloads or the normal application database.

---

# 22. Scheduler API

React should communicate with FastAPI.

Suggested endpoints:

```http
POST   /api/v1/scheduled-tasks
GET    /api/v1/scheduled-tasks
GET    /api/v1/scheduled-tasks/{id}
PATCH  /api/v1/scheduled-tasks/{id}
DELETE /api/v1/scheduled-tasks/{id}

POST /api/v1/scheduled-tasks/{id}/cancel
POST /api/v1/scheduled-tasks/{id}/snooze
POST /api/v1/scheduled-tasks/{id}/run
```

React must not directly access Redis or BullMQ.

---

# 23. Create Task Flow

```text
React
  ↓
POST /api/v1/scheduled-tasks
  ↓
FastAPI
  ↓
validate task
  ↓
insert PostgreSQL scheduled_tasks
  ↓
request scheduler to create BullMQ job
  ↓
store bullmq_job_id
  ↓
return task
```

PostgreSQL and BullMQ are separate systems, so these operations are not one atomic transaction.

A reconciliation process must recover tasks whose database record exists but whose BullMQ job is missing.

---

# 24. Update Task Flow

```text
React
  ↓
PATCH task
  ↓
FastAPI
  ↓
update PostgreSQL
  ↓
remove/replace old BullMQ schedule
  ↓
create/update new BullMQ job
```

Changing run time, recurrence, timezone, or status may require replacing the BullMQ schedule.

---

# 25. Cancel Flow

```text
React
  ↓
POST /cancel
  ↓
FastAPI
  ↓
mark PostgreSQL task = cancelled
  ↓
cancel/remove BullMQ job
```

The worker must still verify PostgreSQL state to handle races.

---

# 26. Snooze Flow

```text
POST /snooze
    ↓
update PostgreSQL
    ↓
replace BullMQ delayed job
```

Example:

```text
Current: 09:00
Snooze: 30 minutes
New: 09:30
```

---

# 27. Manual Run

Manual runs should go through BullMQ too.

```text
POST /scheduled-tasks/{id}/run
    ↓
BullMQ
    ↓
Worker
    ↓
Task Executor
```

Do not create a second execution path that bypasses the worker.

---

# 28. Startup

Development:

```text
Terminal 1:
Redis

Terminal 2:
PostgreSQL

Terminal 3:
FastAPI
python -m donna

Terminal 4:
Scheduler
npm run dev
```

The React UI does not need to be open for scheduled tasks to execute.

---

# 29. Autostart

Recommended background startup:

```text
Windows starts
    ↓
Redis available
    ↓
PostgreSQL available
    ↓
Donna FastAPI
    ↓
Scheduler worker
```

No Docker is required.

Windows startup mechanisms can be used later to launch the local services.

---

# 30. Missed Jobs / Catch-up Policy

Do not blindly execute every missed job after downtime.

Recommended initial behavior:

### One-time reminders

If missed by a short amount:

```text
mark as missed
notify user that it was missed
```

If too old:

```text
mark missed
do not execute automatically
```

### AI tasks

Use a configurable maximum catch-up age.

Example:

```text
Scheduled: 09:00
Donna returns: 15:00

Do not automatically run an old AI task unless it is within its catch-up window.
```

The exact default window should be configurable.

---

# 31. Reconciliation

Because PostgreSQL and Redis are separate, periodically reconcile them.

Concept:

```text
PostgreSQL
   ↓
find active tasks with next_run_at
   ↓
check corresponding BullMQ job
   ↓
missing?
   ↓
recreate job
```

This protects against:

- Redis restart
- worker crash
- application restart
- partial scheduling failures
- lost queue metadata

PostgreSQL remains authoritative.

---

# 32. Failure Behavior

## Redis unavailable

- PostgreSQL task data remains intact
- scheduler reports unhealthy
- scheduling requests fail clearly or enter recoverable state
- reconciliation occurs after Redis returns

Do not delete PostgreSQL tasks.

## PostgreSQL unavailable

- worker should not execute tasks without authoritative state
- transient connection failures may be retried
- prefer safety over stale execution

## Worker crash

BullMQ should recover pending work according to its queue semantics.

Execution records must make duplicate execution detectable.

---

# 33. Notifications

Use a notification abstraction.

Possible channels:

```text
windows
in_app
ntfy
```

Architecture:

```text
Task Executor
     ↓
Notification Service
     ├── Windows toast
     ├── Browser/in-app
     └── ntfy
```

Do not put notification-specific logic throughout the scheduler.

---

# 34. Groq Usage

Donna already has Groq free-tier limits.

Scheduling must not create uncontrolled Groq traffic.

Do not call Groq for deterministic tasks.

Future work can add:

- per-model request throttling
- token buckets
- queue-level AI concurrency limits

---

# 35. MCP Execution

Correct:

```text
BullMQ
  ↓
Worker
  ↓
FastAPI
  ↓
Agent / MCPHub
  ↓
MCP
```

Incorrect:

```text
BullMQ
  ↓
Node worker
  ↓
new MCP client implementation
```

The existing MCPHub remains the single owner of MCP connections.

---

# 36. Approval Gates

Scheduled execution must not bypass existing approval/security behavior.

For V1:

- tasks requiring interactive approval must not silently bypass approval
- record `approval_required` where appropriate
- notify the user
- preserve existing security rules

---

# 37. Security

Keep the system local-first.

```text
FastAPI:
127.0.0.1:8765

Scheduler:
127.0.0.1:8766
```

Redis and PostgreSQL should remain local.

Do not expose BullMQ administration publicly.

Do not store credentials in task payloads.

Do not log:

- API keys
- OAuth tokens
- internal auth tokens
- MCP credentials
- sensitive tool payloads

Continue using Windows Credential Manager/keyring for secrets.

---

# 38. Environment Variables

Suggested scheduler configuration:

```text
REDIS_URL=redis://127.0.0.1:6379

POSTGRES_URL=postgresql://...

DONNA_API_URL=http://127.0.0.1:8765

DONNA_INTERNAL_API_TOKEN=...

SCHEDULER_HOST=127.0.0.1

SCHEDULER_PORT=8766
```

Never commit real credentials.

---

# 39. Scheduler Components

## server.ts

- start scheduler HTTP server
- health endpoint
- scheduler routes
- initialize dependencies

## queue.ts

- Redis connection
- BullMQ queue
- queue configuration

## scheduler.ts

- delayed jobs
- recurring jobs
- cancellation
- rescheduling
- reconciliation

## worker.ts

- BullMQ Worker
- job processing
- retries
- lifecycle

## executor.ts

- load execution context
- call FastAPI
- map responses/errors

## db.ts

- PostgreSQL connection pool
- scheduler database operations

## types.ts

- task/job types
- status enums
- execution result types

---

# 40. Health Endpoint

Scheduler:

```http
GET /health
```

Example:

```json
{
  "status": "ok",
  "redis": "ok",
  "postgres": "ok",
  "worker": "running"
}
```

FastAPI should retain its existing health mechanism.

---

# 41. Database Migrations

Version PostgreSQL changes:

```text
migrations/postgres/
    001_scheduled_tasks.sql
    002_task_executions.sql
```

Future schema changes should use additional migrations.

Do not recreate tables every time the application starts.

---

# 42. Implementation Order

## Phase 1 — Infrastructure

Set up:

- PostgreSQL
- Redis
- scheduler directory
- Node.js/TypeScript project
- environment configuration

Do not change the agent yet.

## Phase 2 — PostgreSQL

Create:

```text
scheduled_tasks
task_executions
```

Add indexes and constraints.

## Phase 3 — BullMQ

Implement:

```text
queue.ts
worker.ts
scheduler.ts
```

Verify a simple delayed job.

## Phase 4 — FastAPI Scheduler API

Implement task CRUD and scheduler communication.

## Phase 5 — Internal Execution API

Implement:

```text
POST /api/v1/internal/scheduled-tasks/{task_id}/execute
```

Protect it.

## Phase 6 — Reminders

Implement deterministic reminders without Groq.

## Phase 7 — AI Tasks

Connect scheduled AI execution to the existing Donna Agent.

## Phase 8 — Recovery

Implement:

- restart recovery
- catch-up
- reconciliation
- idempotency
- duplicate protection

## Phase 9 — Notifications

Implement notification abstraction and channels.

## Phase 10 — React UI

Add:

- task list
- create/edit
- cancel
- snooze
- manual run
- execution history
- status
- next run
- failures

---

# 43. Testing

## Unit tests

Test:

- recurrence calculation
- timezone handling
- validation
- status transitions
- retry classification
- idempotency
- catch-up rules

## Integration tests

Test:

```text
FastAPI
    ↓
PostgreSQL
    ↓
Scheduler
    ↓
Redis
    ↓
BullMQ Worker
```

## End-to-end tests

### Reminder

```text
create → wait → execute → notification → history
```

### Recurring

```text
create → execution #1 → next_run_at → execution #2
```

### Cancellation

```text
create → cancel → scheduled time → must NOT execute
```

### Snooze

```text
create → snooze → old time passes → no execution → new time → execute
```

### Restart

```text
create → stop scheduler → restart → task still executes
```

### Redis restart

```text
create → restart Redis → reconcile → task remains recoverable
```

### FastAPI restart

```text
scheduled task exists → restart FastAPI → worker remains alive → task executes
```

---

# 44. Migration Rules

Do not perform a big-bang rewrite.

### Preserve

```text
React
FastAPI
Agent
Groq
MCPHub
SQLite
Keyring
SSE
```

### Add

```text
PostgreSQL
Redis
Node.js
TypeScript
BullMQ
Scheduler service
Task execution API
```

### Do not initially change

```text
conversation storage
message storage
MCP architecture
Groq integration
existing tool implementations
existing secret storage
```

---

# 45. Resource Constraints

Donna runs on a Windows laptop with approximately 8 GB RAM.

Keep everything lightweight:

- local Redis
- local PostgreSQL
- lightweight scheduler
- no Docker requirement
- no unnecessary replicas
- conservative worker concurrency

Initial worker configuration:

```text
concurrency = 1
```

Increase only after measuring resource usage.

---

# 46. Process Model

Initial setup:

```text
Process 1:
FastAPI / Donna

Process 2:
Node.js Scheduler / BullMQ Worker

Process 3:
Redis

Process 4:
PostgreSQL
```

This is a small local distributed architecture, not a large microservice fleet.

---

# 47. Responsibility Matrix

| Component | Owns |
|---|---|
| React | Task UI, status, history, user actions |
| FastAPI | Donna API, scheduler API, agent, Groq, MCPHub |
| Node Scheduler | Queue orchestration and scheduling |
| BullMQ | Jobs, delays, retries, worker state |
| Redis | BullMQ operational state |
| PostgreSQL | Durable task/execution business state |
| Donna Agent | AI reasoning and tool-calling |
| MCPHub | MCP connections and tool execution |
| SQLite | Existing Donna application data |
| Keyring | Secrets |

---

# 48. What the Worker Must NOT Do

The worker must not:

- directly manipulate SQLite
- directly execute MCP tools
- directly call Groq
- store user credentials
- become the main Donna agent
- duplicate FastAPI business logic
- become the source of truth for scheduled tasks

Its core job is:

```text
When should this run?
        ↓
Which task should run?
        ↓
Ask Donna to execute it.
```

---

# 49. Example — Simple Reminder

User:

```text
Remind me at 6 PM to call Mom.
```

Creation:

```text
React
 ↓
FastAPI
 ↓
PostgreSQL
 ↓
Scheduler
 ↓
BullMQ delayed job
```

At 6 PM:

```text
BullMQ Worker
 ↓
Load task
 ↓
Create execution
 ↓
FastAPI
 ↓
Notification
 ↓
Record execution
```

No Groq call is necessary for the reminder itself.

---

# 50. Example — AI Scheduled Task

User:

```text
Every morning at 9 AM, check my unread Gmail messages and summarize anything important.
```

Creation:

```text
React
 ↓
FastAPI
 ↓
PostgreSQL
 ↓
Scheduler
 ↓
BullMQ
```

Execution:

```text
BullMQ Worker
 ↓
FastAPI internal execution API
 ↓
Donna Agent
 ↓
Groq
 ↓
Gmail MCP
 ↓
Agent reasoning
 ↓
Result
 ↓
Notification
 ↓
task_executions
```

---

# 51. Example — Cancellation Race

Possible race:

```text
09:59:59
user cancels task

10:00:00
BullMQ worker receives job
```

Worker must load the PostgreSQL task.

If:

```text
status = cancelled
```

then:

```text
skip execution
```

This database check is mandatory.

---

# 52. Example — Worker Crash

```text
10:00
worker starts execution

10:00:02
worker crashes

10:01
worker restarts
```

BullMQ should recover the job according to its queue semantics.

Execution records must allow Donna to distinguish:

- never started
- started and failed
- completed
- uncertain

External side effects must be handled conservatively.

---

# 53. Definition of Done

- [ ] PostgreSQL runs locally
- [ ] Redis runs locally
- [ ] Scheduler is a separate TypeScript service
- [ ] BullMQ queue works
- [ ] BullMQ worker works
- [ ] Scheduled tasks persist in PostgreSQL
- [ ] Execution history persists in PostgreSQL
- [ ] FastAPI exposes scheduler CRUD
- [ ] Protected internal execution endpoint exists
- [ ] Reminder tasks execute
- [ ] AI tasks execute through Donna Agent
- [ ] MCPHub remains the MCP execution layer
- [ ] Deterministic reminders do not consume Groq
- [ ] Cancellation works
- [ ] Snooze works
- [ ] Manual run works
- [ ] Recurring tasks work
- [ ] Timezones work
- [ ] Retry/backoff works
- [ ] Idempotency protection exists
- [ ] Worker restart recovery works
- [ ] Scheduler reconciliation works
- [ ] Missed-task policy is implemented
- [ ] Secrets are protected
- [ ] Services remain localhost-only
- [ ] Existing Donna functionality still works
- [ ] React UI can manage scheduled tasks
- [ ] Documentation reflects the new architecture

---

# 54. Final Architecture

```text
                         ┌─────────────────────┐
                         │      React UI       │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   FastAPI / Donna   │
                         │                     │
                         │ Agent               │
                         │ Groq                │
                         │ MCPHub              │
                         │ Scheduler API       │
                         └──────┬───────┬──────┘
                                │       │
                    existing    │       │ scheduling data
                    Donna data  │       ▼
                                │   ┌─────────────┐
                                │   │ PostgreSQL  │
                                │   │ tasks       │
                                │   │ executions  │
                                │   └─────────────┘
                                │
                                │ internal execution
                                ▼
                         ┌─────────────────────┐
                         │ Scheduler Service   │
                         │ Node.js + TypeScript│
                         │ BullMQ              │
                         └──────────┬──────────┘
                                    │
                                    ▼
                              ┌───────────┐
                              │   Redis   │
                              └─────┬─────┘
                                    │
                                    ▼
                              ┌───────────┐
                              │  Worker   │
                              └─────┬─────┘
                                    │
                                    ▼
                         FastAPI execution API
                                    │
                                    ▼
                           Donna Agent / MCPHub
                                    │
                           ┌────────┴────────┐
                           ▼                 ▼
                         Groq              MCP
```

The key boundary is:

```text
                  SCHEDULER
                     │
                     │ "When should this run?"
                     ▼
                  BullMQ
                     │
                     │ "Run task X"
                     ▼
                   DONNA
                     │
                     │ "How should this task be executed?"
                     ▼
                Agent / MCP
```

This keeps scheduling and execution responsibilities separate while allowing Donna to grow into a local automation platform.

---

# 55. Immediate Scope

Do not implement the entire future automation platform in this migration.

The immediate objective is:

```text
Reliable scheduled tasks
        +
BullMQ worker
        +
PostgreSQL persistence
        +
Redis
        +
existing Donna execution capabilities
```

After this foundation is stable, the same architecture can support:

```text
scheduled tasks
→ recurring tasks
→ conditional tasks
→ event triggers
→ multi-step automations
→ agent-driven workflows
```

Build the scheduler as a reliable foundation first.
