import { lazy, memo, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Connection, Conversation, Message, ToolEvent } from "../api";
import { Icon, PixelD, toolIcon } from "../icons";
import { fmtMs } from "./ui";

const Markdown = lazy(() => import("./Markdown"));

export interface Live {
  runId?: string;
  conversationId: string;
  text: string;
  tools: ToolEvent[];
  status?: string;
}

const ACTION_TITLES: Record<string, string> = {
  send_email: "Send email", reply_email: "Reply to email", forward_email: "Forward email", send_draft: "Send draft",
  delete_email: "Move email to trash", create_event: "Create calendar event", update_event: "Change calendar event",
  delete_event: "Delete calendar event", delete_page: "Move Notion page to trash",
};
export const actionTitle = (name: string) =>
  ACTION_TITLES[name] ?? name.split("__").pop()!.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const SLASH = [
  { cmd: "/new", d: "Start a new chat" },
  { cmd: "/tools", d: "See every tool Donna can use" },
  { cmd: "/connections", d: "Open Connections" },
  { cmd: "/settings", d: "Open Settings" },
  { cmd: "/search", d: "Search conversations" },
];

interface Props {
  conversation: Conversation | null;
  messages: Message[];
  live: Live | null;
  connections: Connection[];
  blocked: string | null;
  runOpen: boolean;
  onSend: (text: string, forced: string[]) => void;
  onStop: () => void;
  onDecide: (callId: string, approved: boolean) => void;
  onCommand: (cmd: string) => void;
  onOpenSide: () => void;
  onToggleRun: () => void;
  onOpenRun: () => void;
}

