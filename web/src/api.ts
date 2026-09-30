// Typed client for the Donna backend (/api/v1) plus the Server-Sent Events reader for chat.

export interface Conversation {
  id: string;
  title: string;
  pinned: boolean;
  created_at: number;
  updated_at: number;
  preview?: string | null;
  snippet?: string;
}

export interface ToolEvent {
  call_id: string;
  name: string;
  server: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error" | "rejected" | "waiting";
  ok: boolean;
  preview: string;
  ms: number;
  approved?: boolean | "auto";
  description?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  tool_events: ToolEvent[];
  created_at: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  confirm: boolean;
  enabled: boolean;
}

export type ConnState = "connected" | "not_set_up" | "needs_auth" | "needs_reauth" | "error" | "off" | "connecting" | "stopped";

export interface Connection {
  id: string;
  kind: "builtin" | "mcp";
  name: string;
  icon: string;
  source: string;
  setup: "google" | "notion" | "mcp" | null;
  state: ConnState;
  auth_state?: ConnState;
  detail: string;
  transport?: string;
  tools: ToolInfo[];
}

export interface Usage {
  date: string;
  requests: number;
  tokens: number;
}

/** plan: read-only, writes a plan · manual: actions wait for approval · auto: actions run without asking */
export type PermissionMode = "plan" | "manual" | "auto";

export interface Settings {
  model: string;
  timezone: string;
  reasoning_effort: string;
  pixel_grid: boolean;
  reduce_motion: boolean;
  onboarded: boolean;
  permission_mode: PermissionMode;
  groq_key_set: boolean;
  groq_key_hint: string;
  usage: Usage;
  data_dir: string;
  home: string;
}

export interface Health {
  groq: "ok" | "no_key" | "invalid_key" | "unreachable" | "error";
  online: boolean;
  usage: Usage;
}

export type TaskType = "reminder" | "ai_task";
export type TaskStatus = "scheduled" | "running" | "completed" | "cancelled" | "failed" | "paused";
export type RecurrenceType = "once" | "interval" | "cron";
export type ExecutionStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "missed" | "approval_required";

export interface TaskExecution {
  id: string;
  scheduled_for: string;
  trigger: "schedule" | "manual";
  status: ExecutionStatus;
  attempts: number;
  started_at: string | null;
  completed_at: string | null;
  result: string | null;
  error: string | null;
  conversation_id: string | null;
}

export interface ScheduledTask {
  id: string;
  title: string;
  description: string | null;
  type: TaskType;
  status: TaskStatus;
  run_at: string | null;
  timezone: string;
  recurrence_type: RecurrenceType;
  recurrence_rule: string | null;
  message: string;
  prompt: string;
  conversation_id: string | null;
  notification_channels: string[];
  permission_mode: PermissionMode;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status?: ExecutionStatus | null;
  last_result?: string | null;
  last_error?: string | null;
  executions?: TaskExecution[];
  warning?: string | null;
}

/** Create/edit body. run_at is local time without an offset (read in `timezone`). */
export interface TaskInput {
  title: string;
  type: TaskType;
  message?: string;
  prompt?: string;
  run_at?: string | null;
  timezone?: string;
  recurrence_type: RecurrenceType;
  recurrence_rule?: string | null;
  notification_channels?: string[];
  permission_mode?: PermissionMode;
}

export interface SchedulerHealth {
  ok: boolean;
  scheduler: "ok" | "down";
  redis: string;
  postgres: "ok" | "down";
  worker: string;
  detail?: string;
}

export interface Notice {
  id: string;
  task_id: string | null;
  title: string;
  body: string;
  level: "info" | "warn" | "error";
  conversation_id: string | null;
  created_at: string;
}

export type ChatEvent =
  | { type: "run.start"; run_id: string; conversation: Conversation }
  | { type: "delta"; text: string }
  | { type: "status"; text: string }
  | ({ type: "tool.start" } & ToolEvent)
  | ({ type: "tool.end" } & ToolEvent)
  | { type: "confirm.request"; run_id: string; call_id: string; name: string; server: string; args: Record<string, unknown>; description: string }
  | { type: "done"; message: Message; stopped: boolean; conversation: Conversation }
  | { type: "error"; message: string };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers: body instanceof FormData || body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Donna's server isn't running. Start it with scripts\\start.ps1.", 0);
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, res.status);
  }
  const type = res.headers.get("content-type") || "";
  return (type.includes("application/json") ? res.json() : (res.text() as unknown)) as Promise<T>;
}

