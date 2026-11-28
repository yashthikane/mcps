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
  status: "running" | "done" | "error" | "rejected" | "waiting" | "pending";
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
  setup: "google" | "notion" | "images" | "mcp" | null;
  state: ConnState;
  auth_state?: ConnState;
  detail: string;
  transport?: string;
  tools: ToolInfo[];
}

/** An image generation model from the backend registry (donna/images.py). `daily` is an estimate. */
export interface ImageModel {
  id: string;
  label: string;
  note: string;
  daily: string;
  seconds: number;  // usual time for one picture
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
  llm_provider: "groq" | "claude_code";
  claude_model: string;
  timezone: string;
  reasoning_effort: string;
  pixel_grid: boolean;
  reduce_motion: boolean;
  onboarded: boolean;
  permission_mode: PermissionMode;
  image_model: string;
  groq_key_set: boolean;
  groq_key_hint: string;
  usage: Usage;
  data_dir: string;
  home: string;
  // messaging channels
  messaging_enabled: boolean;
  require_trigger: boolean;
  trigger_word: string;
  automations_enabled: boolean;
  owner_name: string;
  owner_pronouns: "" | "he" | "she" | "they";
  assistant_profile: string;
  default_language: string;
  default_tone: string;
  contact_rate_per_hour: number;
  global_rate_per_hour: number;
  contact_images_per_day: number;
  approval_ttl_hours: number;
  unknown_contact_action: "ignore" | "notify";
  openwa_url: string;
  owner_channel: string;
  // availability and the away greeting
  greeting_enabled: boolean;
  greeting_mention_trigger: boolean;
  urgent_notify_dnd: boolean;
  urgent_notify_sleeping: boolean;
  calendar_auto_status: boolean;
  sleep_hours: { enabled: boolean; start: string; end: string };
  menu_options: { id: MenuOptionId; enabled: boolean }[];
  meeting_default_minutes: number;
  meeting_hours: { start: string; end: string; days: number[] };
}

/* ------------------------------------------------------------------ availability (your status) */
export type StatusMode = "available" | "sleeping" | "working" | "dnd" | "office" | "leave" | "custom" | "meeting";
export type MenuOptionId = "message" | "meeting" | "callback" | "urgent";

export interface EffectiveStatus {
  mode: StatusMode;
  detail: string;
  until: number | null;          // epoch seconds
  source: "manual" | "calendar" | "sleep_hours" | "default";
  emoji: string;
  label: string;
  away: boolean;
  phrase: string;                // "Yashraj is working on … until 6:00 PM", as contacts are told
  until_text: string;            // "until 6:00 PM"
}

export interface Availability {
  status: EffectiveStatus;
  manual: { mode: StatusMode; detail: string; until: number | null; set_at: number };
  modes: { id: StatusMode; emoji: string; label: string }[];
  menu: { id: MenuOptionId; emoji: string; label: string; request: string }[];
}

export interface Clash { checked: boolean; free: boolean | null; detail: string; start?: string; end?: string; minutes?: number }

/* ------------------------------------------------------------------ messaging channels */
export type ChannelStatus = "not_set_up" | "connected" | "qr" | "connecting" | "disconnected" | "error";

export interface Channel {
  id: string;
  type: string;
  label: string;
  display_name: string;
  enabled: boolean;
  paused: boolean;
  status: ChannelStatus;
  account: string;
  credentials: boolean;
  last_error: string;
  last_activity_at: number | null;
  setup?: GatewaySetup;  // WhatsApp only
}

/** The WhatsApp Connect job: install OpenWA (first time) → start it → open the session → QR. */
export interface GatewaySetup {
  phase: "idle" | "checking" | "installing" | "starting" | "connecting" | "qr" | "connected" | "error";
  detail: string;
  error: string;
  busy: boolean;
  log: string[];
  installed: boolean;
  running: boolean;
}

export type ContactMode = "disabled" | "suggest" | "auto" | "human_first";
export type CapLevel = "off" | "on" | "approval";

export interface Identity {
  id: string;
  channel_id: string;
  external_id: string;
  phone: string;
  display_name: string;
}

export interface WorkingHours { days?: number[]; start?: string; end?: string }

