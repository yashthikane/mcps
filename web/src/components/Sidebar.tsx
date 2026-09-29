import { useEffect, useRef, useState } from "react";
import type { Conversation, Usage } from "../api";
import { Icon } from "../icons";
import { timeAgo } from "./ui";

interface Props {
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
    <aside className="side" aria-label="Conversations">
      <button className="btn btn-primary" onClick={p.onNew}><Icon name="plus" />New chat</button>
      <div className="search">
        <Icon name="search" />
        <label className="sr" htmlFor="conv-search">Search conversations</label>
        <input ref={p.searchRef} className="input" id="conv-search" type="search" placeholder="Search conversations"
          autoComplete="off" value={p.query} onChange={(e) => p.onQuery(e.target.value)} />
      </div>
      <div className="convs">
        {!groups.length && (
          <div className="rp-empty"><span className="meta">{p.query ? `No conversations match “${p.query}”` : "No conversations yet"}</span></div>
        )}
        {groups.map(([name, items]) => (
          <div key={name}>
            <div className="cgroup eyebrow">{name}</div>
            {items.map((c) => (
              <ConvItem key={c.id} c={c} active={c.id === p.activeId} running={c.id === p.runningId}
                editing={editing === c.id} deleting={deleting === c.id}
                onOpen={() => p.onOpen(c.id)} onPin={() => p.onPin(c)}
                onStartRename={() => { setDeleting(null); setEditing(c.id); }}
                onRename={(t) => { setEditing(null); if (t && t !== c.title) p.onRename(c, t); }}
                onStartDelete={() => { setEditing(null); setDeleting(c.id); }}
                onDelete={(yes) => { setDeleting(null); if (yes) p.onDelete(c); }} />
            ))}
          </div>
        ))}
      </div>
      <div className="side-foot">
        <div className="budget-row"><span className="eyebrow">Groq today</span><span className="meta">{req.toLocaleString()} / 1,000 req</span></div>
        <div className={`meter ${req >= 800 ? "warn" : ""}`} title={`${(p.usage?.tokens ?? 0).toLocaleString()} tokens today`}>
          <i style={{ width: `${Math.min(100, req / 10)}%` }} />
        </div>
      </div>
    </aside>
  );
}

function ConvItem({ c, active, running, editing, deleting, onOpen, onPin, onStartRename, onRename, onStartDelete, onDelete }: {
  c: Conversation; active: boolean; running: boolean; editing: boolean; deleting: boolean;
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
      <button className="conv-btn" onClick={onOpen} aria-current={active ? "true" : undefined}>
        <span className="conv-title">{c.title}</span>
        <span className="meta">{running ? <span className="st-proc">● answering</span> : c.snippet ? c.snippet.replace(/[[\]]/g, "") : timeAgo(c.updated_at)}</span>
      </button>
      <div className="conv-actions">
        <button className={`btn btn-ghost btn-icon${c.pinned ? " on" : ""}`} onClick={onPin} aria-pressed={c.pinned} aria-label={`${c.pinned ? "Unpin" : "Pin"} ${c.title}`}><Icon name="pin" /></button>
        <button className="btn btn-ghost btn-icon" onClick={onStartRename} aria-label={`Rename ${c.title}`}><Icon name="pen" /></button>
        <button className="btn btn-ghost btn-icon" onClick={onStartDelete} aria-label={`Delete ${c.title}`}><Icon name="trash" /></button>
      </div>
    </div>
  );
}
