import { lazy, memo, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ThinkingOrb } from "thinking-orbs";
import type { Connection, Conversation, Message, PermissionMode, ToolEvent } from "../api";
import { Icon, toolIcon } from "../icons";
import AmbientBackground from "./ambient/AmbientBackground";
import { liveOrb } from "./orbState";
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

export const MODES: { id: PermissionMode; label: string; icon: string; hint: string; desc: string; foot: string }[] = [
  { id: "plan", label: "Plan", icon: "note", hint: "Plan mode: Donna only reads and writes a plan. Nothing is sent, changed or deleted.",
    desc: "Reads and writes a plan. Nothing is sent, changed or deleted.", foot: "Plan mode: Donna only reads and writes a plan." },
  { id: "manual", label: "Manual", icon: "shield", hint: "Manual mode: actions that send, change or delete wait for your approval.",
    desc: "Asks before sending, changing or deleting.", foot: "Donna asks before sending, changing or deleting anything." },
  { id: "auto", label: "Auto", icon: "bolt", hint: "Auto mode: every action runs without asking, including sending and deleting.",
    desc: "Runs every action without asking, including sending and deleting.", foot: "Auto mode: actions run without asking." },
];

interface Props {
  conversation: Conversation | null;
  messages: Message[];
  live: Live | null;
  connections: Connection[];
  blocked: string | null;
  reducedMotion: boolean;
  onSend: (text: string, forced: string[]) => void;
  onStop: () => void;
  onDecide: (callId: string, approved: boolean) => void;
  onCommand: (cmd: string) => void;
  mode: PermissionMode;
  onMode: (m: PermissionMode) => void;
}

export default function Chat(p: Props) {
  const threadRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [focused, setFocused] = useState(false);

  useLayoutEffect(() => {
    const el = threadRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  const empty = !p.messages.length && !p.live;
  const composer = (
    <Composer busy={!!p.live} blocked={p.blocked} connections={p.connections} onFocusChange={setFocused}
      onSend={p.onSend} onStop={p.onStop} onCommand={p.onCommand} mode={p.mode} onMode={p.onMode} />
  );
  return (
    <div className={`chat ${empty ? "is-welcome" : "is-thread"}`}>
      {/* Animated only on the idle welcome screen; frozen (and dimmed by CSS) as soon as you type or chat. */}
      <AmbientBackground state={empty && !focused ? "idle" : "paused"} reduced={p.reducedMotion} />
      {empty ? (
        <Welcome connections={p.connections} reduced={p.reducedMotion} onSend={(t) => p.onSend(t, [])} onCommand={p.onCommand}>{composer}</Welcome>
      ) : (
        <>
          <div className="thread" ref={threadRef} aria-live="polite"
            onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
            <div className="thread-inner">
              {/* Index keys: the optimistic user message and the live reply are swapped for saved copies
                  without remounting, so nothing flickers when a reply finishes. */}
              {p.messages.map((m, i) => (m.role === "user" ? <UserMsg key={i} m={m} /> : <BotMsg key={i} m={m} />))}
              {p.live && <LiveTurn live={p.live} mode={p.mode} reduced={p.reducedMotion} onDecide={p.onDecide} />}
            </div>
          </div>
          <div className="dock">{composer}</div>
        </>
      )}
    </div>
  );
}

const clock = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const UserMsg = memo(function UserMsg({ m }: { m: Message }) {
  return <div className="msg-user"><p>{m.content}</p></div>;
});

const BotMsg = memo(function BotMsg({ m }: { m: Message }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard?.writeText(m.content).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }); };
  return (
    <div className="msg-bot">
      <div className="who">Donna <span className="ts">{clock(m.created_at)}</span></div>
      {m.tool_events.length > 0 && <ToolList tools={m.tool_events} />}
      {m.content && <div className="bot-text md"><Suspense fallback={<p style={{ whiteSpace: "pre-wrap" }}>{m.content}</p>}><Markdown text={m.content} /></Suspense></div>}
      {m.content && (
        <div className="msg-acts">
          <button className="icon-btn" onClick={copy} aria-label="Copy reply" title={copied ? "Copied" : "Copy"}><Icon name={copied ? "check" : "copy"} size={15} /></button>
        </div>
      )}
    </div>
  );
});