export interface Contact {
  id: string;
  display_name: string;
  mode: ContactMode;
  is_owner: boolean;
  paused: boolean;
  discovered: boolean;
  language: string;
  tone: string;
  timezone: string;
  working_hours: WorkingHours;
  share_memory: boolean;
  notes: string;
  last_interaction_at: number | null;
  created_at: number;
  identities: Identity[];
  conversations?: number;
  policies?: Record<string, Record<string, CapLevel>>;
  // communication profile
  relationship: "" | "family" | "friend" | "client" | "colleague" | "vendor" | "other";
  relationship_label: string;
  address_as: string;
  script: "" | "latin" | "devanagari";
  formality: "" | "casual" | "neutral" | "formal";
  greeting: string;
  intro_message: string;
  background: string;
  special_instructions: string;
  allowed_requests: string[];
  default_intro?: string;
}

export type RequestStatus = "new" | "information_required" | "waiting_for_owner" | "approved" | "in_progress" | "completed" | "rejected" | "cancelled";

export interface ContactRequest {
  id: number;
  code: string;
  contact_id: string | null;
  contact_name: string | null;
  channel_id: string;
  channel_conversation_id: string | null;
  type: string;
  type_label: string;
  summary: string;
  details: Record<string, string>;
  urgency: "low" | "normal" | "high" | "urgent";
  status: RequestStatus;
  required_action: string;
  missing: string;
  outcome: string;
  owner_notified: boolean;
  created_at: number;
  updated_at: number;
  event_id: string;
  event_start: string;
  event_end: string;
  meet_link: string;
  contact_informed?: boolean;
  booked?: { meet: string; start: string; end: string; moved: boolean };
}

export interface Capability { id: string; label: string; help: string; forced_approval: boolean }

export interface Memory { id: string; scope: "global" | "contact"; contact_id: string | null; content: string; source: string; created_at: number }

export interface InboxConversation {
  id: string;
  channel_id: string;
  contact_id: string;
  contact_name: string;
  contact_mode: ContactMode;
  contact_is_owner: boolean;
  external_chat_id: string;
  conversation_id: string;
  status: "active" | "archived";
  escalated: boolean;
  unread: number;
  summary: string;
  preview: string | null;
  failed: number;
  pending_approvals: number;
  last_message_at: number | null;
}

export interface ChannelMessage {
  id: string;
  event_id: string | null;
  direction: "in" | "out";
  author: "contact" | "donna" | "owner";
  message_type: string;
  content: string;
  attachments: { type: string; file?: string; filename?: string }[];  // file: a picture Donna made (/api/v1/images/…)
  status: string;
  error: string;
  created_at: number;
  event_status: string | null;
  event_result: string | null;
}

export interface Approval {
  id: string;
  kind: "tool" | "reply";
  audience: "owner" | "contact";
  channel_id: string;
  contact_id: string | null;
  contact_name?: string | null;
  channel_conversation_id: string | null;
  tool: string;
  args: Record<string, unknown>;
  description: string;
  status: "pending" | "executing" | "executed" | "failed" | "rejected" | "expired";
  result: string;
  expires_at: number;
  created_at: number;
  decided_at: number | null;
  decided_by: string;
}

export interface InboxCounts { unread: number; escalated: number; failed: number; approvals: number; requests: number }

export interface AuditRecord { id: number; at: number; actor: string; action: string; target: string; outcome: string; detail: Record<string, unknown> }

export interface Health {
  groq: "ok" | "no_key" | "invalid_key" | "unreachable" | "error";
  provider?: "groq" | "claude_code";
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
  deliver_contact_id: string | null;
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
  deliver_contact_id?: string | null;
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
  connectImages: (account_id: string, token: string) =>
    request<{ ok: boolean; image: string; model: string; seconds: number }>("POST", "/connections/images", { account_id, token }),
  disconnectImages: () => request("DELETE", "/connections/images"),
  imageModels: () => request<{ current: string; models: ImageModel[] }>("GET", "/images/models"),
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

  channels: () => request<Channel[]>("GET", "/channels"),
  channelStatus: (id: string) => request<Channel>("GET", `/channels/${id}/status`),
  connectChannel: (type: string) => request<Channel>("POST", `/channels/${type}/connect`),
  disconnectChannel: (id: string) => request<Channel>("POST", `/channels/${id}/disconnect`),
  forgetChannel: (id: string) => request<Channel>("DELETE", `/channels/${id}`),
  pauseChannel: (id: string, paused: boolean) => request<Channel>("POST", `/channels/${id}/${paused ? "pause" : "resume"}`),
  channelQr: (id: string) => request<{ status: string; qr?: string }>("GET", `/channels/${id}/qr`),
  whatsappSetup: () => request<GatewaySetup>("POST", "/channels/whatsapp/setup"),
  whatsappSetupState: () => request<GatewaySetup>("GET", "/channels/whatsapp/setup"),
  telegramToken: (token: string) => request<Channel>("PUT", "/channels/telegram/token", { token }),