export default function Chat(p: Props) {
  const threadRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const toolCount = p.connections.filter((c) => c.state === "connected").reduce((n, c) => n + c.tools.filter((t) => t.enabled).length, 0);

  useLayoutEffect(() => {
    const el = threadRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  const empty = !p.messages.length && !p.live;
  return (
    <div className="thread-col">
      <div className="chat-head">
        <button className="btn btn-ghost btn-icon menu-btn" onClick={p.onOpenSide} aria-label="Show conversations"><Icon name="menu" /></button>
        <div className="t">
          <h1>{p.conversation?.title ?? "New chat"}</h1>
          <div className="meta">{toolCount} tools ready · {p.connections.filter((c) => c.state === "connected").length} connections</div>
        </div>
        <button className="btn btn-ghost btn-icon" onClick={p.onToggleRun} aria-label="Toggle activity panel" aria-pressed={p.runOpen}><Icon name="panel" /></button>
      </div>

      <div className="thread" ref={threadRef} aria-live="polite"
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {empty ? (
          <EmptyState connections={p.connections} onSend={(t) => p.onSend(t, [])} onCommand={p.onCommand} />
        ) : (
          <div className="thread-inner">
            {/* Index keys: the optimistic user message and the live reply are swapped for saved copies
                without remounting, so nothing flickers when a reply finishes. */}
            {p.messages.map((m, i) => (m.role === "user" ? <UserMsg key={i} m={m} /> : <BotMsg key={i} m={m} onOpenRun={p.onOpenRun} />))}
            {p.live && <LiveTurn live={p.live} onDecide={p.onDecide} onOpenRun={p.onOpenRun} />}
          </div>
        )}
      </div>

      <Composer busy={!!p.live} blocked={p.blocked} connections={p.connections} toolCount={toolCount}
        onSend={p.onSend} onStop={p.onStop} onCommand={p.onCommand} />
    </div>
  );
}

const UserMsg = memo(function UserMsg({ m }: { m: Message }) {
  return <div className="msg-user"><p>{m.content}</p></div>;
});

const BotMsg = memo(function BotMsg({ m, onOpenRun }: { m: Message; onOpenRun: () => void }) {
  return (
    <div className="msg-bot">
      <span className="avatar"><PixelD size={12} /></span>
      <div className="bot-body">
        <span className="meta">DONNA</span>
        {m.tool_events.length > 0 && <ToolList tools={m.tool_events} onOpenRun={onOpenRun} />}
        {m.content && <div className="bot-text md"><Suspense fallback={<p style={{ whiteSpace: "pre-wrap" }}>{m.content}</p>}><Markdown text={m.content} /></Suspense></div>}
      </div>
    </div>
  );
});

function LiveTurn({ live, onDecide, onOpenRun }: { live: Live; onDecide: (id: string, ok: boolean) => void; onOpenRun: () => void }) {
  const waiting = live.tools.filter((t) => t.status === "waiting");
  const shown = live.tools.filter((t) => t.status !== "waiting");
  return (
    <div className="msg-bot live-turn">
      <span className="avatar"><PixelD size={12} /></span>
      <div className="bot-body">
        <span className="meta">DONNA · {live.status ?? (live.text ? "writing" : live.tools.length ? "working" : "thinking")}</span>
        {shown.length > 0 && <ToolList tools={shown} onOpenRun={onOpenRun} />}
        {waiting.map((t) => <Approval key={t.call_id} t={t} onDecide={onDecide} />)}
        <div className="bot-text md">
          <Suspense fallback={<p style={{ whiteSpace: "pre-wrap" }}>{live.text}</p>}>{live.text && <Markdown text={live.text} />}</Suspense>
          {!waiting.length && <span className="caret" aria-hidden="true" />}
        </div>
      </div>
    </div>
  );
}

function ToolList({ tools, onOpenRun }: { tools: ToolEvent[]; onOpenRun: () => void }) {
  return <div className="toollist">{tools.map((t) => <ToolCard key={t.call_id} t={t} onOpenRun={onOpenRun} />)}</div>;
}

const STATUS: Record<string, [string, string]> = {
  running: ["proc", "Running"], done: ["ok", "Done"], error: ["err", "Failed"], rejected: ["off", "Rejected"], waiting: ["warn", "Needs approval"],
};

function argSummary(args: Record<string, unknown>): string {
  return Object.entries(args).filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ").slice(0, 140);
}

function ToolCard({ t, onOpenRun }: { t: ToolEvent; onOpenRun: () => void }) {
  const [open, setOpen] = useState(t.status === "error");
  const [cls, label] = STATUS[t.status] ?? ["off", t.status];
  return (
    <div className={`toolcall s-${t.status}`}>
      <button className="tc-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="tc-ic"><Icon name={toolIcon(t.name)} size={13} /></span>
        <span className="tc-name mono">{t.name}</span>
        <span className="tc-args">{argSummary(t.args)}</span>
        <span className={`pill ${cls}`}>{label}</span>
        {t.ms > 0 && <span className="meta">{fmtMs(t.ms)}</span>}
        <Icon name="chevron" size={13} className={open ? "rot" : ""} />
      </button>
      {open && (
        <div className="tc-body">
          <pre>{t.preview || (t.status === "running" ? "Running…" : "(no output)")}</pre>
          <button className="linkbtn" onClick={onOpenRun}>Open in activity panel</button>
        </div>
      )}
    </div>
  );
}

function Approval({ t, onDecide }: { t: ToolEvent; onDecide: (id: string, ok: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  const decide = (ok: boolean) => { setBusy(true); onDecide(t.call_id, ok); };
  const entries = Object.entries(t.args).filter(([, v]) => v !== "" && v !== null);
  return (
    <div className="approval" role="group" aria-label="Action requires approval">
      <span className="eyebrow st-warn">■ Action requires approval</span>
      <h3>{actionTitle(t.name)}</h3>
      {t.description && <span className="help">{t.description}</span>}
      <dl className="kv">
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd></div>
        ))}
        <dt>tool</dt><dd>{t.name}</dd>
      </dl>
      <div className="actions">
        <button className="btn btn-secondary" disabled={busy} onClick={() => decide(false)}>Reject</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)} autoFocus>{busy ? "Working…" : "Approve"}</button>
      </div>
    </div>
  );
}

