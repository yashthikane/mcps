import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Approval, type AuditRecord, type Channel, type ChannelMessage, type ContactRequest, type InboxConversation, type RequestStatus } from "../api";
import { Icon } from "../icons";
import { Result, timeAgo, useToast } from "./ui";

/* ------------------------------------------------------------------ labels */
const MODE_LABEL: Record<string, string> = { disabled: "Disabled", suggest: "Suggest", auto: "Auto reply", human_first: "Human first" };
const IN_PILL: Record<string, [string, string]> = {
  received: ["proc", "Received"], processing: ["proc", "Thinking"], answered: ["ok", "Answered"], drafted: ["warn", "Draft waiting"],
  escalated: ["warn", "For you"], ignored: ["off", "Ignored"], failed: ["err", "Failed"],
};
const OUT_PILL: Record<string, [string, string]> = {
  queued: ["proc", "Sending"], sent: ["ok", "Sent"], delivered: ["ok", "Delivered"], read: ["ok", "Read"], failed: ["err", "Not sent"], unconfirmed: ["warn", "Unconfirmed"],
};
const APPROVAL_PILL: Record<string, [string, string]> = {
  pending: ["warn", "Waiting"], executing: ["proc", "Running"], executed: ["ok", "Done"], failed: ["err", "Failed"],
  rejected: ["off", "Rejected"], expired: ["off", "Expired"],
};
const Pill = ({ map, s }: { map: Record<string, [string, string]>; s: string }) => {
  const [cls, label] = map[s] ?? ["off", s];
  return <span className={`pill ${cls}`}>{label}</span>;
};
const chanLabel = (id: string) => (id === "whatsapp" ? "WhatsApp" : id === "telegram" ? "Telegram" : id);
const until = (t: number) => {
  const m = Math.round((t - Date.now() / 1000) / 60);
  return m <= 0 ? "expired" : m < 60 ? `${m} min left` : `${Math.round(m / 60)} h left`;
};

type Tab = "requests" | "chats" | "approvals" | "log";

export default function InboxPage({ onCounts }: { onCounts: () => void }) {
  const [tab, setTab] = useState<Tab>("requests");
  const [channels, setChannels] = useState<Channel[]>([]);
  useEffect(() => { api.channels().then(setChannels).catch(() => {}); }, []);
  const connected = channels.filter((c) => c.enabled);
  const tabs: [Tab, string][] = [["requests", "Requests"], ["chats", "Conversations"], ["approvals", "Approvals"], ["log", "Activity log"]];
  const idx = tabs.findIndex(([t]) => t === tab);

  return (
    <div className="page">
      <div className="phead">
        <div>
          <span className="eyebrow"><span className="sq">■</span> Channels // inbox</span>
          <h1>Inbox</h1>
          <p>Everything people send Donna on WhatsApp and Telegram, what Donna answered, and the actions waiting for your OK.
            Only contacts you enabled get answers; everyone else is stored here and ignored.</p>
        </div>
      </div>
      {connected.length === 0 && (
        <div className="card sched-health" role="status">
          <Icon name="plug" />
          <div className="grow"><b>No messaging channel is connected.</b>
            <span className="help">Connect WhatsApp or Telegram under Connections → Messaging channels.</span></div>
        </div>
      )}
      <div className="seg inbox-tabs" role="tablist" aria-label="Inbox sections">
        <span className="ind" aria-hidden="true" style={{ width: "calc(25% - 2px)", transform: `translateX(${idx * 100}%)` }} />
        {tabs.map(([t, label]) => <button key={t} role="tab" aria-selected={tab === t} aria-checked={tab === t} onClick={() => setTab(t)}>{label}</button>)}
      </div>
      {tab === "requests" && <Requests channels={channels} onCounts={onCounts} />}
      {tab === "chats" && <Conversations channels={channels} onCounts={onCounts} />}
      {tab === "approvals" && <Approvals onCounts={onCounts} />}
      {tab === "log" && <ActivityLog />}
    </div>
  );
}