  capabilities: () => request<Capability[]>("GET", "/capabilities"),
  contacts: (q = "", discovered?: boolean) => request<Contact[]>("GET", `/contacts?q=${encodeURIComponent(q)}${discovered === undefined ? "" : `&discovered=${discovered}`}`),
  contact: (id: string) => request<Contact>("GET", `/contacts/${id}`),
  createContact: (c: Partial<Contact> & { display_name: string; channel_id?: string; address?: string; is_owner?: boolean }) =>
    request<Contact>("POST", "/contacts", c),
  updateContact: (id: string, patch: Partial<Contact>) => request<Contact>("PATCH", `/contacts/${id}`, patch),
  deleteContact: (id: string) => request("DELETE", `/contacts/${id}`),
  makeOwner: (id: string) => request<Contact>("POST", `/contacts/${id}/owner`),
  setPolicy: (id: string, channel_id: string, capabilities: Record<string, CapLevel>) =>
    request<Record<string, Record<string, CapLevel>>>("PUT", `/contacts/${id}/policies`, { channel_id, capabilities }),
  clearPolicy: (id: string, channel_id: string) => request("DELETE", `/contacts/${id}/policies/${channel_id}`),
  addIdentity: (id: string, channel_id: string, address: string) => request<Contact>("POST", `/contacts/${id}/identities`, { channel_id, address }),
  removeIdentity: (id: string, identityId: string) => request<Contact>("DELETE", `/contacts/${id}/identities/${identityId}`),
  contactMemories: (id: string) => request<Memory[]>("GET", `/contacts/${id}/memories`),
  addContactMemory: (id: string, content: string) => request<Memory>("POST", `/contacts/${id}/memories`, { content }),
  deleteContactMemory: (id: string, mid: string) => request("DELETE", `/contacts/${id}/memories/${mid}`),

  inbox: (f: { channel_id?: string; contact_id?: string; status?: string; q?: string } = {}) =>
    request<InboxConversation[]>("GET", `/inbox?${new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])}`),
  inboxCounts: () => request<InboxCounts>("GET", "/inbox/counts"),
  inboxMessages: (id: string) => request<ChannelMessage[]>("GET", `/inbox/${id}/messages`),
  inboxSend: (id: string, text: string) => request("POST", `/inbox/${id}/send`, { text }),
  inboxArchive: (id: string, archived: boolean) => request<InboxConversation>("POST", `/inbox/${id}/archive`, { archived }),
  inboxSummarize: (id: string) => request<{ summary: string }>("POST", `/inbox/${id}/summarize`),
  inboxDelete: (id: string) => request("DELETE", `/inbox/${id}`),
  retryEvent: (eventId: string) => request("POST", `/inbox/events/${eventId}/retry`),

  requestTypes: () => request<{ types: Record<string, string>; statuses: RequestStatus[] }>("GET", "/requests/types"),
  requests: (f: { contact_id?: string; channel_id?: string; type?: string; status?: string } = {}) =>
    request<ContactRequest[]>("GET", `/requests?${new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][])}`),
  updateRequest: (id: number, patch: { status?: RequestStatus; outcome?: string; notify_contact?: boolean;
    book?: { start: string; end: string; title?: string; add_meet?: boolean } }) =>
    request<ContactRequest>("PATCH", `/requests/${id}`, patch),
  requestClash: (id: number) => request<Clash>("GET", `/requests/${id}/clash`),

  availability: () => request<Availability>("GET", "/availability"),
  setAvailability: (mode: StatusMode, detail = "", until: number | null = null) =>
    request<Availability>("PUT", "/availability", { mode, detail, until }),

  approvals: (status?: string) => request<Approval[]>("GET", `/approvals${status ? `?status=${status}` : ""}`),
  approve: (id: string, text?: string) => request<Approval>("POST", `/approvals/${id}/approve`, { text }),
  reject: (id: string) => request<Approval>("POST", `/approvals/${id}/reject`),

  memories: () => request<Memory[]>("GET", "/memories"),
  addMemory: (content: string) => request<Memory>("POST", "/memories", { content }),
  deleteMemory: (id: string) => request("DELETE", `/memories/${id}`),
  audit: (outcome?: string) => request<AuditRecord[]>("GET", `/audit${outcome ? `?outcome=${outcome}` : ""}`),
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