function LiveTurn({ live, mode, reduced, onDecide }: { live: Live; mode: PermissionMode; reduced: boolean; onDecide: (id: string, ok: boolean) => void }) {
  const waiting = live.tools.filter((t) => t.status === "waiting");
  const shown = live.tools.filter((t) => t.status !== "waiting");
  const orb = liveOrb(live, mode);
  return (
    <div className="msg-bot live-turn">
      <div className="who">Donna
        <span className="live" role="status">
          <ThinkingOrb state={orb.state} size={20} theme="dark" paused={reduced} aria-label={orb.label} />
          <span className="live-label">{orb.label}</span>
        </span>
      </div>
      {shown.length > 0 && <ToolList tools={shown} />}
      {waiting.map((t) => <Approval key={t.call_id} t={t} onDecide={onDecide} />)}
      {(live.text || !waiting.length) && (
        <div className="bot-text md">
          <Suspense fallback={<p style={{ whiteSpace: "pre-wrap" }}>{live.text}</p>}>{live.text && <Markdown text={live.text} />}</Suspense>
          {!waiting.length && live.text && <span className="caret" aria-hidden="true" />}
        </div>
      )}
    </div>
  );
}

function ToolList({ tools }: { tools: ToolEvent[] }) {
  return <div className="toollist">{tools.map((t) => <ToolCard key={t.call_id} t={t} />)}</div>;
}

const STATUS: Record<string, [string, string]> = {
  running: ["run", "Running"], done: ["done", "Done"], error: ["err", "Failed"], rejected: ["off", "Rejected"], waiting: ["wait", "Needs approval"], pending: ["wait", "Waiting for approval"],
};

function argSummary(args: Record<string, unknown>): string {
  return Object.entries(args).filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ").slice(0, 140);
}

/** One tool call: name, short description, status and timing; expands to the real result and arguments. */
function ToolCard({ t }: { t: ToolEvent }) {
  const [open, setOpen] = useState(t.status === "error");
  const [cls, label] = STATUS[t.status] ?? ["off", t.status];
  return (
    <div className={`toolcall s-${t.status}`}>
      <button className="tc-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="tc-ic"><Icon name={toolIcon(t.name)} size={14} /></span>
        <span className="tc-name mono">{t.name}</span>
        <span className="tc-args">{t.description || argSummary(t.args)}</span>
        {t.approved === "auto" && <span className="tag" title="Ran without asking (Auto mode)">auto</span>}
        <span className={`st ${cls}`}>{label}</span>
        {t.ms > 0 && <span className="tc-ms mono">{fmtMs(t.ms)}</span>}
        <Icon name="chevron" size={14} className={`tc-chev${open ? " rot" : ""}`} />
      </button>
      {open && (
        <div className="tc-body">
          <div className="kvb"><b>Result</b><pre>{t.preview || (t.status === "running" ? "Running…" : "(no output)")}</pre></div>
          <div className="kvb"><b>Arguments</b><pre className="args">{JSON.stringify(t.args, null, 2)}</pre></div>
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
      <h3><Icon name="sent" size={15} />{actionTitle(t.name)}</h3>
      {t.description && <span className="help">{t.description}</span>}
      <dl className="kv">
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd></div>
        ))}
        <dt>tool</dt><dd>{t.name}</dd>
      </dl>
      <div className="actions">
        <button className="btn btn-secondary" disabled={busy} onClick={() => decide(false)}><Icon name="x" size={14} />Reject</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)} autoFocus><Icon name="check" size={14} />{busy ? "Working…" : "Approve"}</button>
      </div>
    </div>
  );
}

