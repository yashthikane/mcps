import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type PermissionMode, type ExecutionStatus, type RecurrenceType, type ScheduledTask, type SchedulerHealth, type TaskInput, type TaskStatus, type TaskType } from "../api";
import { Icon } from "../icons";
import { MODES } from "./Chat";
import { timezones } from "./Settings";
import { Dialog, DialogHead, Result, useToast } from "./ui";

/* ------------------------------------------------------------------ formatting */
const TASK_PILL: Record<TaskStatus, [string, string]> = {
  scheduled: ["ok", "Scheduled"], running: ["proc", "Running"], paused: ["off", "Paused"],
  completed: ["off", "Done"], cancelled: ["off", "Cancelled"], failed: ["err", "Failed"],
};
const EXEC_PILL: Record<ExecutionStatus, [string, string]> = {
  succeeded: ["ok", "Done"], failed: ["err", "Failed"], missed: ["warn", "Missed"], approval_required: ["warn", "Needs approval"],
  running: ["proc", "Running"], pending: ["proc", "Retrying"], skipped: ["off", "Skipped"],
};
const Pill = ({ map, s }: { map: Record<string, [string, string]>; s: string }) => {
  const [cls, label] = map[s] ?? ["off", s];
  return <span className={`pill ${cls}`}>{label}</span>;
};

export function fmtWhen(iso: string | null | undefined, tz: string): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  } catch {
    return new Date(iso).toLocaleString();
  }
}