export const api = {
  health: () => request<Health>("GET", "/health"),
  conversations: (q = "") => request<Conversation[]>("GET", `/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createConversation: () => request<Conversation>("POST", "/conversations"),
  updateConversation: (id: string, patch: { title?: string; pinned?: boolean }) =>
    request<Conversation>("PATCH", `/conversations/${id}`, patch),
  deleteConversation: (id: string) => request("DELETE", `/conversations/${id}`),
  messages: (id: string) => request<Message[]>("GET", `/conversations/${id}/messages`),
  confirm: (runId: string, callId: string, approved: boolean) =>
    request("POST", `/runs/${runId}/confirm`, { call_id: callId, approved }),
  cancel: (runId: string) => request("POST", `/runs/${runId}/cancel`),

  connections: () => request<Connection[]>("GET", "/connections"),
  patchConnection: (id: string, patch: { enabled?: boolean; disabled_tools?: string[] }) =>
    request("PATCH", `/connections/${id}`, patch),
  uploadGoogleCredentials: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ ok: boolean; project_id: string; filename: string }>("POST", "/connections/google/credentials", fd);
  },
  authorizeGoogle: () => request<{ ok: boolean; email: string }>("POST", "/connections/google/authorize"),
  testGoogle: () => request<{ events: string[]; emails: string[] }>("POST", "/connections/google/test"),
  disconnectGoogle: (forgetClient = false) => request("DELETE", `/connections/google?forget_client=${forgetClient}`),
  connectNotion: (token: string) => request<{ ok: boolean; pages: string[] }>("POST", "/connections/notion", { token }),
  disconnectNotion: () => request("DELETE", "/connections/notion"),
  testServer: (cfg: ServerConfig) => request<{ ok: boolean; tools: ToolInfo[] }>("POST", "/mcp-servers/test", cfg),
  addServer: (cfg: ServerConfig) => request<{ id: string; state: string; error: string }>("POST", "/mcp-servers", cfg),
  removeServer: (id: string) => request("DELETE", `/mcp-servers/${id}`),
  restartServer: (id: string) => request<{ state: string; error: string; tools: number }>("POST", `/mcp-servers/${id}/restart`),

  settings: () => request<Settings>("GET", "/settings"),
  saveSettings: (patch: Partial<Settings>) => request<Settings>("PUT", "/settings", patch),
  saveGroqKey: (key?: string) => request<{ ok: boolean; models: string[]; hint: string }>("PUT", "/settings/groq-key", { key }),
  wipe: () => request<{ ok: boolean; deleted: number }>("POST", "/wipe"),
  openDataFolder: () => request("POST", "/open-data-folder"),

  schedulerHealth: () => request<SchedulerHealth>("GET", "/scheduler/health"),
  tasks: () => request<ScheduledTask[]>("GET", "/scheduled-tasks"),
  task: (id: string) => request<ScheduledTask>("GET", `/scheduled-tasks/${id}`),
  createTask: (t: TaskInput) => request<ScheduledTask>("POST", "/scheduled-tasks", t),
  updateTask: (id: string, patch: Partial<TaskInput>) => request<ScheduledTask>("PATCH", `/scheduled-tasks/${id}`, patch),
  deleteTask: (id: string) => request("DELETE", `/scheduled-tasks/${id}`),
  previewTask: (t: Pick<TaskInput, "run_at" | "timezone" | "recurrence_type" | "recurrence_rule">) =>
    request<{ runs: string[] }>("POST", "/scheduled-tasks/preview", t),
  taskAction: (id: string, action: "cancel" | "pause" | "resume" | "run") =>
    request<ScheduledTask>("POST", `/scheduled-tasks/${id}/${action}`),
  snoozeTask: (id: string, minutes: number) => request<ScheduledTask>("POST", `/scheduled-tasks/${id}/snooze`, { minutes }),
  notifications: () => request<Notice[]>("GET", "/notifications"),
  markNotificationsRead: (ids: string[]) => request("POST", "/notifications/read", { ids }),
};

export interface ServerConfig {
  name: string;
  transport: "stdio" | "http";
  command: string;
  args: string;
  url: string;
  env: Record<string, string>;
  disabled_tools?: string[];
}

/** POST a chat message and feed each SSE event to `onEvent` until the stream ends. */
export async function streamChat(
  conversationId: string,
  text: string,
  connections: string[],
  onEvent: (ev: ChatEvent) => void,
  mode?: PermissionMode,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, connections, mode }),
    });
  } catch {
    throw new ApiError("Donna's server isn't running. Start it with scripts\\start.ps1.", 0);
  }
  if (!res.ok || !res.body) {
    let detail = `Request failed (${res.status})`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, res.status);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = block
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data) as ChatEvent);
    }
  }
}