function Welcome({ connections, reduced, onSend, onCommand, children }: {
  connections: Connection[]; reduced: boolean; onSend: (t: string) => void; onCommand: (c: string) => void; children: React.ReactNode;
}) {
  const on = (id: string) => connections.find((c) => c.id === id)?.state === "connected";
  const sugg: { icon: string; t: string; prompt?: string; cmd?: string }[] = [];
  if (on("gmail")) sugg.push({ icon: "mail", t: "Summarize unread emails", prompt: "Summarize my unread emails" });
  else sugg.push({ icon: "mail", t: "Connect Gmail & Calendar", cmd: "wizard:google" });
  if (on("calendar")) sugg.push({ icon: "cal", t: "This week's calendar", prompt: "What's on my calendar this week?" });
  if (on("notion")) sugg.push({ icon: "notion", t: "List my Notion pages", prompt: "List my Notion pages" });
  else sugg.push({ icon: "notion", t: "Connect Notion", cmd: "wizard:notion" });
  sugg.push({ icon: "sun", t: "Weather in Pune", prompt: "What's the weather in Pune right now?" });
  if (sugg.length < 4) sugg.push({ icon: "terminal", t: "Add an MCP server", cmd: "wizard:mcp" });
  return (
    <div className="empty">
      <div className="w-orb"><ThinkingOrb state="breathing" size={64} theme="dark" paused={reduced} aria-label="Donna is ready" /></div>
      <h2>What can I help you with?</h2>
      <p className="w-sub">Your personal assistant. Ready when you are.</p>
      <div className="w-chips">
        {sugg.slice(0, 4).map((x) => (
          <button key={x.t} className="w-chip" onClick={() => (x.cmd ? onCommand(x.cmd) : onSend(x.prompt ?? x.t))}>
            <Icon name={x.icon} size={14} />{x.t}
          </button>
        ))}
      </div>
      <div className="w-composer">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ composer */

function useOutside(open: boolean, close: () => void, ...refs: React.RefObject<HTMLElement>[]) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!refs.some((r) => r.current?.contains(e.target as Node))) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

function Composer({ busy, blocked, connections, onSend, onStop, onCommand, mode, onMode, onFocusChange }: {
  busy: boolean; blocked: string | null; connections: Connection[];
  onSend: (t: string, forced: string[]) => void; onStop: () => void; onCommand: (c: string) => void;
  mode: PermissionMode; onMode: (m: PermissionMode) => void; onFocusChange: (f: boolean) => void;
}) {
  const cycleMode = () => onMode(MODES[(MODES.findIndex((m) => m.id === mode) + 1) % MODES.length].id);
  const [text, setText] = useState("");
  const [forced, setForced] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const [menu, setMenu] = useState<"plus" | "mode" | null>(null);
  const [focus, setFocus] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const plusRef = useRef<HTMLDivElement>(null);
  const modeRef = useRef<HTMLDivElement>(null);
  useOutside(menu === "plus", () => setMenu(null), plusRef);
  useOutside(menu === "mode", () => setMenu(null), modeRef);
  const usable = connections.filter((c) => c.id !== "utils" && (c.state === "connected" || c.setup === "google" || c.setup === "notion"));
  const slash = text.startsWith("/") && !text.includes(" ") ? SLASH.filter((s) => s.cmd.startsWith(text.toLowerCase())) : [];
  const m = MODES.find((x) => x.id === mode) ?? MODES[1];

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(180, el.scrollHeight)}px`;
  }, [text]);
  useEffect(() => { if (!busy) ta.current?.focus(); }, [busy]);
  useEffect(() => () => onFocusChange(false), [onFocusChange]);

  const submit = () => {
    const t = text.trim();
    if (!t || busy || blocked) return;
    onSend(t, forced);
    setText("");
    setForced([]);
  };
  const runSlash = (cmd: string) => { setText(""); onCommand(cmd.slice(1)); };
  const setF = (f: boolean) => { setFocus(f); onFocusChange(f); };
  const toggle = (id: string) => setForced(forced.includes(id) ? forced.filter((x) => x !== id) : [...forced, id]);

  return (
    <div className={`composer-wrap${focus ? " focus" : ""}`}>
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
        {forced.length > 0 && (
          <div className="tokens">
            {forced.map((id) => {
              const c = connections.find((x) => x.id === id);
              return c ? (
                <span key={id} className="token"><Icon name={c.id === "notion" ? "notion" : c.icon} size={13} />{c.name}
                  <button type="button" onClick={() => toggle(id)} aria-label={`Stop using ${c.name}`}><Icon name="x" size={12} /></button>
                </span>
              ) : null;
            })}
          </div>
        )}
        <label className="sr" htmlFor="prompt">Message Donna</label>
        <textarea id="prompt" ref={ta} rows={1} value={text} disabled={!!blocked && !busy}
          placeholder={blocked ?? "Ask Donna anything…"}
          onFocus={() => setF(true)} onBlur={() => setF(false)}
          onChange={(e) => { setText(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (slash.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setSel((sel + (e.key === "ArrowDown" ? 1 : slash.length - 1)) % slash.length); return; }
              if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); runSlash(slash[sel].cmd); return; }
              if (e.key === "Escape") { setText(""); return; }
            }
            if (e.key === "Tab" && e.shiftKey) { e.preventDefault(); cycleMode(); return; }
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }} />
        <div className="comp-row">
          <div className="pop" ref={plusRef}>
            <button type="button" className="cbtn round" aria-label="Connections and commands" aria-expanded={menu === "plus"} onClick={() => setMenu(menu === "plus" ? null : "plus")}>
              <Icon name="plus" size={16} />
            </button>
            {menu === "plus" && (
              <div className="menu" role="menu">
                <div className="mh">Use a connection for this message</div>
                {usable.map((c) => {
                  const ready = c.state === "connected";
                  return (
                    <button key={c.id} type="button" role="menuitemcheckbox" aria-checked={forced.includes(c.id)} className="mo"
                      onClick={() => { if (ready) toggle(c.id); else onCommand(`wizard:${c.setup}`); setMenu(null); }}>
                      <Icon name={c.id === "notion" ? "notion" : c.icon} size={16} /><span className="tx">{c.name}</span>
                      {!ready ? <span className="st-txt">Connect</span> : forced.includes(c.id) ? <Icon name="check" size={15} className="ck" /> : null}
                    </button>
                  );
                })}
                <hr />
                <button type="button" role="menuitem" className="mo" onClick={() => { setMenu(null); setText("/"); ta.current?.focus(); }}>
                  <Icon name="slash" size={16} /><span className="tx">Commands</span><kbd>/</kbd>
                </button>
                <button type="button" role="menuitem" className="mo" onClick={() => { setMenu(null); onCommand("new-task"); }}>
                  <Icon name="clock" size={16} /><span className="tx">Schedule a task</span>
                </button>
                <button type="button" role="menuitem" className="mo" onClick={() => { setMenu(null); onCommand("connections"); }}>
                  <Icon name="plug" size={16} /><span className="tx">Manage connections</span>
                </button>
              </div>
            )}
          </div>
          <div className="pop" ref={modeRef}>
            <button type="button" className={`cbtn mode m-${mode}`} aria-label={`Permission mode: ${m.label}`} aria-expanded={menu === "mode"} title="Permission mode (Shift+Tab)"
              onClick={() => setMenu(menu === "mode" ? null : "mode")}>
              <Icon name={m.icon} size={15} className="mi" />{m.label}<Icon name="chevdown" size={13} className="chev" />
            </button>
            {menu === "mode" && (
              <div className="menu" role="menu" aria-label="Permission mode">
                <div className="mh">Permission mode</div>
                {MODES.map((x) => (
                  <button key={x.id} type="button" role="menuitemradio" aria-checked={mode === x.id} className={`mo m-${x.id}${mode === x.id ? " sel" : ""}`}
                    onClick={() => { onMode(x.id); setMenu(null); ta.current?.focus(); }}>
                    <Icon name={x.icon} size={16} className="mi" /><span className="tx">{x.label}<small>{x.desc}</small></span>
                    {mode === x.id && <Icon name="check" size={15} className="ck" />}
                  </button>
                ))}
                <div className="mf"><span>Switch with</span><kbd>Shift+Tab</kbd></div>
              </div>
            )}
          </div>
          <button type="submit" className={`send${busy ? " stop" : ""}`} aria-label={busy ? "Stop" : "Send"} disabled={!busy && (!text.trim() || !!blocked)}>
            <Icon name={busy ? "stop" : "send"} size={17} />
          </button>
        </div>
      </form>
      <div className={`comp-foot m-${mode}`}>{m.foot}</div>
    </div>
  );
}
