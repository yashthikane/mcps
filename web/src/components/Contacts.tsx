import { useCallback, useEffect, useState } from "react";
import { api, type CapLevel, type Capability, type Contact, type ContactMode, type Memory } from "../api";
import { Icon } from "../icons";
import { timezones } from "./Settings";
import { Dialog, DialogHead, Result, Switch, timeAgo, useToast } from "./ui";

export const CONTACT_MODES: { id: ContactMode; label: string; hint: string }[] = [
  { id: "disabled", label: "Disabled", hint: "Donna ignores them. Their messages are only stored." },
  { id: "suggest", label: "Suggest", hint: "Donna drafts each reply; you approve, edit or reject it." },
  { id: "auto", label: "Auto reply", hint: "Donna answers within the permissions below. Actions still need your OK." },
  { id: "human_first", label: "Human first", hint: "You get an alert; Donna stays silent." },
];
const CHANNELS: [string, string][] = [["whatsapp", "WhatsApp"], ["telegram", "Telegram"]];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];  // backend: Monday = 0

export default function ContactsPage() {
  const toast = useToast();
  const [list, setList] = useState<Contact[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const load = useCallback(async () => {
    try { setList(await api.contacts(q)); } catch (e) { toast((e as Error).message, "err"); }
  }, [q, toast]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);

  const known = (list ?? []).filter((c) => !c.discovered);
  const discovered = (list ?? []).filter((c) => c.discovered);
  const owner = known.find((c) => c.is_owner);

  return (
    <div className="page">
      <div className="phead">
        <div>
          <span className="eyebrow"><span className="sq">■</span> Channels // who may talk to Donna</span>
          <h1>Contacts</h1>
          <p>Nobody gets an answer until you enable them here. For each contact choose how Donna replies and exactly what she may do
            for them. These rules are enforced by Donna's code, not just suggested to the AI.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setAdding(true)}><Icon name="plus" />Add contact</button>
      </div>
      {!owner && list !== null && (
        <div className="card sched-health" role="status">
          <Icon name="key" />
          <div className="grow"><b>Add yourself as the owner.</b>
            <span className="help">Your own WhatsApp or Telegram then gets full access to Donna, alerts, briefings and approval requests
              (reply “approve CODE”). Add a contact with your number and turn on “This is me”.</span></div>
        </div>
      )}
      <div className="srow"><input className="input grow" placeholder="Search by name, number or ID" value={q} onChange={(e) => setQ(e.target.value)}
        aria-label="Search contacts" /></div>

      <div className="sec-h"><h2>Contacts</h2><span className="meta">{known.length}</span></div>
      {list === null ? <span className="meta">Loading…</span> : known.length === 0 ? (
        <div className="card tasks-empty"><Icon name="smile" size={20} /><p>No contacts yet. Add the people Donna may talk to, or enable
          someone from “Discovered” after they message you.</p></div>
      ) : <div className="tlist">{known.map((c) => <ContactRow key={c.id} c={c} onOpen={() => setOpen(c.id)} />)}</div>}

      {discovered.length > 0 && (<>
        <div className="sec-h"><h2>Discovered</h2><span className="meta">people who messaged Donna but aren't enabled</span></div>
        <div className="tlist">{discovered.map((c) => <ContactRow key={c.id} c={c} onOpen={() => setOpen(c.id)} />)}</div>
      </>)}

      {adding && <AddContact onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); load(); setOpen(c.id); }} />}
      {open && <ContactEditor id={open} onClose={() => { setOpen(null); load(); }} />}
    </div>
  );
}

