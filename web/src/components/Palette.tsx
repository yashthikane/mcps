import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { Dialog } from "./ui";

export interface Command { id: string; label: string; icon: string; run: () => void }

export default function Palette({ open, commands, onClose }: { open: boolean; commands: Command[]; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const items = useMemo(() => commands.filter((c) => c.label.toLowerCase().includes(q.trim().toLowerCase())), [commands, q]);
  useEffect(() => { if (open) { setQ(""); setSel(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);
  useEffect(() => { document.getElementById(`pal-${sel}`)?.scrollIntoView({ block: "nearest" }); }, [sel]);
  const run = (i: number) => { const c = items[i]; if (!c) return; onClose(); c.run(); };

  return (
    <Dialog open={open} onClose={onClose} className="palette-dlg">
      <div className="pal-in">
        <Icon name="search" />
        <label className="sr" htmlFor="pal-q">Search commands</label>
        <input ref={input} id="pal-q" placeholder="Search commands…" autoComplete="off" role="combobox" aria-expanded="true"
          aria-controls="pal-list" aria-activedescendant={items.length ? `pal-${sel}` : undefined} value={q}
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((sel + 1) % Math.max(1, items.length)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setSel((sel - 1 + items.length) % Math.max(1, items.length)); }
            if (e.key === "Enter") { e.preventDefault(); run(sel); }
          }} />
      </div>
      <ul className="pal-list" id="pal-list" role="listbox">
        {items.length ? items.map((c, i) => (
          <li key={c.id} id={`pal-${i}`} role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => run(i)}>
            <Icon name={c.icon} /><span>{c.label}</span>
          </li>
        )) : <li aria-disabled="true"><span className="meta">No matching commands</span></li>}
      </ul>
      <div className="pal-foot"><span className="meta">↑↓ move</span><span className="meta">↵ open</span><span className="meta">Esc close</span></div>
    </Dialog>
  );
}