function EmptyState({ connections, onSend, onCommand }: { connections: Connection[]; onSend: (t: string) => void; onCommand: (c: string) => void }) {
  const on = (id: string) => connections.find((c) => c.id === id)?.state === "connected";
  const sugg: { icon: string; t: string; s: string; prompt?: string; cmd?: string }[] = [];
  if (on("gmail")) sugg.push({ icon: "mail", t: "Summarize my unread emails", s: "Gmail" });
  else sugg.push({ icon: "mail", t: "Connect Gmail and Calendar", s: "Guided setup · about 5 minutes", cmd: "wizard:google" });
  if (on("calendar")) sugg.push({ icon: "cal", t: "What's on my calendar this week?", s: "Google Calendar" });
  if (on("notion")) {
    sugg.push({ icon: "doc", t: "List my Notion pages", s: "Notion" });
  } else sugg.push({ icon: "doc", t: "Connect Notion", s: "Guided setup · about 2 minutes", cmd: "wizard:notion" });
  sugg.push({ icon: "sun", t: "What's the weather in Pune right now?", s: "Weather" });
  if (sugg.length < 4) sugg.push({ icon: "terminal", t: "Add an MCP server", s: "Filesystem, Git, Fetch or any server", cmd: "wizard:mcp" });
  return (
    <div className="empty">
      <span className="eyebrow"><span className="sq">■</span> New conversation</span>
      <h2>What can I do<br />for you today?</h2>
      <p>Ask in plain words. Donna uses your connected apps and asks before sending, changing or deleting anything. Type <kbd>/</kbd> for commands.</p>
      <div className="suggest">
        {sugg.slice(0, 4).map((x) => (
          <button key={x.t} className="sugg" onClick={() => (x.cmd ? onCommand(x.cmd) : onSend(x.prompt ?? x.t))}>
            <span className="ic"><Icon name={x.icon} /></span>
            <span><b>{x.t}</b><span className="meta">{x.s}</span></span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer({ busy, blocked, connections, toolCount, onSend, onStop, onCommand }: {
  busy: boolean; blocked: string | null; connections: Connection[]; toolCount: number;
  onSend: (t: string, forced: string[]) => void; onStop: () => void; onCommand: (c: string) => void;
}) {
  const [text, setText] = useState("");
  const [forced, setForced] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const ta = useRef<HTMLTextAreaElement>(null);
  const chips = connections.filter((c) => c.state === "connected" && c.id !== "utils");
  const slash = text.startsWith("/") && !text.includes(" ") ? SLASH.filter((s) => s.cmd.startsWith(text.toLowerCase())) : [];

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(180, el.scrollHeight)}px`;
  }, [text]);
  useEffect(() => { if (!busy) ta.current?.focus(); }, [busy]);

  const submit = () => {
    const t = text.trim();
    if (!t || busy || blocked) return;
    onSend(t, forced);
    setText("");
    setForced([]);
  };
  const runSlash = (cmd: string) => { setText(""); onCommand(cmd.slice(1)); };

  return (
    <div className="composer-wrap">
      {slash.length > 0 && (
        <div className="slash"><ul role="listbox" aria-label="Commands">
          {slash.map((s, i) => (
            <li key={s.cmd} role="option" aria-selected={i === sel} className={i === sel ? "sel" : ""}
              onMouseDown={(e) => { e.preventDefault(); runSlash(s.cmd); }}>
              <span className="mono">{s.cmd}</span><span>{s.d}</span>
            </li>
          ))}
        </ul></div>
      )}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); if (busy) onStop(); else submit(); }}>
        <label className="sr" htmlFor="prompt">Message Donna</label>
        <textarea id="prompt" ref={ta} rows={1} value={text} disabled={!!blocked && !busy}
          placeholder={blocked ?? "Ask Donna anything…  (/ for commands)"}
          onChange={(e) => { setText(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (slash.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setSel((sel + (e.key === "ArrowDown" ? 1 : slash.length - 1)) % slash.length); return; }
              if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); runSlash(slash[sel].cmd); return; }
              if (e.key === "Escape") { setText(""); return; }
            }
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }} />
        <div className="comp-row">
          {chips.map((c) => {
            const on = forced.includes(c.id);
            return (
              <button key={c.id} type="button" className="chipbtn" aria-pressed={on} title={`Use ${c.name} for this message`}
                onClick={() => setForced(on ? forced.filter((x) => x !== c.id) : [...forced, c.id])}>
                @{c.name.toLowerCase().replace(/^google /, "").replace(/\s+/g, "-")}
              </button>
            );
          })}
          <button type="submit" className={`btn btn-icon send ${busy ? "btn-secondary" : "btn-primary"}`} aria-label={busy ? "Stop" : "Send"}
            disabled={!busy && (!text.trim() || !!blocked)}>
            <Icon name={busy ? "stop" : "send"} />
          </button>
        </div>
      </form>
      <div className="comp-hint"><span className="meta">Enter to send · Shift + Enter for a new line</span><span className="meta">{toolCount} tools ready</span></div>
    </div>
  );
}