function ContactRow({ c, onOpen }: { c: Contact; onOpen: () => void }) {
  const mode = CONTACT_MODES.find((m) => m.id === c.mode)!;
  return (
    <article className="card trow">
      <div className="trow-main">
        <span className="cc-ic"><Icon name={c.is_owner ? "key" : "smile"} size={16} /></span>
        <button className="trow-title linkbtn" onClick={onOpen}>
          <b>{c.display_name}{c.is_owner && <span className="tag">you · owner</span>}</b>
          <span className="meta">{c.identities.map((i) => `${i.channel_id === "whatsapp" ? "WhatsApp" : "Telegram"} ${i.phone ? `+${i.phone}` : i.external_id}`).join(" · ") || "no channel yet"}
            {c.last_interaction_at ? ` · last message ${timeAgo(c.last_interaction_at)}` : ""}</span>
        </button>
        {c.paused && <span className="pill off">Paused</span>}
        {!c.is_owner && <span className={`pill ${c.mode === "auto" ? "ok" : c.mode === "disabled" ? "off" : "warn"}`}>{mode.label}</span>}
        <button className="btn btn-ghost btn-sm" onClick={onOpen}><Icon name="pen" />{c.discovered ? "Review" : "Edit"}</button>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ add */
function AddContact({ onClose, onSaved }: { onClose: () => void; onSaved: (c: Contact) => void }) {
  const [f, setF] = useState({ display_name: "", channel_id: "whatsapp", address: "", mode: "suggest" as ContactMode, is_owner: false });
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      onSaved(await api.createContact({ ...f, mode: f.is_owner ? "auto" : f.mode }));
    } catch (e) {
      setErr((e as Error).message);
      setSaving(false);
    }
  };
  return (
    <Dialog open onClose={onClose} labelledBy="ac-title" className="task-dlg">
      <div className="dlg">
        <DialogHead id="ac-title" eyebrow="■ Contacts // new" title="Add a contact" onClose={onClose} />
        <div className="dlg-body">
          <div className="field"><label htmlFor="ac-name">Name</label>
            <input className="input" id="ac-name" autoFocus value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></div>
          <div className="two">
            <div className="field"><label htmlFor="ac-ch">Channel</label>
              <select className="select" id="ac-ch" value={f.channel_id} onChange={(e) => setF({ ...f, channel_id: e.target.value })}>
                {CHANNELS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>
            <div className="field"><label htmlFor="ac-addr">{f.channel_id === "whatsapp" ? "WhatsApp number" : "Telegram user ID"}</label>
              <input className="input mono" id="ac-addr" value={f.address} placeholder={f.channel_id === "whatsapp" ? "+91 98123 45678" : "123456789"}
                onChange={(e) => setF({ ...f, address: e.target.value })} /></div>
          </div>
          <span className="help">{f.channel_id === "whatsapp" ? "Include the country code." : "Telegram has no lookup by phone: the easiest way is to ask them to message your bot, then enable them under Discovered."}</span>
          <div className="srow"><div className="grow"><b>This is me</b><span>Your own number: full access, alerts and approvals.</span></div>
            <Switch checked={f.is_owner} label="This is me" onChange={(v) => setF({ ...f, is_owner: v })} /></div>
          {!f.is_owner && <ModeSeg value={f.mode} onChange={(mode) => setF({ ...f, mode })} />}
          {err && <Result kind="err">{err}</Result>}
        </div>
        <div className="dlg-foot">
          <span className="meta grow">You can set permissions next.</span>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={!f.display_name.trim() || !f.address.trim() || saving} onClick={save}>Add contact</button>
        </div>
      </div>
    </Dialog>
  );
}

function ModeSeg({ value, onChange }: { value: ContactMode; onChange: (m: ContactMode) => void }) {
  const idx = CONTACT_MODES.findIndex((m) => m.id === value);
  return (<>
    <span className="label">How Donna handles their messages</span>
    <div className="seg" role="radiogroup" aria-label="Contact mode">
      <span className="ind" aria-hidden="true" style={{ width: "calc(25% - 2px)", transform: `translateX(${idx * 100}%)` }} />
      {CONTACT_MODES.map((m) => <button key={m.id} role="radio" aria-checked={value === m.id} onClick={() => onChange(m.id)}>{m.label}</button>)}
    </div>
    <span className={`help${value === "auto" ? " st-warn" : ""}`}>{CONTACT_MODES[idx].hint}</span>
  </>);
}

/* ------------------------------------------------------------------ communication profile */
const RELATIONSHIPS: [Contact["relationship"], string][] = [["", "Not set"], ["family", "Family"], ["friend", "Friend"],
  ["client", "Business client"], ["colleague", "Colleague"], ["vendor", "Vendor"], ["other", "Other"]];

/** How Donna talks to this person. Empty fields use the relationship's defaults. */
function ProfileSection({ c, patch, requestTypes }: {
  c: Contact; patch: (p: Partial<Contact>, msg?: string) => Promise<void>; requestTypes: Record<string, string>;
}) {
  const text = (key: keyof Contact, label: string, placeholder: string, long = false) => {
    const value = String(c[key] ?? "");
    const save = (v: string) => { if (v !== value) patch({ [key]: v } as Partial<Contact>, `${label} saved.`); };
    return (
      <div className="field"><label htmlFor={`cp-${key}`}>{label}</label>
        {long ? <textarea className="input textarea" id={`cp-${key}`} rows={2} defaultValue={value} placeholder={placeholder} onBlur={(e) => save(e.target.value)} />
          : <input className="input" id={`cp-${key}`} defaultValue={value} placeholder={placeholder} onBlur={(e) => save(e.target.value)} />}
      </div>
    );
  };
  const allowed = c.allowed_requests ?? [];
  return (<>
    <div className="sec-h"><h2>How Donna talks to them</h2></div>
    <p className="help">Donna always introduces herself as your AI assistant and never speaks as you. Empty fields follow the relationship.</p>
    <div className="two">
      <div className="field"><label htmlFor="cp-rel">Relationship</label>
        <select className="select" id="cp-rel" value={c.relationship} onChange={(e) => patch({ relationship: e.target.value as Contact["relationship"] }, "Relationship saved.")}>
          {RELATIONSHIPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
      {text("relationship_label", "Who they are to you", "e.g. Mother, Client at ABC Ltd")}
    </div>
    <div className="two">
      {text("address_as", "Donna calls them", "e.g. Aai")}
      {text("greeting", "Greeting", "e.g. Namaskar")}
    </div>
    <div className="two">
      {text("language", "Language", "Same as theirs, or e.g. Marathi + English")}
      <div className="field"><label htmlFor="cp-script">Script</label>
        <select className="select" id="cp-script" value={c.script} onChange={(e) => patch({ script: e.target.value as Contact["script"] }, "Script saved.")}>
          <option value="">Match theirs</option><option value="latin">Roman letters (e.g. Romanized Marathi)</option><option value="devanagari">Devanagari</option></select></div>
    </div>
    <div className="two">
      {text("tone", "Tone", "e.g. warm, familiar and respectful")}
      <div className="field"><label htmlFor="cp-form">Formality</label>
        <select className="select" id="cp-form" value={c.formality} onChange={(e) => patch({ formality: e.target.value as Contact["formality"] }, "Formality saved.")}>
          <option value="">Relationship default</option><option value="casual">Casual</option><option value="neutral">Neutral</option><option value="formal">Formal</option></select></div>
    </div>
    {text("intro_message", "Introduction", c.default_intro ?? "", true)}
    <span className="help">Sent the first time Donna replies to them. Leave empty to use the one shown above.</span>
    {text("background", "Background (for understanding only)", "e.g. Often shares saree products and education links. Donna won't bring these up herself.", true)}
    {text("special_instructions", "Special instructions", "e.g. If she asks for a call, mark it urgent.", true)}
    <span className="help">Background and instructions never give them extra permissions; only the switches below do.</span>
    <span className="label">Requests Donna may take for you</span>
    <div className="srow wrap">
      <label className="chk"><input type="checkbox" checked={allowed.length === 0}
        onChange={(e) => patch({ allowed_requests: e.target.checked ? [] : ["callback", "personal_message"] }, "Saved.")} />All types</label>
      {allowed.length > 0 && Object.entries(requestTypes).map(([t, label]) => (
        <label key={t} className="chk"><input type="checkbox" checked={allowed.includes(t)}
          onChange={(e) => patch({ allowed_requests: e.target.checked ? [...allowed, t] : allowed.filter((x) => x !== t) }, "Saved.")} />{label}</label>
      ))}
    </div>
  </>);
}

/* ------------------------------------------------------------------ editor */
function ContactEditor({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useToast();
  const [c, setC] = useState<Contact | null>(null);
  const [caps, setCaps] = useState<Capability[]>([]);
  const [scope, setScope] = useState("");  // "" = all channels, or a channel override
  const [memories, setMemories] = useState<Memory[]>([]);
  const [note, setNote] = useState("");
  const [ident, setIdent] = useState({ channel_id: "whatsapp", address: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [requestTypes, setRequestTypes] = useState<Record<string, string>>({});
  useEffect(() => { api.requestTypes().then((r) => setRequestTypes(r.types)).catch(() => {}); }, []);
  const zones = timezones(c?.timezone || "");

  const load = useCallback(async () => {
    try {
      const [contact, catalog, mem] = await Promise.all([api.contact(id), api.capabilities(), api.contactMemories(id)]);
      setC(contact);
      setCaps(catalog);
      setMemories(mem);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }, [id, toast]);
  useEffect(() => { load(); }, [load]);

  const patch = async (p: Partial<Contact>, msg?: string) => {
    try {
      const next = await api.updateContact(id, p);
      setC((x) => (x ? { ...x, ...next } : next));
      if (msg) toast(msg);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); toast(msg); load(); } catch (e) { toast((e as Error).message, "err"); }
  };
  if (!c) return null;

  const policies = c.policies ?? { "": {} };
  const base = policies[""] ?? {};
  const own = policies[scope];
  const effective: Record<string, CapLevel> = { ...base, ...(scope ? own ?? {} : {}) };
  const setCap = (cap: string, level: CapLevel) => {
    const next = { ...(scope ? own ?? {} : base), [cap]: level };
    run(() => api.setPolicy(id, scope, next), "Permissions saved.");
  };
  const hours = c.working_hours ?? {};
  const anyTime = !hours.start;
  const setHours = (h: typeof hours | null) => patch({ working_hours: (h ?? {}) as Contact["working_hours"] }, "Working hours saved.");

  return (
    <Dialog open onClose={onClose} labelledBy="ce-title" className="contact-dlg">
      <div className="dlg">
        <DialogHead id="ce-title" eyebrow={c.is_owner ? "■ Contacts // owner" : c.discovered ? "■ Contacts // discovered" : "■ Contacts // edit"}
          title={c.display_name} onClose={onClose} />
        <div className="dlg-body">
          {c.discovered && <Result kind="ok">This person messaged Donna but isn't enabled. Choose a mode to let Donna answer them.</Result>}
          <div className="two">
            <div className="field"><label htmlFor="ce-name">Name</label>
              <input className="input" id="ce-name" defaultValue={c.display_name} onBlur={(e) => e.target.value.trim() !== c.display_name && patch({ display_name: e.target.value }, "Renamed.")} /></div>
            <div className="field"><label htmlFor="ce-tz">Their timezone</label>
              <select className="select" id="ce-tz" value={c.timezone} onChange={(e) => patch({ timezone: e.target.value }, "Timezone saved.")}>
                <option value="">Same as mine</option>{zones.map((z) => <option key={z} value={z}>{z}</option>)}</select></div>
          </div>

          {c.is_owner ? (
            <Result kind="ok">This is you. Messages from these identities get full owner access, like the web chat. Actions follow
              your permission mode; in Manual mode Donna asks you to reply “approve CODE”.</Result>
          ) : (<>
            <ModeSeg value={c.mode} onChange={(mode) => patch({ mode }, "Mode saved.")} />
            <div className="srow"><div className="grow"><b>Pause</b><span>Stop answering them for now, whatever the mode.</span></div>
              <Switch checked={c.paused} label="Pause contact" onChange={(v) => patch({ paused: v }, v ? "Paused." : "Resumed.")} /></div>
            <ProfileSection c={c} patch={patch} requestTypes={requestTypes} />

            <span className="label">Auto replies only during</span>
            <div className="srow wrap">
              <label className="chk"><input type="checkbox" checked={anyTime} onChange={(e) => setHours(e.target.checked ? null : { days: [0, 1, 2, 3, 4], start: "09:00", end: "20:00" })} />Any time</label>
              {!anyTime && (<>
                {DAYS.map((d, i) => (
                  <label key={d} className="chk"><input type="checkbox" checked={(hours.days ?? []).includes(i)}
                    onChange={(e) => setHours({ ...hours, days: e.target.checked ? [...(hours.days ?? []), i] : (hours.days ?? []).filter((x) => x !== i) })} />{d}</label>
                ))}
                <input className="input mono hours" type="time" aria-label="From" value={hours.start} onChange={(e) => setHours({ ...hours, start: e.target.value })} />
                <span className="meta">to</span>
                <input className="input mono hours" type="time" aria-label="To" value={hours.end} onChange={(e) => setHours({ ...hours, end: e.target.value })} />
              </>)}
            </div>
            <span className="help">Outside these hours, and above the hourly reply limit, Donna drafts replies for your approval instead.</span>

            <div className="sec-h"><h2>What Donna may do for them</h2>
              <select className="select" aria-label="Channel" value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="">All channels</option>{CHANNELS.map(([cid, label]) => <option key={cid} value={cid}>{label} only</option>)}</select>
            </div>
            {scope && (
              <span className="help">{own ? <>Overrides for {scope}. <button className="linkbtn" onClick={() => run(() => api.clearPolicy(id, scope), "Override removed.")}>Use the default for all channels</button></>
                : `No override for ${scope}: it uses the defaults. Changing anything here creates one.`}</span>
            )}
            <div className="capgrid">
              {caps.map((cap) => {
                const level = effective[cap.id] ?? "off";
                return (
                  <div key={cap.id} className="caprow">
                    <div className="grow"><b>{cap.label}</b><span className="help">{cap.help}</span></div>
                    <div className="capseg" role="radiogroup" aria-label={cap.label}>
                      {(["off", "on", "approval"] as CapLevel[]).map((l) => (
                        <button key={l} role="radio" aria-checked={level === l || (cap.forced_approval && level === "on" && l === "approval")}
                          disabled={cap.forced_approval && l === "on"} title={cap.forced_approval && l === "on" ? "Always needs your approval" : undefined}
                          onClick={() => setCap(cap.id, l)}>{l === "off" ? "Off" : l === "on" ? "Allowed" : "Ask me"}</button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </>)}

          <div className="sec-h"><h2>Identities</h2></div>
          <div className="toolrows">
            {c.identities.map((i) => (
              <div key={i.id} className="toolrow">
                <span className="tag">{i.channel_id === "whatsapp" ? "WhatsApp" : "Telegram"}</span>
                <span className="mono grow">{i.external_id}{i.phone && !i.external_id.startsWith(i.phone) ? ` (+${i.phone})` : ""}</span>
                <span className="meta">{i.display_name}</span>
                <button className="btn btn-ghost btn-icon btn-sm" aria-label="Remove identity" onClick={() => run(() => api.removeIdentity(id, i.id), "Removed.")}><Icon name="x" /></button>
              </div>
            ))}
          </div>
          <div className="srow">
            <select className="select" aria-label="Channel" value={ident.channel_id} onChange={(e) => setIdent({ ...ident, channel_id: e.target.value })}>
              {CHANNELS.map(([cid, label]) => <option key={cid} value={cid}>{label}</option>)}</select>
            <input className="input mono grow" placeholder={ident.channel_id === "whatsapp" ? "+91 98123 45678" : "Telegram user ID"} value={ident.address}
              onChange={(e) => setIdent({ ...ident, address: e.target.value })} aria-label="Number or ID" />
            <button className="btn btn-secondary btn-sm" disabled={!ident.address.trim()}
              onClick={() => run(async () => { await api.addIdentity(id, ident.channel_id, ident.address); setIdent({ ...ident, address: "" }); }, "Identity added.")}>Add</button>
          </div>

          <div className="sec-h"><h2>What Donna remembers about them</h2></div>
          {!c.is_owner && (
            <div className="srow"><div className="grow"><b>Share memory across channels</b><span>Off: what Donna learns on WhatsApp stays with WhatsApp.</span></div>
              <Switch checked={c.share_memory} label="Share memory across channels" onChange={(v) => patch({ share_memory: v }, "Saved.")} /></div>
          )}
          {memories.length === 0 ? <span className="meta">Nothing yet.</span> : (
            <div className="toolrows">
              {memories.map((m) => (
                <div key={m.id} className="toolrow">
                  <span className="d grow">{m.content}</span>
                  <span className="tag">{m.source === "owner" ? "you" : "Donna"}</span>
                  <button className="btn btn-ghost btn-icon btn-sm" aria-label="Forget" onClick={() => run(() => api.deleteContactMemory(id, m.id), "Forgotten.")}><Icon name="trash" /></button>
                </div>
              ))}
            </div>
          )}
          <div className="srow">
            <input className="input grow" placeholder="Add a note, e.g. prefers calls after 6 PM" value={note} onChange={(e) => setNote(e.target.value)} aria-label="New note" />
            <button className="btn btn-secondary btn-sm" disabled={!note.trim()} onClick={() => run(async () => { await api.addContactMemory(id, note); setNote(""); }, "Saved.")}>Add</button>
          </div>
        </div>
        <div className="dlg-foot">
          {confirmDelete ? (<>
            <span className="help grow">Delete {c.display_name}, their conversations and memories?</span>
            <button className="btn btn-ghost" onClick={() => setConfirmDelete(false)}>Keep</button>
            <button className="btn btn-danger" onClick={async () => { await api.deleteContact(id); toast("Deleted."); onClose(); }}>Delete</button>
          </>) : (<>
            <button className="btn btn-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" />Delete</button>
            {!c.is_owner && <button className="btn btn-ghost" onClick={() => run(() => api.makeOwner(id), "Set as you (owner).")}>This is me</button>}
            <span className="grow" />
            <button className="btn btn-primary" onClick={onClose}>Done</button>
          </>)}
        </div>
      </div>
    </Dialog>
  );
}