/* ------------------------------------------------------------------ conversations */
function Conversations({ channels, onCounts }: { channels: Channel[]; onCounts: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<InboxConversation[] | null>(null);
  const [filter, setFilter] = useState({ channel_id: "", status: "active", q: "" });
  const [active, setActive] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setItems(await api.inbox(filter)); } catch (e) { toast((e as Error).message, "err"); }
  }, [filter, toast]);
  useEffect(() => {
    const t = setTimeout(load, filter.q ? 250 : 0);
    const poll = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => { clearTimeout(t); clearInterval(poll); };
  }, [load, filter.q]);
  const current = items?.find((c) => c.id === active) ?? null;

  return (
    <div className="inbox">
      <aside className="card inbox-list">
        <div className="inbox-filters">
          <input className="input" placeholder="Search name or text" value={filter.q} aria-label="Search conversations"
            onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
          <div className="srow">
            <select className="select" aria-label="Channel" value={filter.channel_id} onChange={(e) => setFilter({ ...filter, channel_id: e.target.value })}>
              <option value="">All channels</option>
              {channels.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
            <select className="select" aria-label="Status" value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
              <option value="active">Active</option><option value="archived">Archived</option><option value="any">All</option>
            </select>
          </div>
        </div>
        {items === null ? <span className="meta">Loading…</span> : items.length === 0 ? (
          <p className="meta inbox-empty">No conversations yet. When someone messages your WhatsApp or Telegram, it shows up here.</p>
        ) : items.map((c) => (
          <button key={c.id} className={`inbox-item${c.id === active ? " on" : ""}`} onClick={() => setActive(c.id)}>
            <span className="inbox-top">
              <b>{c.contact_name}{c.contact_is_owner && <span className="tag">you</span>}</b>
              <span className="meta">{c.last_message_at ? timeAgo(c.last_message_at) : ""}</span>
            </span>
            <span className="meta inbox-prev">{c.preview || "—"}</span>
            <span className="inbox-tags">
              <span className="tag">{chanLabel(c.channel_id)}</span>
              {!c.contact_is_owner && <span className="tag">{MODE_LABEL[c.contact_mode]}</span>}
              {c.unread > 0 && <span className="pill live">{c.unread} new</span>}
              {c.escalated && <span className="pill warn">For you</span>}
              {c.pending_approvals > 0 && <span className="pill warn">{c.pending_approvals} to approve</span>}
              {c.failed > 0 && <span className="pill err">{c.failed} failed</span>}
            </span>
          </button>
        ))}
      </aside>
      {current ? <Thread key={current.id} conv={current} reload={() => { load(); onCounts(); }} onGone={() => { setActive(null); load(); }} />
        : <div className="card inbox-thread inbox-none"><Icon name="chat" size={20} /><p className="meta">Pick a conversation.</p></div>}
    </div>
  );
}

