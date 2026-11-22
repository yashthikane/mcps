import { useEffect, useRef, useState } from "react";
import { api, type Availability, type MenuOptionId, type Settings, type StatusMode } from "../api";
import { Icon } from "../icons";
import { Switch, useToast } from "./ui";

/* Your status: what contacts are told in Donna's away greeting (donna/availability.py). The sidebar card opens the
 * picker; the top-bar pill shows it while you're away. */

const DETAIL_MODES: StatusMode[] = ["working", "custom"];

function at(hour: number, minute = 0, dayOffset = 0): number {
  const d = new Date();
  d.setDate(d.getDate() + dayOffset);
  d.setHours(hour, minute, 0, 0);
  return d.getTime() / 1000;
}

/** Quick "until" choices: [label, epoch seconds | null]. */
function untilChoices(): [string, number | null][] {
  const now = Date.now() / 1000;
  const evening = at(18);
  return [
    ["30 min", now + 1800],
    ["1 hour", now + 3600],
    ...(evening - now > 3600 ? [["6:00 PM", evening] as [string, number]] : []),
    ["Tomorrow 9 AM", at(9, 0, 1)],
    ["Until I change it", null],
  ];
}

function localInput(epoch: number): string {
  const d = new Date(epoch * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const SOURCE: Record<string, string> = { calendar: "from your calendar", sleep_hours: "sleeping hours", manual: "", default: "" };

export function StatusCard({ data, open, onOpen, onClose, onSaved }: {
  data: Availability | null; open: boolean; onOpen: () => void; onClose: () => void; onSaved: (a: Availability) => void;
}) {
  const toast = useToast();
  const box = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const manual = data?.manual;
  const [mode, setMode] = useState<StatusMode>("working");
  const [detail, setDetail] = useState("");
  const [until, setUntil] = useState<number | null>(null);
  const [choice, setChoice] = useState("Until I change it");
  const [busy, setBusy] = useState(false);

  useEffect(() => {  // start from what's set now
    if (!open || !manual) return;
    setMode(manual.mode === "meeting" ? "working" : manual.mode);
    setDetail(manual.detail || "");
    const live = manual.until && manual.until > Date.now() / 1000 ? manual.until : null;
    setUntil(live);
    setChoice(live ? "custom" : "Until I change it");
  }, [open, manual]);

  useEffect(() => {  // fixed to the card, so the sidebar's overflow can't clip it
    if (!open || !btn.current) return;
    const place = () => { const r = btn.current!.getBoundingClientRect(); setPos({ left: Math.max(8, r.left), bottom: window.innerHeight - r.top + 8 }); };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const click = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) onClose(); };
    window.addEventListener("keydown", key);
    window.addEventListener("mousedown", click);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("mousedown", click); };
  }, [open, onClose]);

  const save = async (m: StatusMode = mode, u: number | null = until) => {
    setBusy(true);
    try {
      const res = await api.setAvailability(m, DETAIL_MODES.includes(m) ? detail.trim() : "", m === "available" ? null : u);
      onSaved(res);
      toast(m === "available" ? "You're available. Donna stays quiet." : `Status: ${res.status.emoji} ${res.status.label}`);
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
    setBusy(false);
  };

  const st = data?.status;
  return (
    <div className="pop status-pop" ref={box}>
      {open && data && (
        <div className="menu status-menu" role="dialog" aria-label="Set your status" style={pos ? { position: "fixed", left: pos.left, bottom: pos.bottom } : undefined}>
          <div className="mh">Your status · contacts see it in Donna's greeting</div>
          {data.modes.map((m) => (
            <button key={m.id} className={`mo${mode === m.id ? " sel" : ""}`} onClick={() => (m.id === "available" ? save("available", null) : setMode(m.id))}>
              <span className="st-emoji" aria-hidden>{m.emoji}</span>
              <span className="tx">{m.label}{m.id === "available" && <small>Donna stays quiet; contacts use @donna</small>}</span>
              {mode === m.id && m.id !== "available" && <Icon name="check" size={14} className="ck" />}
            </button>
          ))}
          <hr />
          {DETAIL_MODES.includes(mode) && (
            <div className="st-field">
              <label htmlFor="st-detail">{mode === "working" ? "What are you working on? (shown to contacts)" : "Your status, as contacts will read it"}</label>
              <input id="st-detail" className="input" maxLength={120} value={detail} onChange={(e) => setDetail(e.target.value)}
                placeholder={mode === "working" ? "the Q4 product launch" : "travelling to Pune"} />
            </div>
          )}
          <div className="st-field">
            <span className="lbl">Until</span>
            <div className="st-chips">
              {untilChoices().map(([label, value]) => (
                <button key={label} className={`chip${choice === label ? " on" : ""}`} onClick={() => { setChoice(label); setUntil(value); }}>{label}</button>
              ))}
            </div>
            <input className="input mono" type="datetime-local" aria-label="Until (date and time)"
              value={until ? localInput(until) : ""} onChange={(e) => {
                const t = e.target.value ? new Date(e.target.value).getTime() / 1000 : null;
                setUntil(t); setChoice(t ? "custom" : "Until I change it");
              }} />
          </div>
          <div className="st-act">
            <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary btn-sm" disabled={busy || (mode === "custom" && !detail.trim())} onClick={() => save()}>Set status</button>
          </div>
        </div>
      )}
      <button ref={btn} className={`s-avail${st?.away ? " away" : ""}`} onClick={open ? onClose : onOpen} aria-expanded={open}
        title={st?.away ? `Contacts are told: “${st.phrase}”` : "You're available"}>
        <span className="st-emoji" aria-hidden>{st?.emoji ?? "🟢"}</span>
        <span className="st-tx">
          <b>{st ? (st.mode === "custom" && st.detail ? st.detail : st.label) : "Status"}</b>
          <small>{st ? [st.mode === "working" && st.detail ? `“${st.detail}”` : "", st.until_text, SOURCE[st.source]].filter(Boolean).join(" · ") || "Donna stays quiet" : "Loading…"}</small>
        </span>
        <Icon name="chevdown" size={13} className="st-chev" />
      </button>
    </div>
  );
}