function relative(iso: string | null): string {
  if (!iso) return "";
  const min = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (Math.abs(min) < 1) return "now";
  const abs = Math.abs(min);
  const txt = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
  return min > 0 ? `in ${txt}` : `${txt} ago`;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export function describeRule(t: Pick<ScheduledTask, "recurrence_type" | "recurrence_rule">): string {
  if (t.recurrence_type === "once") return "Once";
  if (t.recurrence_type === "interval") return `Every ${t.recurrence_rule}`;
  const m = /^(\d+) (\d+) \* \* (\*|[\d,-]+)$/.exec(t.recurrence_rule ?? "");
  if (!m) return `Cron ${t.recurrence_rule}`;
  const time = `${m[2].padStart(2, "0")}:${m[1].padStart(2, "0")}`;
  if (m[3] === "*") return `Daily at ${time}`;
  if (m[3] === "1-5") return `Weekdays at ${time}`;
  const days = m[3].split(",").map((d) => DAYS[Number(d) % 7] ?? d).join(", ");
  return `${days} at ${time}`;
}

/** "YYYY-MM-DDTHH:MM" wall-clock time of `date` in `tz` (the value a datetime-local input shows). */
function localInput(date: Date, tz: string): string {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/* ------------------------------------------------------------------ page */
export default function TasksPage({ defaultTimezone, newSignal, onOpenConversation }: {
  defaultTimezone: string; newSignal: number; onOpenConversation: (id: string) => void;
}) {
  const toast = useToast();
  const [tasks, setTasks] = useState<ScheduledTask[] | null>(null);
  const [health, setHealth] = useState<SchedulerHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ task?: ScheduledTask } | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Independent: the health check can take a couple of seconds while the scheduler is down.
    api.schedulerHealth().then(setHealth).catch(() => {});
    try {
      setTasks(await api.tasks());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => { if (!document.hidden) load(); }, 15000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => { if (newSignal) setEditor({}); }, [newSignal]);

  const act = async (fn: () => Promise<ScheduledTask | unknown>, ok: string) => {
    try {
      const r = await fn();
      const warning = (r as ScheduledTask | undefined)?.warning;
      toast(warning || ok, warning ? "warn" : "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    }
    load();
  };

  const list = tasks ?? [];
  const active = list.filter((t) => ["scheduled", "running", "paused"].includes(t.status));
  const next = active.filter((t) => t.next_run_at && t.status !== "paused")
    .sort((a, b) => a.next_run_at!.localeCompare(b.next_run_at!))[0];
  const attention = list.filter((t) => t.status === "failed" || t.last_status === "approval_required" || t.last_status === "failed").length;

  return (
    <div className="page">
      <div className="phead">
        <div>
          <span className="eyebrow"><span className="sq">■</span> Automation // scheduler</span>
          <h1>Tasks</h1>
          <p>Reminders and AI tasks that run on a schedule, even when this page is closed. Reminders don't use Groq. AI tasks run
            Donna unattended in the mode you pick: Plan (only plans), Manual (reads, flags actions for you) or Auto (acts without asking).</p>
        </div>
        <button className="btn btn-primary" onClick={() => setEditor({})} disabled={health !== null && health.postgres !== "ok"}>
          <Icon name="plus" />New task</button>
      </div>

      {health && !health.ok && (
        <div className="card sched-health" role="status">
          <Icon name="alert" />
          <div className="grow">
            <b>Scheduling isn't fully running.</b>
            <span className="help">{health.detail ?? "Some parts of the scheduler are down. Start Donna with scripts\\start.ps1."}
              {" "}Tasks you already scheduled are kept and run once it's back.</span>
          </div>
          <span className="sched-parts">
            {([["Scheduler", health.scheduler === "ok"], ["Redis", health.redis === "ok"], ["PostgreSQL", health.postgres === "ok"],
              ["Worker", health.worker === "running"]] as [string, boolean][]).map(([n, ok]) => (
              <span key={n} className={`pill ${ok ? "ok" : "err"}`}>{n}</span>
            ))}
          </span>
          <button className="btn btn-secondary btn-sm" onClick={load}><Icon name="refresh" />Retry</button>
        </div>
      )}

      <div className="stat-row">
        <Stat label="Active" value={String(active.length)} note={`${list.length} in total`} />
        <Stat label="Next run" value={next ? relative(next.next_run_at) : "—"} note={next ? next.title : "nothing scheduled"} />
        <Stat label="Recurring" value={String(active.filter((t) => t.recurrence_type !== "once").length)} note="interval or cron" />
        <Stat label="Need attention" value={String(attention)} note={attention ? "failed or needs approval" : "all good"} warn={attention > 0} />
      </div>

      <div className="sec-h"><h2>Your tasks</h2></div>
      {error && !tasks ? <Result kind="err">{error}</Result> : tasks === null ? <span className="meta">Loading tasks…</span> : list.length === 0 ? (
        <div className="card tasks-empty">
          <Icon name="clock" size={20} />
          <p>No scheduled tasks yet. Create one here, or ask in chat: <i>“remind me at 6 PM to call Mom”</i>, <i>“every weekday at 9 summarize my unread emails”</i>.</p>
        </div>
      ) : (
        <div className="tlist">
          {list.map((t) => (
            <TaskRow key={t.id} t={t} open={open === t.id} onToggle={() => setOpen(open === t.id ? null : t.id)}
              onEdit={() => setEditor({ task: t })} act={act} onOpenConversation={onOpenConversation} />
          ))}
        </div>
      )}

      {editor && <TaskEditor task={editor.task} defaultTimezone={defaultTimezone} onClose={() => setEditor(null)}
        onSaved={(t) => { setEditor(null); toast(t.warning || (editor.task ? "Task updated." : `Scheduled “${t.title}”.`), t.warning ? "warn" : "ok"); load(); }} />}
    </div>
  );
}

function Stat({ label, value, note, warn }: { label: string; value: string; note: string; warn?: boolean }) {
  return (
    <div className="stat"><span className="eyebrow">{label}</span><span className="num">{value}</span>
      <span className="chg" style={warn ? { color: "var(--amber)" } : undefined}>{note}</span></div>
  );
}

/* ------------------------------------------------------------------ row */
function TaskRow({ t, open, onToggle, onEdit, act, onOpenConversation }: {
  t: ScheduledTask; open: boolean; onToggle: () => void; onEdit: () => void;
  act: (fn: () => Promise<unknown>, ok: string) => Promise<void>; onOpenConversation: (id: string) => void;
}) {
  const [detail, setDetail] = useState<ScheduledTask | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => {
    if (open) api.task(t.id).then(setDetail).catch(() => setDetail(null));
  }, [open, t.id, t.last_run_at, t.status]);
  const live = t.status === "scheduled" || t.status === "running";

  return (
    <article className={`card trow${open ? " open" : ""}`}>
      <div className="trow-main">
        <span className="cc-ic"><Icon name={t.type === "ai_task" ? "spark" : "clock"} size={16} /></span>
        <button className="trow-title linkbtn" onClick={onToggle} aria-expanded={open}>
          <b>{t.title}</b>
          <span className="meta">{t.type === "ai_task" ? `AI task · ${MODES.find((m) => m.id === t.permission_mode)?.label ?? "Manual"} mode` : "Reminder"} · {describeRule(t)} · {t.timezone}</span>
        </button>
        <div className="trow-next">
          <span className="eyebrow">Next</span>
          <span className="mono">{t.status === "paused" ? "paused" : fmtWhen(t.next_run_at, t.timezone)}</span>
          {t.next_run_at && t.status !== "paused" && <span className="meta">{relative(t.next_run_at)}</span>}
        </div>
        <Pill map={TASK_PILL} s={t.status} />
      </div>
      {(t.last_status || t.last_error) && (
        <div className="trow-last">
          <span className="eyebrow">Last run</span>
          {t.last_status && <Pill map={EXEC_PILL} s={t.last_status} />}
          <span className="meta grow">{(t.last_error || t.last_result || "").slice(0, 160)}</span>
        </div>
      )}
      <div className="trow-actions">
        <button className="btn btn-secondary btn-sm" onClick={() => act(() => api.taskAction(t.id, "run"), "Queued. It runs in a moment.")}><Icon name="bolt" />Run now</button>
        {live && [15, 60].map((m) => (
          <button key={m} className="btn btn-ghost btn-sm" onClick={() => act(() => api.snoozeTask(t.id, m), `Snoozed ${m < 60 ? `${m} min` : "1 hour"}.`)}>
            +{m < 60 ? `${m}m` : "1h"}</button>
        ))}
        {live && <button className="btn btn-ghost btn-sm" onClick={() => act(() => api.taskAction(t.id, "pause"), "Paused.")}>Pause</button>}
        {t.status === "paused" && <button className="btn btn-ghost btn-sm" onClick={() => act(() => api.taskAction(t.id, "resume"), "Resumed.")}>Resume</button>}
        <button className="btn btn-ghost btn-sm" onClick={onEdit}><Icon name="pen" />Edit</button>
        {(live || t.status === "paused") && <button className="btn btn-ghost btn-sm" onClick={() => act(() => api.taskAction(t.id, "cancel"), "Cancelled.")}>Cancel task</button>}
        <span className="grow" />
        {confirmDelete ? (<>
          <span className="help">Delete it and its history?</span>
          <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>Keep</button>
          <button className="btn btn-danger btn-sm" onClick={() => act(() => api.deleteTask(t.id), "Deleted.")}>Delete</button>
        </>) : <button className="btn btn-ghost btn-icon btn-sm" aria-label={`Delete ${t.title}`} onClick={() => setConfirmDelete(true)}><Icon name="trash" /></button>}
        <button className="btn btn-ghost btn-icon btn-sm" aria-label={open ? "Hide history" : "Show history"} onClick={onToggle}>
          <Icon name="chevron" className={open ? "rot90" : ""} /></button>
      </div>
      {open && (
        <div className="trow-hist">
          {t.type === "ai_task" ? <p className="help"><b>Prompt:</b> {t.prompt}</p> : t.message && t.message !== t.title && <p className="help"><b>Message:</b> {t.message}</p>}
          {!detail ? <span className="meta">Loading history…</span> : !detail.executions?.length ? <span className="meta">No runs yet.</span> : (
            <div className="toolrows">
              {detail.executions.map((e) => (
                <div key={e.id} className="toolrow">
                  <span className="mono hist-when">{fmtWhen(e.scheduled_for, t.timezone)}</span>
                  <Pill map={EXEC_PILL} s={e.status} />
                  {e.trigger === "manual" && <span className="tag">manual</span>}
                  {e.attempts > 1 && <span className="tag">{e.attempts} attempts</span>}
                  <span className="d grow">{(e.error || e.result || "").slice(0, 220)}</span>
                  {e.conversation_id && <button className="btn btn-ghost btn-sm" onClick={() => onOpenConversation(e.conversation_id!)}>Open chat</button>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

/* ------------------------------------------------------------------ editor */
const CRON_PRESETS: [string, string][] = [["Daily 09:00", "0 9 * * *"], ["Weekdays 09:00", "0 9 * * 1-5"], ["Mondays 09:00", "0 9 * * 1"], ["Daily 18:00", "0 18 * * *"]];
const INTERVAL_PRESETS = ["15m", "30m", "1h", "4h", "1d"];

function TaskEditor({ task, defaultTimezone, onClose, onSaved }: {
  task?: ScheduledTask; defaultTimezone: string; onClose: () => void; onSaved: (t: ScheduledTask) => void;
}) {
  const initial = useMemo(() => {
    const tz = task?.timezone || defaultTimezone || "UTC";
    const start = task?.run_at ? new Date(task.run_at) : new Date(Math.ceil((Date.now() + 30 * 60000) / 900000) * 900000);
    return {
      type: task?.type ?? ("reminder" as TaskType), title: task?.title ?? "", message: task?.message ?? "", prompt: task?.prompt ?? "",
      timezone: tz, run_at: localInput(start, tz), recurrence_type: task?.recurrence_type ?? ("once" as RecurrenceType),
      recurrence_rule: task?.recurrence_rule ?? "", channels: task?.notification_channels ?? ["windows", "in_app"],
      permission_mode: task?.permission_mode ?? ("manual" as PermissionMode),
    };
  }, [task, defaultTimezone]);
  const [f, setF] = useState(initial);
  const [runs, setRuns] = useState<{ ok: boolean; items: string[]; error?: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const zones = useMemo(() => timezones(f.timezone), [f.timezone]);
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }));

  const schedule = { run_at: f.run_at || null, timezone: f.timezone, recurrence_type: f.recurrence_type,
    recurrence_rule: f.recurrence_type === "once" ? null : f.recurrence_rule.trim() || null };
  const key = JSON.stringify(schedule);
  useEffect(() => {
    if (f.recurrence_type !== "once" && !schedule.recurrence_rule) { setRuns(null); return; }
    const timer = setTimeout(() => {
      api.previewTask(schedule).then((r) => setRuns({ ok: true, items: r.runs }))
        .catch((e) => setRuns({ ok: false, items: [], error: (e as Error).message }));
    }, 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const valid = f.title.trim() && (f.type === "reminder" || f.prompt.trim()) && f.channels.length > 0
    && (f.recurrence_type === "once" ? f.run_at : f.recurrence_rule.trim()) && runs?.ok !== false;

  const save = async () => {
    setSaving(true);
    setErr(null);
    const body: TaskInput = {
      title: f.title.trim(), type: f.type, message: f.message.trim(), prompt: f.prompt.trim(), timezone: f.timezone,
      run_at: f.run_at || null, recurrence_type: f.recurrence_type, recurrence_rule: schedule.recurrence_rule, notification_channels: f.channels,
      permission_mode: f.permission_mode,
    };
    try {
      if (task) {
        // Only send what changed: a new time or rule reschedules; a new title doesn't.
        const patch: Partial<TaskInput> = {};
        const was: Record<string, unknown> = { ...initial, notification_channels: initial.channels,
          recurrence_rule: initial.recurrence_type === "once" ? null : initial.recurrence_rule || null, run_at: initial.run_at || null };
        for (const [k, v] of Object.entries(body) as [keyof TaskInput, unknown][]) {
          if (JSON.stringify(v) !== JSON.stringify(was[k])) (patch as Record<string, unknown>)[k] = v;
        }
        if (patch.recurrence_type || patch.recurrence_rule !== undefined || patch.timezone) Object.assign(patch, { run_at: body.run_at });
        onSaved(await api.updateTask(task.id, patch));
      } else {
        onSaved(await api.createTask(body));
      }
    } catch (e) {
      setErr((e as Error).message);
      setSaving(false);
    }
  };

  const recurrence: [RecurrenceType, string][] = [["once", "Once"], ["interval", "Interval"], ["cron", "Schedule (cron)"]];
  const rIndex = recurrence.findIndex(([r]) => r === f.recurrence_type);
  return (
    <Dialog open onClose={onClose} labelledBy="task-title" className="task-dlg">
      <div className="dlg">
        <DialogHead id="task-title" eyebrow="■ Scheduler // task" title={task ? "Edit task" : "New task"} onClose={onClose} />
        <div className="dlg-body">
          <div className="seg" role="radiogroup" aria-label="Task type">
            <span className="ind" aria-hidden="true" style={{ width: "calc(50% - 3px)", transform: `translateX(${f.type === "ai_task" ? "100%" : 0})` }} />
            <button role="radio" aria-checked={f.type === "reminder"} onClick={() => set({ type: "reminder" })}>Reminder · no AI</button>
            <button role="radio" aria-checked={f.type === "ai_task"} onClick={() => set({ type: "ai_task" })}>AI task · runs Donna</button>
          </div>
          <div className="field"><label htmlFor="t-title">Title</label>
            <input className="input" id="t-title" value={f.title} maxLength={200} autoFocus placeholder={f.type === "ai_task" ? "Morning inbox summary" : "Call Mom"}
              onChange={(e) => set({ title: e.target.value })} /></div>
          {f.type === "reminder" ? (
            <div className="field"><label htmlFor="t-msg">Notification text <span className="meta">(optional)</span></label>
              <input className="input" id="t-msg" value={f.message} placeholder="Defaults to the title" onChange={(e) => set({ message: e.target.value })} /></div>
          ) : (
            <div className="field"><label htmlFor="t-prompt">What should Donna do?</label>
              <textarea className="input textarea" id="t-prompt" rows={3} value={f.prompt} placeholder="Check my unread emails and summarize anything important."
                onChange={(e) => set({ prompt: e.target.value })} />
            </div>
          )}
          {f.type === "ai_task" && (<>
            <span className="label">When it runs, Donna may…</span>
            <div className="seg" role="radiogroup" aria-label="Permission mode">
              <span className="ind" aria-hidden="true" style={{ width: "calc(33.333% - 2px)", transform: `translateX(${MODES.findIndex((m) => m.id === f.permission_mode) * 100}%)` }} />
              {MODES.map((m) => (
                <button key={m.id} role="radio" aria-checked={f.permission_mode === m.id} onClick={() => set({ permission_mode: m.id })}>
                  {m.id === "plan" ? "Plan only" : m.id === "manual" ? "Read · flag actions" : "Act (Auto)"}</button>
              ))}
            </div>
            <span className={`help${f.permission_mode === "auto" ? " st-warn" : ""}`}>
              {f.permission_mode === "plan" ? "Only reads and writes a plan into the task's chat. Nothing is sent, changed or deleted."
                : f.permission_mode === "manual" ? "Reads and summarizes. Actions that send, change or delete are skipped and flagged for you to do from chat."
                : "Runs every action without asking, including sending email and deleting. Use it for tasks you trust, like a scheduled email."}
            </span>
          </>)}

          <span className="label">Repeat</span>
          <div className="seg" role="radiogroup" aria-label="Repeat">
            <span className="ind" aria-hidden="true" style={{ width: "calc(33.333% - 2px)", transform: `translateX(${rIndex * 100}%)` }} />
            {recurrence.map(([r, label]) => (
              <button key={r} role="radio" aria-checked={f.recurrence_type === r} onClick={() => set({ recurrence_type: r, recurrence_rule:
                r === "once" ? "" : r === f.recurrence_type ? f.recurrence_rule : r === "interval" ? "1h" : "0 9 * * *" })}>{label}</button>
            ))}
          </div>
          {f.recurrence_type === "interval" && (
            <div className="field"><label htmlFor="t-rule">Every</label>
              <div className="srow"><input className="input mono" id="t-rule" value={f.recurrence_rule} placeholder="30m, 2h or 1d" onChange={(e) => set({ recurrence_rule: e.target.value })} />
                {INTERVAL_PRESETS.map((p) => <button key={p} className="btn btn-ghost btn-sm" onClick={() => set({ recurrence_rule: p })}>{p}</button>)}</div>
            </div>
          )}
          {f.recurrence_type === "cron" && (
            <div className="field"><label htmlFor="t-cron">Cron rule <span className="meta">(minute hour day month weekday, in the timezone below)</span></label>
              <input className="input mono" id="t-cron" value={f.recurrence_rule} placeholder="0 9 * * 1-5" onChange={(e) => set({ recurrence_rule: e.target.value })} />
              <div className="srow wrap">{CRON_PRESETS.map(([label, rule]) => <button key={rule} className="btn btn-ghost btn-sm" onClick={() => set({ recurrence_rule: rule })}>{label}</button>)}</div>
            </div>
          )}
          <div className="two">
            <div className="field"><label htmlFor="t-when">{f.recurrence_type === "once" ? "When" : f.recurrence_type === "interval" ? "First run" : "Starting from"}</label>
              <input className="input mono" id="t-when" type="datetime-local" value={f.run_at} onChange={(e) => set({ run_at: e.target.value })} /></div>
            <div className="field"><label htmlFor="t-tz">Timezone</label>
              <select className="input select" id="t-tz" value={f.timezone} onChange={(e) => set({ timezone: e.target.value })}>
                {zones.map((z) => <option key={z} value={z}>{z}</option>)}
              </select></div>
          </div>
          <div className="field"><span className="label">Notify me with</span>
            <div className="srow">
              {([["windows", "Windows notification"], ["in_app", "In Donna"]] as [string, string][]).map(([c, label]) => (
                <label key={c} className="chk"><input type="checkbox" checked={f.channels.includes(c)}
                  onChange={(e) => set({ channels: e.target.checked ? [...f.channels, c] : f.channels.filter((x) => x !== c) })} />{label}</label>
              ))}
            </div>
          </div>
          {runs && (runs.ok ? (
            <div className="runs"><span className="eyebrow">Next runs</span>
              {runs.items.map((r) => <span key={r} className="mono">{fmtWhen(r, f.timezone)}</span>)}</div>
          ) : <Result kind="err">{runs.error}</Result>)}
          {err && <Result kind="err">{err}</Result>}
        </div>
        <div className="dlg-foot">
          <span className="meta grow">{f.timezone}</span>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={!valid || saving}>{saving ? "Saving…" : task ? "Save changes" : "Schedule"}</button>
        </div>
      </div>
    </Dialog>
  );
}