function Thread({ conv, reload, onGone }: { conv: InboxConversation; reload: () => void; onGone: () => void }) {
  const toast = useToast();
  const [msgs, setMsgs] = useState<ChannelMessage[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => {
    try { setMsgs(await api.inboxMessages(conv.id)); } catch (e) { toast((e as Error).message, "err"); }
  }, [conv.id, toast]);
  useEffect(() => {
    load().then(reload);
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs?.length]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); } catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
    load();
    reload();
  };
  const send = () => act(async () => { await api.inboxSend(conv.id, text); setText(""); }, "Sent.");

  return (
    <section className="card inbox-thread">
      <header className="inbox-head">
        <div className="grow">
          <b>{conv.contact_name}</b>
          <span className="meta">{chanLabel(conv.channel_id)} · <span className="mono">{conv.external_chat_id}</span>
            {!conv.contact_is_owner && <> · {MODE_LABEL[conv.contact_mode]}</>}</span>
        </div>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act(() => api.inboxSummarize(conv.id), "Summary updated.")}>
          <Icon name="brain" />Summarize</button>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => act(() => api.inboxArchive(conv.id, conv.status !== "archived"),
          conv.status === "archived" ? "Moved back to active." : "Archived.")}>{conv.status === "archived" ? "Unarchive" : "Archive"}</button>
        {confirmDelete ? (<>
          <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>Keep</button>
          <button className="btn btn-danger btn-sm" onClick={async () => { await api.inboxDelete(conv.id); toast("Deleted."); onGone(); }}>Delete</button>
        </>) : <button className="btn btn-ghost btn-icon btn-sm" aria-label="Delete conversation" onClick={() => setConfirmDelete(true)}><Icon name="trash" /></button>}
      </header>
      {conv.summary && <details className="nres inbox-summary"><summary className="meta">Summary of earlier messages</summary><pre>{conv.summary}</pre></details>}
      <div className="inbox-msgs">
        {msgs === null ? <span className="meta">Loading…</span> : msgs.map((m) => (
          <div key={m.id} className={`bubble ${m.direction === "in" ? "b-in" : m.author === "owner" ? "b-owner" : "b-out"}`}>
            <div className="b-meta">
              <span>{m.direction === "in" ? conv.contact_name : m.author === "owner" ? "You" : "Donna"}</span>
              <span>{timeAgo(m.created_at)}</span>
              {m.message_type !== "text" && <span className="tag">{m.message_type}</span>}
              <Pill map={m.direction === "in" ? IN_PILL : OUT_PILL} s={m.status} />
            </div>
            <div className="b-text">{m.content || <i className="meta">(no text)</i>}</div>
            {(m.error || (m.event_status === "failed" && m.event_result)) && (
              <div className="b-err">
                <span className="st-err">{m.error || m.event_result}</span>
                {m.event_id && m.event_status === "failed" &&
                  <button className="btn btn-secondary btn-sm" onClick={() => act(() => api.retryEvent(m.event_id!), "Retrying…")}>Retry</button>}
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="inbox-compose">
        <textarea className="input textarea" rows={2} value={text} placeholder={`Reply to ${conv.contact_name} yourself (sent as you, not Donna)`}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) send(); }} />
        <button className="btn btn-primary" disabled={!text.trim() || busy} onClick={send}><Icon name="send" />Send</button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ requests */
const REQ_PILL: Record<string, [string, string]> = {
  new: ["live", "New"], information_required: ["proc", "Collecting details"], waiting_for_owner: ["warn", "Waiting for you"],
  approved: ["ok", "Approved"], in_progress: ["proc", "In progress"], completed: ["ok", "Completed"],
  rejected: ["off", "Rejected"], cancelled: ["off", "Cancelled"],
};
const OWNER_STATUSES: [RequestStatus, string][] = [["approved", "Approve"], ["in_progress", "In progress"],
  ["completed", "Completed"], ["rejected", "Reject"], ["cancelled", "Cancel"]];

function Requests({ channels, onCounts }: { channels: Channel[]; onCounts: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<ContactRequest[] | null>(null);
  const [types, setTypes] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState({ status: "open", type: "", channel_id: "", contact_id: "" });
  const load = useCallback(async () => {
    try { setItems(await api.requests(filter)); } catch (e) { toast((e as Error).message, "err"); }
  }, [filter, toast]);
  useEffect(() => { api.requestTypes().then((r) => setTypes(r.types)).catch(() => {}); }, []);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => clearInterval(t);
  }, [load]);
  const contactsSeen = [...new Map((items ?? []).filter((r) => r.contact_id).map((r) => [r.contact_id!, r.contact_name ?? "?"])).entries()];

  return (
    <>
      <div className="srow wrap req-filters">
        <select className="select" aria-label="Status" value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
          <option value="open">Open</option><option value="">All</option>
          {Object.keys(REQ_PILL).map((s) => <option key={s} value={s}>{REQ_PILL[s][1]}</option>)}
        </select>
        <select className="select" aria-label="Type" value={filter.type} onChange={(e) => setFilter({ ...filter, type: e.target.value })}>
          <option value="">All types</option>{Object.entries(types).map(([t, l]) => <option key={t} value={t}>{l}</option>)}
        </select>
        <select className="select" aria-label="Channel" value={filter.channel_id} onChange={(e) => setFilter({ ...filter, channel_id: e.target.value })}>
          <option value="">All channels</option>{channels.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <select className="select" aria-label="Contact" value={filter.contact_id} onChange={(e) => setFilter({ ...filter, contact_id: e.target.value })}>
          <option value="">All contacts</option>{contactsSeen.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
        </select>
      </div>
      {items === null ? <span className="meta">Loading…</span> : items.length === 0 ? (
        <div className="card tasks-empty"><Icon name="chat" size={20} /><p>No requests here. When someone asks Donna for a callback, a
          meeting or sends an enquiry, she records it here with the details and tells you.</p></div>
      ) : (
        <div className="tlist">{items.map((r) => <RequestRow key={r.id} r={r} reload={() => { load(); onCounts(); }} />)}</div>
      )}
    </>
  );
}

function RequestRow({ r, reload }: { r: ContactRequest; reload: () => void }) {
  const toast = useToast();
  const [outcome, setOutcome] = useState(r.outcome);
  const [notify, setNotify] = useState(true);
  const [busy, setBusy] = useState(false);
  const open = !["completed", "rejected", "cancelled"].includes(r.status);
  const set = async (status: RequestStatus) => {
    setBusy(true);
    try {
      const res = await api.updateRequest(r.id, { status, outcome, notify_contact: notify });
      toast(`${r.code}: ${REQ_PILL[status][1]}.${notify ? (res.contact_informed ? ` Donna told ${r.contact_name}.` : " Couldn't message them.") : ""}`,
        notify && !res.contact_informed ? "warn" : "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    }
    setBusy(false);
    reload();
  };
  return (
    <article className={`card trow req-row u-${r.urgency}`}>
      <div className="trow-main">
        <span className="cc-ic"><Icon name={r.type === "callback" ? "send" : r.type === "meeting" ? "cal" : "chat"} size={16} /></span>
        <div className="trow-title">
          <b>{r.type_label} · {r.contact_name ?? "Unknown"}</b>
          <span className="meta">{r.summary} · {chanLabel(r.channel_id)} · {timeAgo(r.created_at)} · <span className="mono">{r.code}</span></span>
        </div>
        {r.urgency !== "normal" && <span className={`pill ${r.urgency === "urgent" || r.urgency === "high" ? "err" : "off"}`}>{r.urgency}</span>}
        <Pill map={REQ_PILL} s={r.status} />
      </div>
      {(Object.keys(r.details).length > 0 || r.missing) && (
        <div className="kv req-details">
          {Object.entries(r.details).map(([k, v]) => <div key={k}><span className="meta">{k.replace(/_/g, " ")}</span><span>{v}</span></div>)}
          {r.missing && <div><span className="meta">still asking</span><span className="st-warn">{r.missing}</span></div>}
        </div>
      )}
      {r.type === "meeting" && (open || (!!r.event_id && r.status === "completed")) && <BookMeeting r={r} outcome={outcome} notify={notify} reload={reload} />}
      {open ? (<>
        <input className="input" placeholder="Outcome or note for them (optional), e.g. I'll call you at 6 PM" value={outcome}
          onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome" />
        <div className="trow-actions">
          {OWNER_STATUSES.map(([s, label]) => (
            <button key={s} className={`btn btn-sm ${s === "completed" || s === "approved" ? "btn-primary" : "btn-ghost"}`} disabled={busy}
              onClick={() => set(s)}>{label}</button>
          ))}
          <span className="grow" />
          <label className="chk"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />
            Let Donna tell {r.contact_name ?? "them"}</label>
        </div>
      </>) : r.outcome && <p className="help"><b>Outcome:</b> {r.outcome}</p>}
    </article>
  );
}

/** "YYYY-MM-DDTHH:MM" (local wall-clock) plus minutes. */
function addMinutes(local: string, minutes: number): string {
  const [d, t] = local.split("T");
  const [y, mo, da] = d.split("-").map(Number);
  const [h, mi] = t.split(":").map(Number);
  const x = new Date(y, mo - 1, da, h, mi + minutes);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${x.getFullYear()}-${p(x.getMonth() + 1)}-${p(x.getDate())}T${p(x.getHours())}:${p(x.getMinutes())}`;
}

/** "2026-11-22T19:00" → "Sun 22 Nov, 7:00 PM" (local wall-clock, as said to the contact). */
function human(local: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!m) return local;
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** Book a meeting request, or move it when it's already booked (same event, same Meet link). Donna then sends the
 * contact the exact time and link as the calendar saved them. Prefilled only from the exact time they asked for:
 * no guessed default, and a time that differs from their request needs a second, deliberate click. */
function BookMeeting({ r, outcome, notify, reload }: { r: ContactRequest; outcome: string; notify: boolean; reload: () => void }) {
  const toast = useToast();
  const asked = r.details.start || Object.values(r.details).join(" ").match(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/)?.[0] || "";
  const booked = !!r.event_id;
  const [start, setStart] = useState(asked);
  const [minutes, setMinutes] = useState(Number(r.details.duration_minutes) || 30);
  const [title, setTitle] = useState(`Meeting with ${r.contact_name ?? "contact"}`);
  const [meet, setMeet] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmDiff, setConfirmDiff] = useState(false);
  const differs = !!asked && !!start && start.slice(0, 16) !== asked.slice(0, 16);
  const book = async () => {
    if (differs && !confirmDiff) { setConfirmDiff(true); return; }
    setBusy(true);
    try {
      const res = await api.updateRequest(r.id, { status: "completed", outcome, notify_contact: notify,
        book: { start, end: addMinutes(start, minutes), title, add_meet: meet } });
      const at = res.booked ? human(res.booked.start) : human(start);
      toast(`${res.booked?.moved ? "Moved to" : "Booked for"} ${at}${res.booked?.meet ? " (Meet link included)" : ""}.`
        + (notify ? (res.contact_informed ? ` Donna told ${r.contact_name}.` : " Couldn't message them.") : ""),
        notify && !res.contact_informed ? "warn" : "ok");
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
    setBusy(false);
    setConfirmDiff(false);
  };
  return (
    <div className="book-box">
      <span className="label">{booked ? "Move the booked meeting" : "Book it on your calendar"}</span>
      <span className="help">
        {booked && <>Booked now: <b>{human(r.event_start)}</b>{r.meet_link ? " · same Meet link is kept" : ""}. </>}
        {asked ? <>{r.contact_name} asked for <b>{human(asked)}</b>.</> : <>{r.contact_name} didn't give an exact time: pick one.</>}
      </span>
      <div className="srow wrap">
        <input className="input" aria-label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input className="input mono" type="datetime-local" aria-label="Start" value={start}
          onChange={(e) => { setStart(e.target.value); setConfirmDiff(false); }} />
        <select className="select" aria-label="Duration" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
          {[15, 30, 45, 60, 90].map((m) => <option key={m} value={m}>{m} min</option>)}</select>
        {!booked && <label className="chk"><input type="checkbox" checked={meet} onChange={(e) => setMeet(e.target.checked)} />Google Meet link</label>}
        <button className={`btn btn-sm ${confirmDiff ? "btn-danger" : "btn-primary"}`} disabled={busy || !start} onClick={book}><Icon name="cal" />
          {confirmDiff ? `Yes, ${human(start)}` : booked ? (notify ? `Move & tell ${r.contact_name ?? "them"}` : "Move")
            : (notify ? `Book & tell ${r.contact_name ?? "them"}` : "Book")}</button>
      </div>
      {start && <span className="help">{booked ? "Will move to" : "Will book"} <b>{human(start)}</b> to {human(addMinutes(start, minutes)).split(", ").pop()}.</span>}
      {differs && <span className="help st-warn">That's not the time {r.contact_name} asked for ({human(asked)}).
        {confirmDiff ? " Click again to book it anyway." : ""}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ approvals */
function Approvals({ onCounts }: { onCounts: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<Approval[] | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setItems(await api.approvals()); } catch (e) { toast((e as Error).message, "err"); }
  }, [toast]);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => clearInterval(t);
  }, [load]);

  const decide = async (a: Approval, ok: boolean) => {
    setBusy(a.id);
    try {
      const r = ok ? await api.approve(a.id, a.kind === "reply" ? edits[a.id] : undefined) : await api.reject(a.id);
      toast(ok ? (r.status === "executed" ? `Done: ${r.result.slice(0, 120) || a.description}` : `Couldn't do it: ${r.result}`) : "Rejected.",
        ok && r.status !== "executed" ? "err" : "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    }
    setBusy(null);
    load();
    onCounts();
  };
  const pending = (items ?? []).filter((a) => a.status === "pending");
  const done = (items ?? []).filter((a) => a.status !== "pending");

  return (
    <>
      <div className="sec-h"><h2>Waiting for you</h2></div>
      {items === null ? <span className="meta">Loading…</span> : pending.length === 0 ? (
        <div className="card tasks-empty"><Icon name="shield" size={20} /><p>Nothing to approve. Actions contacts ask for that change something
          (booking a meeting, sending an email) and replies Donna drafts in Suggest mode wait here. You can also answer
          “approve CODE” from your own WhatsApp or Telegram.</p></div>
      ) : (
        <div className="tlist">
          {pending.map((a) => (
            <article key={a.id} className="card trow approval-row">
              <div className="trow-main">
                <span className="cc-ic"><Icon name={a.kind === "reply" ? "chat" : "bolt"} size={16} /></span>
                <div className="trow-title">
                  <b>{a.kind === "reply" ? `Reply to ${a.contact_name ?? "contact"}` : a.description}</b>
                  <span className="meta">{a.audience === "owner" ? "You asked" : `${a.contact_name ?? "A contact"} asked`} on {chanLabel(a.channel_id)}
                    {" · "}{timeAgo(a.created_at)} · code <span className="mono">{a.id}</span></span>
                </div>
                <span className="pill warn">{until(a.expires_at)}</span>
              </div>
              {a.kind === "reply" ? (
                <textarea className="input textarea" rows={3} aria-label="Reply text" value={edits[a.id] ?? String(a.args.text ?? "")}
                  onChange={(e) => setEdits({ ...edits, [a.id]: e.target.value })} />
              ) : (
                <pre className="approval-args">{JSON.stringify(a.args, null, 2)}</pre>
              )}
              <div className="trow-actions">
                <button className="btn btn-primary btn-sm" disabled={busy === a.id} onClick={() => decide(a, true)}>
                  <Icon name="check" />{a.kind === "reply" ? (edits[a.id] !== undefined ? "Send edited reply" : "Send reply") : "Approve and run"}</button>
                <button className="btn btn-ghost btn-sm" disabled={busy === a.id} onClick={() => decide(a, false)}>Reject</button>
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="sec-h"><h2>History</h2></div>
      {done.length === 0 ? <span className="meta">No decisions yet.</span> : (
        <div className="toolrows">
          {done.map((a) => (
            <div key={a.id} className="toolrow">
              <span className="mono hist-when">{timeAgo(a.decided_at ?? a.created_at)}</span>
              <Pill map={APPROVAL_PILL} s={a.status} />
              <span className="d grow">{a.description}{a.result ? ` → ${a.result.slice(0, 160)}` : ""}</span>
              <span className="meta">{a.contact_name ?? (a.audience === "owner" ? "You" : "")}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ audit log */
function ActivityLog() {
  const [rows, setRows] = useState<AuditRecord[] | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.audit(onlyProblems ? "denied" : undefined).then(setRows).catch((e) => setErr((e as Error).message));
  }, [onlyProblems]);
  return (
    <>
      <div className="sec-h"><h2>Security and actions</h2>
        <label className="chk"><input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />Only denied</label>
      </div>
      {err ? <Result kind="err">{err}</Result> : rows === null ? <span className="meta">Loading…</span> : rows.length === 0 ? <span className="meta">Nothing logged yet.</span> : (
        <div className="toolrows">
          {rows.map((r) => (
            <div key={r.id} className="toolrow">
              <span className="mono hist-when">{new Date(r.at * 1000).toLocaleString()}</span>
              <span className={`pill ${r.outcome === "ok" ? "ok" : r.outcome === "denied" || r.outcome === "failed" ? "err" : "off"}`}>{r.outcome}</span>
              <span className="mono">{r.action}</span>
              <span className="d grow">{r.actor}{r.target ? ` → ${r.target}` : ""} {Object.keys(r.detail).length ? JSON.stringify(r.detail).slice(0, 180) : ""}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