export function StatusPill({ data, onClick }: { data: Availability | null; onClick: () => void }) {
  const st = data?.status;
  if (!st?.away) return null;
  return (
    <button className="tb-pill st-pill" onClick={onClick} title={`Contacts are told: “${st.phrase}”`}>
      <span aria-hidden>{st.emoji}</span><span className="lb">{st.label}{st.until_text ? ` ${st.until_text}` : ""}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ Settings → Availability & greetings */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const MENU_META: Record<MenuOptionId, { emoji: string; label: string }> = {
  message: { emoji: "📩", label: "Pass a message" }, meeting: { emoji: "📅", label: "Schedule a meeting" },
  callback: { emoji: "📞", label: "Request a call back" }, urgent: { emoji: "⚡", label: "It's urgent" },
};

export function AwaySettings({ settings, save }: { settings: Settings; save: (patch: Partial<Settings>, msg?: string) => void }) {
  const [sleep, setSleep] = useState(settings.sleep_hours);
  const [hours, setHours] = useState(settings.meeting_hours);
  useEffect(() => setSleep(settings.sleep_hours), [settings.sleep_hours]);
  useEffect(() => setHours(settings.meeting_hours), [settings.meeting_hours]);
  const meta = MENU_META;
  const options = settings.menu_options;
  const move = (i: number, d: number) => {
    const next = [...options];
    const j = i + d;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    save({ menu_options: next });
  };
  const toggle = (id: MenuOptionId, enabled: boolean) => save({ menu_options: options.map((o) => (o.id === id ? { ...o, enabled } : o)) });
  const help: Record<MenuOptionId, string> = {
    message: "Repeated back word for word, then you're notified",
    meeting: "Free slots from your calendar, needs your approval, Google Meet link on approval",
    callback: "A request plus a notification for you",
    urgent: "Follows the urgent toggles below",
  };

  return (
    <article className="card scard">
      <span className="eyebrow">Availability</span>
      <h2>Away greeting and quick menu</h2>
      <p>While your status isn't Available, Donna greets saved contacts who write to you (once per status, never in groups)
        and offers a quick menu: a WhatsApp poll they tap, or a numbered list elsewhere.</p>

      <div className="srow"><span className="grow">Greet contacts while I'm away<small className="help">Saved contacts only; unknown numbers are left for you</small></span>
        <Switch checked={settings.greeting_enabled} label="Away greeting" onChange={(v) => save({ greeting_enabled: v }, v ? "Away greeting on." : "Away greeting off.")} /></div>
      <div className="srow"><span className="grow">Mention {settings.trigger_word || "@donna"} in the greeting<small className="help">“…or type {settings.trigger_word || "@donna"} anytime to talk to me”</small></span>
        <Switch checked={settings.greeting_mention_trigger} label="Mention trigger word" onChange={(v) => save({ greeting_mention_trigger: v })} /></div>

      <span className="label">Menu options</span>
      <div className="toolrows menu-opts">
        {options.map((o, i) => (
          <div key={o.id} className="toolrow">
            <span className="st-emoji" aria-hidden>{meta[o.id]?.emoji}</span>
            <span className="d grow"><b>{meta[o.id]?.label}</b><small className="help">{help[o.id]}</small></span>
            <button className="btn btn-ghost btn-icon btn-sm" aria-label={`Move ${meta[o.id]?.label} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button className="btn btn-ghost btn-icon btn-sm" aria-label={`Move ${meta[o.id]?.label} down`} disabled={i === options.length - 1} onClick={() => move(i, 1)}>↓</button>
            <Switch checked={o.enabled} label={meta[o.id]?.label ?? o.id} onChange={(v) => toggle(o.id, v)} />
          </div>
        ))}
      </div>

      <span className="label">Urgent requests</span>
      <p className="help">“It's urgent” always lands in your Inbox. Choose whether it also pings your phone.</p>
      <div className="srow"><span className="grow">Ping me during Do not disturb</span>
        <Switch checked={settings.urgent_notify_dnd} label="Urgent during DND" onChange={(v) => save({ urgent_notify_dnd: v })} /></div>
      <div className="srow"><span className="grow">Ping me while Sleeping</span>
        <Switch checked={settings.urgent_notify_sleeping} label="Urgent while sleeping" onChange={(v) => save({ urgent_notify_sleeping: v })} /></div>

      <span className="label">Automatic status</span>
      <div className="srow"><span className="grow">“In a meeting” during Google Calendar events<small className="help">Skips all-day events and events marked free. A status you set yourself wins until it ends.</small></span>
        <Switch checked={settings.calendar_auto_status} label="Calendar status" onChange={(v) => save({ calendar_auto_status: v })} /></div>
      <div className="srow wrap"><span className="grow">Sleeping hours</span>
        <input className="input mono st-time" type="time" aria-label="Sleep from" value={sleep.start} onChange={(e) => setSleep({ ...sleep, start: e.target.value })}
          onBlur={() => save({ sleep_hours: sleep })} />
        <span className="meta">to</span>
        <input className="input mono st-time" type="time" aria-label="Sleep until" value={sleep.end} onChange={(e) => setSleep({ ...sleep, end: e.target.value })}
          onBlur={() => save({ sleep_hours: sleep })} />
        <Switch checked={sleep.enabled} label="Sleeping hours" onChange={(v) => save({ sleep_hours: { ...sleep, enabled: v } })} /></div>

      <span className="label">Meetings booked through Donna</span>
      <div className="srow wrap"><span className="grow">Slots Donna offers<small className="help">She only offers times your calendar shows as free</small></span>
        <input className="input mono st-time" type="time" aria-label="Meetings from" value={hours.start} onChange={(e) => setHours({ ...hours, start: e.target.value })}
          onBlur={() => save({ meeting_hours: hours })} />
        <span className="meta">to</span>
        <input className="input mono st-time" type="time" aria-label="Meetings until" value={hours.end} onChange={(e) => setHours({ ...hours, end: e.target.value })}
          onBlur={() => save({ meeting_hours: hours })} />
      </div>
      <div className="st-chips days" role="group" aria-label="Meeting days">
        {DAYS.map((d, i) => {
          const on = hours.days.includes(i);
          return <button key={d} className={`chip${on ? " on" : ""}`} aria-pressed={on}
            onClick={() => { const days = on ? hours.days.filter((x) => x !== i) : [...hours.days, i].sort(); if (days.length) save({ meeting_hours: { ...hours, days } }); }}>{d}</button>;
        })}
      </div>
      <div className="srow"><span className="grow">Default length</span>
        <select className="select st-sel" aria-label="Default meeting length" value={settings.meeting_default_minutes}
          onChange={(e) => save({ meeting_default_minutes: Number(e.target.value) })}>
          {[15, 30, 45, 60].map((m) => <option key={m} value={m}>{m} min</option>)}</select></div>
      <p className="help"><b>Contact cancels:</b> removed from your calendar automatically, and you're told. <b>Contact reschedules:</b> Donna checks
        your calendar, then asks you; the original time stays booked until you approve. From your phone: <span className="mono">approve R12</span> /
        <span className="mono"> decline R12</span>, and <span className="mono">status sleeping until 7am</span>.</p>
    </article>
  );
}
