import { useEffect, useRef, useState } from "react";
import type { Conversation, Usage } from "../api";
import { DonnaOrb, Icon } from "../icons";
import { timeAgo } from "./ui";

export type View = "chat" | "inbox" | "contacts" | "tasks" | "connections" | "settings";

const NAV: [View, string, string][] = [
  ["chat", "chat", "Chat"], ["inbox", "inbox", "Inbox"], ["contacts", "users", "Contacts"],
  ["tasks", "tasks", "Tasks"], ["connections", "plug", "Connections"], ["settings", "sliders", "Settings"],
];

interface Props {
  view: View;
  onView: (v: View) => void;
  badge: number;
  status: [string, string];          // [tone, label] from the real /health result
  conversations: Conversation[];
  activeId: string | null;
  runningId: string | null;
  query: string;
  usage: Usage | null;
  searchRef: React.RefObject<HTMLInputElement>;
  onQuery: (q: string) => void;
  onOpen: (id: string) => void;
  onNew: () => void;
  onPin: (c: Conversation) => void;
  onRename: (c: Conversation, title: string) => void;
  onDelete: (c: Conversation) => void;
  onCollapse: () => void;
}

function groupOf(c: Conversation): string {
  if (c.pinned) return "Pinned";
  const age = Date.now() / 1000 - c.updated_at;
  const startOfToday = new Date().setHours(0, 0, 0, 0) / 1000;
  if (c.updated_at >= startOfToday) return "Today";
  if (age < 7 * 86400) return "This week";
  return "Earlier";
}

export default function Sidebar(p: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const groups = ["Pinned", "Today", "This week", "Earlier"]
    .map((g) => [g, p.conversations.filter((c) => groupOf(c) === g)] as const)
    .filter(([, items]) => items.length);
  const req = p.usage?.requests ?? 0;

  return (
    <aside className="side" aria-label="Navigation and conversations">
      <div className="s-top">
        <a className="brand" href="#" aria-label="Donna home" onClick={(e) => { e.preventDefault(); p.onView("chat"); }}>
          <span className="logo"><DonnaOrb size={22} /></span><span className="word">Donna</span>
        </a>
        <button className="icon-btn s-collapse" onClick={p.onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar"><Icon name="panel" size={17} /></button>
      </div>
      <button className="newchat" onClick={p.onNew}><Icon name="newchat" size={16} /><span className="lb">New chat</span></button>
      <div className="search">
        <Icon name="search" size={15} />
        <label className="sr" htmlFor="conv-search">Search conversations</label>
        <input ref={p.searchRef} id="conv-search" type="search" placeholder="Search" autoComplete="off" value={p.query} onChange={(e) => p.onQuery(e.target.value)} />
      </div>

      <div className="s-label">Workspace</div>
      <nav className="s-nav" role="tablist" aria-label="Sections">
        {NAV.map(([v, icon, label]) => (
          <button key={v} className="tab" role="tab" aria-selected={p.view === v} onClick={() => p.onView(v)} title={label}>
            <Icon name={icon} size={17} /><span className="lb">{label}</span>
            {v === "inbox" && p.badge > 0 && <span className="nav-badge" aria-label={`${p.badge} waiting for you`}>{p.badge}</span>}
          </button>
        ))}
      </nav>

      <div className="s-label">Recent</div>
      <div className="convs">
        {!groups.length && (
          <div className="conv-empty">{p.query ? `No conversations match “${p.query}”` : "No conversations yet"}</div>
        )}
        {groups.map(([name, items]) => (
          <div key={name}>
            <div className="cgroup">{name}</div>
            {items.map((c) => (
              <ConvItem key={c.id} c={c} active={c.id === p.activeId && p.view === "chat"} running={c.id === p.runningId}
                editing={editing === c.id} deleting={deleting === c.id} snippet={p.query ? c.snippet : ""}
                onOpen={() => p.onOpen(c.id)} onPin={() => p.onPin(c)}
                onStartRename={() => { setDeleting(null); setEditing(c.id); }}
                onRename={(t) => { setEditing(null); if (t && t !== c.title) p.onRename(c, t); }}
                onStartDelete={() => { setEditing(null); setDeleting(c.id); }}
                onDelete={(yes) => { setDeleting(null); if (yes) p.onDelete(c); }} />
            ))}
          </div>
        ))}
      </div>

      <div className="s-status" title={`${(p.usage?.tokens ?? 0).toLocaleString()} tokens today`}>
        <div className="r"><span className={`dot ${p.status[0]}`} /><b>{p.status[1]}</b><span className="n">{req.toLocaleString()} / 1,000 req</span></div>
        <div className={`meter ${req >= 800 ? "warn" : ""}`}><i style={{ width: `${Math.min(100, req / 10)}%` }} /></div>
      </div>
    </aside>
  );
}

function ConvItem({ c, active, running, editing, deleting, snippet, onOpen, onPin, onStartRename, onRename, onStartDelete, onDelete }: {
  c: Conversation; active: boolean; running: boolean; editing: boolean; deleting: boolean; snippet?: string;
  onOpen: () => void; onPin: () => void; onStartRename: () => void; onRename: (t: string) => void;
  onStartDelete: () => void; onDelete: (yes: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (editing) { input.current?.focus(); input.current?.select(); } }, [editing]);
  useEffect(() => { if (deleting) keep.current?.focus(); }, [deleting]);
  const cls = `conv${active ? " active" : ""}`;

  if (editing) {
    return (
      <div className={cls}><div className="conv-edit">
        <label className="sr" htmlFor={`ren-${c.id}`}>Rename conversation</label>
        <input ref={input} className="input" id={`ren-${c.id}`} defaultValue={c.title}
          onKeyDown={(e) => { if (e.key === "Enter") onRename(e.currentTarget.value.trim()); if (e.key === "Escape") onRename(c.title); }}
          onBlur={(e) => onRename(e.currentTarget.value.trim())} />
      </div></div>
    );
  }
  if (deleting) {
    return (
      <div className={cls}><div className="conv-del">
        <span>Delete this chat?</span>
        <button ref={keep} className="btn btn-ghost btn-sm" onClick={() => onDelete(false)}>Keep</button>
        <button className="btn btn-danger btn-sm" onClick={() => onDelete(true)}>Delete</button>
      </div></div>
    );
  }
  return (
    <div className={cls}>
      <button className="conv-btn" onClick={onOpen} aria-current={active ? "true" : undefined} title={c.snippet ? c.snippet.replace(/[[\]]/g, "") : timeAgo(c.updated_at)}>
        <span className="conv-title">{c.title}</span>
        {snippet && <span className="conv-snip">{snippet.replace(/[[\]]/g, "")}</span>}
        {running && <span className="conv-run" aria-label="Answering" />}
      </button>
      <div className="conv-actions">
        <button className={`icon-btn${c.pinned ? " on" : ""}`} onClick={onPin} aria-pressed={c.pinned} aria-label={`${c.pinned ? "Unpin" : "Pin"} ${c.title}`}><Icon name="pin" size={14} /></button>
        <button className="icon-btn" onClick={onStartRename} aria-label={`Rename ${c.title}`}><Icon name="pen" size={14} /></button>
        <button className="icon-btn" onClick={onStartDelete} aria-label={`Delete ${c.title}`}><Icon name="trash" size={14} /></button>
      </div>
    </div>
  );
}
