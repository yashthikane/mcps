import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Connection, type ServerConfig, type ToolInfo } from "../api";
import { Icon } from "../icons";
import { Dialog, DialogHead, Result, StatePill, Switch, useToast } from "./ui";

export type WizardKind = "google" | "notion" | "mcp";

/* ============================================================================ page */
export default function ConnectionsPage({ connections, reload, openWizard, onTools }: {
  connections: Connection[]; reload: () => Promise<void>; openWizard: (k: WizardKind) => void; onTools: (c?: Connection) => void;
}) {
  const connected = connections.filter((c) => c.state === "connected");
  const ready = connected.reduce((n, c) => n + c.tools.filter((t) => t.enabled).length, 0);
  const attention = connections.filter((c) => ["needs_reauth", "needs_auth", "error"].includes(c.state)).length;
  const askFirst = connected.reduce((n, c) => n + c.tools.filter((t) => t.enabled && t.confirm).length, 0);
  return (
    <div className="page">
      <div className="phead">
        <div>
          <span className="eyebrow"><span className="sq">■</span> Integrations // MCP</span>
          <h1>Connections</h1>
          <p>Apps and MCP servers Donna can use. Each one has a guided setup, and you can turn tools on or off individually.</p>
        </div>
        <button className="btn btn-primary" onClick={() => openWizard("mcp")}><Icon name="plus" />Add MCP server</button>
      </div>
      <div className="stat-row">
        <Stat label="Connected" value={connected.length} note={`of ${connections.length} connections`} />
        <Stat label="Tools ready" value={ready} note="available to Donna now" />
        <Stat label="Need attention" value={attention} note={attention ? "see below" : "all healthy"} warn={attention > 0} />
        <Stat label="Ask first" value={askFirst} note="tools that wait for approval" />
      </div>
      <div className="sec-h"><h2>Your connections</h2><button className="btn btn-secondary btn-sm" onClick={() => onTools()}><Icon name="wrench" />All tools</button></div>
      <div className="cgrid">
        {connections.map((c) => <ConnCard key={c.id} c={c} reload={reload} openWizard={openWizard} onTools={() => onTools(c)} />)}
        <button className="card ccard addcard" onClick={() => openWizard("mcp")}>
          <span className="cc-ic"><Icon name="plus" size={18} /></span>
          <span><b style={{ display: "block", fontWeight: 600, fontSize: 14, marginBottom: 4 }}>Add an MCP server</b>
            <span className="cc-note">Filesystem, Fetch, Git, Memory, or any command or URL.</span></span>
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value, note, warn }: { label: string; value: number; note: string; warn?: boolean }) {
  return (
    <div className="stat"><span className="eyebrow">{label}</span><span className="num">{value}</span>
      <span className="chg" style={warn ? { color: "var(--amber)" } : undefined}>{note}</span></div>
  );
}

function ConnCard({ c, reload, openWizard, onTools }: {
  c: Connection; reload: () => Promise<void>; openWizard: (k: WizardKind) => void; onTools: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const enabledCount = c.tools.filter((t) => t.enabled).length;
  const act = async (label: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(label);
    try {
      await fn();
      if (ok) toast(ok);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
      await reload();
    }
  };
  const google = c.setup === "google";
  const toggleable = c.state === "connected" || c.state === "off";
  const auth = c.auth_state ?? c.state;

  let actions: ReactNode = null;
  if (confirm) {
    actions = (
      <>
        <span className="help" style={{ marginRight: "auto" }}>{c.kind === "mcp" ? "Remove this server?" : "Disconnect?"}</span>
        <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(false)}>Cancel</button>
        <button className="btn btn-danger btn-sm" onClick={() => {
          setConfirm(false);
          act("remove", () => (c.kind === "mcp" ? api.removeServer(c.id) : google ? api.disconnectGoogle() : api.disconnectNotion()),
            c.kind === "mcp" ? `Removed ${c.name}.` : `${google ? "Google" : c.name} disconnected.`);
        }}>{c.kind === "mcp" ? "Remove" : "Disconnect"}</button>
      </>
    );
  } else if (auth === "not_set_up" || auth === "needs_auth") {
    actions = <button className="btn btn-primary btn-sm" onClick={() => openWizard(c.setup as WizardKind)}>{auth === "needs_auth" ? "Sign in" : "Connect"}</button>;
  } else if (auth === "needs_reauth") {
    actions = (
      <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => {
        toast("Finish signing in on the Google tab that just opened.");
        act("reauth", api.authorizeGoogle, "Google re-authorized.");
      }}>{busy === "reauth" ? "Waiting for Google…" : "Re-authorize"}</button>
    );
  } else if (c.kind === "mcp" && c.state === "error") {
    actions = (
      <>
        <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => act("restart", async () => {
          const r = await api.restartServer(c.id);
          if (r.state !== "connected") throw new ApiError(r.error || "Still not running.", 400);
        }, `${c.name} is running.`)}>{busy === "restart" ? "Restarting…" : "Restart"}</button>
        <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(true)}>Remove</button>
      </>
    );
  } else if (c.setup === "notion" && auth === "error") {
    actions = <button className="btn btn-primary btn-sm" onClick={() => openWizard("notion")}>Update secret</button>;
  } else {
    actions = (
      <>
        <button className="btn btn-ghost btn-sm" onClick={onTools}>Tools</button>
        {google && c.state === "connected" && (
          <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => act("test", async () => {
            const r = await api.testGoogle();
            toast(`Google works: ${r.events.length} upcoming events, ${r.emails.length} recent emails.`);
          })}>{busy === "test" ? "Testing…" : "Test"}</button>
        )}
        {c.kind === "mcp" && c.state === "connected" && (
          <button className="btn btn-secondary btn-sm" disabled={!!busy} onClick={() => act("restart", () => api.restartServer(c.id), `${c.name} restarted.`)}>
            {busy === "restart" ? "Restarting…" : "Restart"}</button>
        )}
        {(c.setup === "google" || c.setup === "notion" || c.kind === "mcp") && (
          <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(true)}>{c.kind === "mcp" ? "Remove" : "Disconnect"}</button>
        )}
      </>
    );
  }

  return (
    <article className="card ccard">
      <div className="cc-top">
        <span className="cc-ic"><Icon name={c.icon} size={18} /></span>
        <div className="t"><h3>{c.name}</h3><div className="cc-src">{c.source}</div></div>
        <StatePill state={c.state} />
      </div>
      <p className="cc-note">{c.detail || NOTES[c.state] || ""}</p>
      <div className="cc-foot">
        <span className="count">{enabledCount} of {c.tools.length} tools on</span>
        {actions}
        {toggleable && !confirm && (
          <Switch checked={c.state !== "off"} label={`Turn ${c.name} ${c.state === "off" ? "on" : "off"}`} disabled={!!busy}
            onChange={(v) => act("toggle", () => api.patchConnection(c.id, { enabled: v }), `${c.name} turned ${v ? "on" : "off"}.`)} />
        )}
      </div>
    </article>
  );
}

const NOTES: Record<string, string> = {
  not_set_up: "Not connected yet. The guided setup takes a few minutes.",
  needs_auth: "Credentials uploaded. Sign in with Google to finish.",
  needs_reauth: "Google sign-ins in testing mode expire after 7 days. Re-authorize with one click.",
  off: "Turned off. Donna won't use these tools.",
  error: "This connection isn't working.",
};

/* ============================================================================ tools dialog */
export function ToolsDialog({ open, connections, only, onClose, reload }: {
  open: boolean; connections: Connection[]; only?: Connection; onClose: () => void; reload: () => Promise<void>;
}) {
  const toast = useToast();
  const list = only ? connections.filter((c) => c.id === only.id) : connections;
  const toggle = async (c: Connection, tool: ToolInfo, on: boolean) => {
    const off = c.tools.filter((t) => (t.name === tool.name ? !on : !t.enabled)).map((t) => t.name);
    try {
      await api.patchConnection(c.id, { disabled_tools: off });
      await reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const total = connections.reduce((n, c) => n + c.tools.length, 0);
  return (
    <Dialog open={open} onClose={onClose} labelledBy="tools-title">
      <div className="dlg">
        <DialogHead id="tools-title" eyebrow="■ Tool catalog" title={only ? `${only.name} tools` : "Tools"} onClose={onClose} />
        <div className="dlg-body">
          {!only && <p>{total} tools installed. Tools marked <span className="tag warn">asks first</span> always wait for your approval before they run.</p>}
          {list.map((c) => (
            <div key={c.id} className="tgroup">
              <div className="tgroup-h"><Icon name={c.icon} /><b>{c.name}</b><StatePill state={c.state} /></div>
              {c.tools.length ? (
                <div className="toolrows">
                  {c.tools.map((t) => (
                    <div key={t.name} className="toolrow">
                      <div className="grow"><div className="mono">{t.name}</div><div className="d">{t.description}</div></div>
                      {t.confirm && <span className="tag warn">asks first</span>}
                      <Switch checked={t.enabled} label={`Enable ${t.name}`} onChange={(v) => toggle(c, t, v)} />
                    </div>
                  ))}
                </div>
              ) : <span className="help">No tools yet. {c.state === "error" ? c.detail : "Connect it first."}</span>}
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  );
}

/* ============================================================================ wizard frame */
function Wizard({ eyebrow, title, steps, index, onBack, onNext, nextLabel, canNext, busy, onClose, children }: {
  eyebrow: string; title: string; steps: string[]; index: number; onBack: () => void; onNext: () => void;
  nextLabel: string; canNext: boolean; busy?: boolean; onClose: () => void; children: ReactNode;
}) {
  return (
    <Dialog open onClose={onClose} labelledBy="wz-title">
      <div className="dlg">
        <DialogHead id="wz-title" eyebrow={eyebrow} title={title} onClose={onClose} />
        <ol className="stepper">
          {steps.map((s, k) => (
            <li key={s} className={k < index ? "done" : k === index ? "cur" : ""} aria-current={k === index ? "step" : undefined}>
              <span className="bar" /><span className="sl">{String(k + 1).padStart(2, "0")} {s}</span>
            </li>
          ))}
        </ol>
        <div className="dlg-body">{children}</div>
        <div className="dlg-foot">
          <span className="meta grow">Step {index + 1} of {steps.length}</span>
          <button className="btn btn-ghost" onClick={onBack} disabled={index === 0 || busy}>Back</button>
          <button className="btn btn-primary" onClick={onNext} disabled={!canNext || busy}>{nextLabel}</button>
        </div>
      </div>
    </Dialog>
  );
}

function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <li><label><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span>{children}</span></label></li>
  );
}

const ext = (href: string, label: string) => <a href={href} target="_blank" rel="noopener noreferrer">{label}</a>;

/* ============================================================================ Google */
export function GoogleWizard({ startAt, onDone, onClose }: { startAt: number; onDone: () => void; onClose: () => void }) {
  const toast = useToast();
  const [i, setI] = useState(startAt);
  const [checks, setChecks] = useState({ g1: startAt > 0, g2: startAt > 0, g3: startAt > 0, g4: startAt > 1 });
  const [upload, setUpload] = useState<{ state: "idle" | "busy" | "ok" | "err"; text?: string }>({ state: startAt > 2 ? "ok" : "idle", text: startAt > 2 ? "credentials.json already uploaded" : "" });
  const [auth, setAuth] = useState<{ state: "idle" | "busy" | "ok" | "err"; text?: string }>({ state: "idle" });
  const [test, setTest] = useState<{ state: "busy" | "ok" | "err"; events?: string[]; emails?: string[]; text?: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const send = async (file: File) => {
    setUpload({ state: "busy" });
    try {
      const r = await api.uploadGoogleCredentials(file);
      setUpload({ state: "ok", text: `${r.filename} · project ${r.project_id || "(unnamed)"}` });
    } catch (e) {
      setUpload({ state: "err", text: (e as Error).message });
    }
  };
  const authorize = async () => {
    setAuth({ state: "busy" });
    try {
      const r = await api.authorizeGoogle();
      setAuth({ state: "ok", text: r.email });
    } catch (e) {
      setAuth({ state: "err", text: (e as Error).message });
    }
  };
  useEffect(() => {
    if (i !== 4 || test) return;
    setTest({ state: "busy" });
    api.testGoogle().then((r) => setTest({ state: "ok", ...r })).catch((e) => setTest({ state: "err", text: (e as Error).message }));
  }, [i, test]);

  const steps = ["Project", "Sign-in client", "Upload", "Authorize", "Test"];
  const can = [checks.g1 && checks.g2 && checks.g3, checks.g4, upload.state === "ok", auth.state === "ok", test?.state !== "busy"][i];
  const set = (k: keyof typeof checks) => (v: boolean) => setChecks({ ...checks, [k]: v });

  return (
    <Wizard eyebrow="■ Connect // Google" title="Gmail + Google Calendar" steps={steps} index={i}
      onBack={() => setI(i - 1)} onClose={onClose} canNext={!!can} busy={upload.state === "busy" || auth.state === "busy"}
      nextLabel={i === 4 ? "Finish" : "Continue"}
      onNext={() => { if (i < 4) setI(i + 1); else { toast("Google connected. Gmail and Calendar tools are ready."); onDone(); } }}>
      {i === 0 && (<>
        <h3 className="wz-h">Create a Google Cloud project</h3>
        <p>Donna uses your own free Google Cloud project, so your data only goes between this computer and Google.</p>
        <ul className="checks">
          <Check checked={checks.g1} onChange={set("g1")}>Open {ext("https://console.cloud.google.com/projectcreate", "console.cloud.google.com")} and create a project (any name, e.g. “Donna”).</Check>
          <Check checked={checks.g2} onChange={set("g2")}>Enable the {ext("https://console.cloud.google.com/apis/library/gmail.googleapis.com", "Gmail API")}.</Check>
          <Check checked={checks.g3} onChange={set("g3")}>Enable the {ext("https://console.cloud.google.com/apis/library/calendar-json.googleapis.com", "Google Calendar API")}.</Check>
        </ul>
      </>)}
      {i === 1 && (<>
        <h3 className="wz-h">Create a sign-in client</h3>
        <ol className="steps-list">
          <li>Open {ext("https://console.cloud.google.com/auth/overview", "Google Auth Platform")} and click <b>Get started</b>: app name <b>Donna</b>, your email, audience <b>External</b>.</li>
          <li>Under <span className="mono">Audience → Test users</span>, add your own Gmail address.</li>
          <li>Under <span className="mono">Clients → Create client</span>, choose <b>Desktop app</b>, then <b>Download JSON</b>.</li>
        </ol>
        <ul className="checks"><Check checked={checks.g4} onChange={set("g4")}>I downloaded the JSON file</Check></ul>
        <span className="help">Testing mode is free. Google asks you to sign in again every 7 days; Donna shows a one-click Re-authorize button when that happens.</span>
      </>)}
      {i === 2 && (<>
        <h3 className="wz-h">Upload credentials.json</h3>
        <label className={`drop${over ? " over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); }}>
          <input ref={fileRef} type="file" accept=".json,application/json" className="sr" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); }} />
          <Icon name="upload" size={22} /><b>Drop the downloaded JSON here</b><span className="help">or click to choose it (client_secret_….json)</span>
        </label>
        {upload.state === "busy" && <Result kind="busy"><span className="help">Checking the file…</span></Result>}
        {upload.state === "ok" && <Result><span className="okline">✓ {upload.text}</span></Result>}
        {upload.state === "err" && <Result kind="err"><span className="st-err mono" style={{ fontSize: 12 }}>✕ {upload.text}</span></Result>}
        <span className="help">The client ID and secret are stored in Windows Credential Manager, not in a file.</span>
      </>)}
      {i === 3 && (<>
        <h3 className="wz-h">Sign in with Google</h3>
        <p>Your browser opens Google's consent screen. Choose your account and allow access, then come back here.</p>
        <button className="btn btn-primary" style={{ alignSelf: "flex-start" }} onClick={authorize} disabled={auth.state === "busy" || auth.state === "ok"}>
          <Icon name="key" />{auth.state === "busy" ? "Waiting for Google…" : "Authorize with Google"}</button>
        {auth.state === "busy" && <Result kind="busy"><span className="help">Approve access in the browser tab that opened. If Google says the app isn't verified, click <b>Continue</b>: it's your own app.</span></Result>}
        {auth.state === "ok" && <Result><span className="okline">✓ Signed in as {auth.text}</span><div className="chips"><span className="tag">gmail.modify</span><span className="tag">gmail.send</span><span className="tag">calendar</span></div></Result>}
        {auth.state === "err" && <Result kind="err"><span className="st-err mono" style={{ fontSize: 12 }}>✕ {auth.text}</span></Result>}
      </>)}
      {i === 4 && (<>
        <h3 className="wz-h">Check that it works</h3>
        {test?.state === "busy" && <Result kind="busy"><span className="help">Reading your calendar and inbox…</span></Result>}
        {test?.state === "err" && <Result kind="err"><span className="st-err mono" style={{ fontSize: 12 }}>✕ {test.text}</span></Result>}
        {test?.state === "ok" && (<>
          <Result><span className="eyebrow st-ok">■ Next events</span><ul>{test.events?.length ? test.events.map((e) => <li key={e}>{e}</li>) : <li>No upcoming events</li>}</ul></Result>
          <Result><span className="eyebrow st-ok">■ Latest emails</span><ul>{test.emails?.length ? test.emails.map((e) => <li key={e}>{e}</li>) : <li>Inbox is empty</li>}</ul></Result>
        </>)}
      </>)}
    </Wizard>
  );
}

/* ============================================================================ Notion */
export function NotionWizard({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const toast = useToast();
  const [i, setI] = useState(0);
  const [c1, setC1] = useState(false);
  const [c2, setC2] = useState(false);
  const [secret, setSecret] = useState("");
  const [res, setRes] = useState<{ state: "busy" | "ok" | "err"; pages?: string[]; text?: string } | null>(null);
  const connect = async () => {
    setRes({ state: "busy" });
    try {
      const r = await api.connectNotion(secret.trim());
      setRes({ state: "ok", pages: r.pages });
    } catch (e) {
      setRes({ state: "err", text: (e as Error).message });
    }
  };
  const can = [c1, c2, res?.state === "ok"][i];
  return (
    <Wizard eyebrow="■ Connect // Notion" title="Notion" steps={["Integration", "Share pages", "Secret"]} index={i}
      onBack={() => setI(i - 1)} onClose={onClose} canNext={!!can} busy={res?.state === "busy"} nextLabel={i === 2 ? "Finish" : "Continue"}
      onNext={() => { if (i < 2) setI(i + 1); else { toast("Notion connected."); onDone(); } }}>
      {i === 0 && (<>
        <h3 className="wz-h">Create a Notion integration</h3>
        <ol className="steps-list">
          <li>Open {ext("https://www.notion.so/profile/integrations", "notion.so/profile/integrations")} and click <b>New integration</b>.</li>
          <li>Name it <b>Donna</b>, choose your workspace, keep type <b>Internal</b>, and save.</li>
          <li>Under <b>Capabilities</b>, allow read, update and insert content.</li>
        </ol>
        <ul className="checks"><Check checked={c1} onChange={setC1}>I created the integration</Check></ul>
      </>)}
      {i === 1 && (<>
        <h3 className="wz-h">Share pages with Donna</h3>
        <p>Donna only sees pages you share with the integration. Sharing a top-level page also shares everything inside it.</p>
        <ol className="steps-list"><li>Open the page or database in Notion.</li><li>Click <span className="mono">•••</span> (top right) → <span className="mono">Connections</span> → <b>Donna</b>.</li></ol>
        <ul className="checks"><Check checked={c2} onChange={setC2}>I shared at least one page</Check></ul>
      </>)}
      {i === 2 && (<>
        <h3 className="wz-h">Paste the integration secret</h3>
        <div className="field">
          <label htmlFor="n-secret">Internal integration secret</label>
          <div className="srow">
            <input className="input mono grow" id="n-secret" type="password" autoComplete="off" placeholder="ntn_…" value={secret}
              onChange={(e) => { setSecret(e.target.value); setRes(null); }} onKeyDown={(e) => { if (e.key === "Enter" && secret) connect(); }} />
            <button className="btn btn-secondary" onClick={connect} disabled={!secret.trim() || res?.state === "busy"}>Connect</button>
          </div>
          <span className="help">In your integration, open <b>Configuration → Internal integration secret → Show → Copy</b>. It's stored in Windows Credential Manager.</span>
        </div>
        {res?.state === "busy" && <Result kind="busy"><span className="help">Checking with Notion…</span></Result>}
        {res?.state === "err" && <Result kind="err"><span className="st-err mono" style={{ fontSize: 12 }}>✕ {res.text}</span></Result>}
        {res?.state === "ok" && <Result><span className="okline">✓ Connected · {res.pages?.length ? `${res.pages.length} shared item(s)` : "no pages shared yet"}</span>
          {res.pages?.length ? <ul>{res.pages.slice(0, 6).map((pg, k) => <li key={k}>{pg}</li>)}</ul> : <span className="help">Share pages in Notion (step 2) and Donna will see them right away.</span>}</Result>}
      </>)}
    </Wizard>
  );
}

/* ============================================================================ MCP server */
const PRESETS = (home: string) => ({
  filesystem: { name: "Filesystem", icon: "folder", transport: "stdio" as const, command: "npx", args: `-y @modelcontextprotocol/server-filesystem "${home}\\Documents"`, sub: "Read and edit files in a folder" },
  fetch: { name: "Fetch", icon: "globe", transport: "stdio" as const, command: "uvx", args: "mcp-server-fetch", sub: "Read web pages as text" },
  git: { name: "Git", icon: "git", transport: "stdio" as const, command: "uvx", args: `mcp-server-git --repository "${home}\\Desktop"`, sub: "Inspect a repository" },
  memory: { name: "Memory", icon: "brain", transport: "stdio" as const, command: "npx", args: "-y @modelcontextprotocol/server-memory", sub: "A local knowledge graph" },
  custom: { name: "", icon: "terminal", transport: "stdio" as const, command: "", args: "", sub: "Any command or URL" },
});

export function McpWizard({ home, onDone, onClose }: { home: string; onDone: () => void; onClose: () => void }) {
  const toast = useToast();
  const presets = PRESETS(home);
  const [i, setI] = useState(0);
  const [preset, setPreset] = useState<keyof typeof presets | null>(null);
  const [cfg, setCfg] = useState<ServerConfig>({ name: "", transport: "stdio", command: "", args: "", url: "", env: {} });
  const [envRows, setEnvRows] = useState<[string, string][]>([["", ""]]);
  const [test, setTest] = useState<{ state: "busy" | "ok" | "err"; tools?: ToolInfo[]; text?: string } | null>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const full = (): ServerConfig => ({ ...cfg, env: Object.fromEntries(envRows.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v])) });
  const choose = (k: keyof typeof presets) => {
    const p = presets[k];
    setPreset(k);
    setCfg({ ...cfg, name: p.name, transport: p.transport, command: p.command, args: p.args });
    setTest(null);
  };
  const runTest = async () => {
    setTest({ state: "busy" });
    try {
      const r = await api.testServer(full());
      setTest({ state: "ok", tools: r.tools });
      setOff(new Set());
    } catch (e) {
      setTest({ state: "err", text: (e as Error).message });
    }
  };
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.addServer({ ...full(), disabled_tools: [...off] });
      toast(r.state === "connected" ? `${cfg.name} added.` : `${cfg.name} saved, but it didn't start: ${r.error}`, r.state === "connected" ? "ok" : "warn");
      onDone();
    } catch (e) {
      toast((e as Error).message, "err");
      setSaving(false);
    }
  };
  const configured = cfg.name.trim() && (cfg.transport === "http" ? /^https?:\/\//.test(cfg.url) : cfg.command.trim());
  const can = [!!preset, !!configured, test?.state === "ok", true][i];

  return (
    <Wizard eyebrow="■ Connect // MCP server" title="Add an MCP server" steps={["Server", "Configure", "Test", "Save"]} index={i}
      onBack={() => setI(i - 1)} onClose={onClose} canNext={can} busy={test?.state === "busy" || saving}
      nextLabel={i === 3 ? (saving ? "Saving…" : "Save server") : "Continue"}
      onNext={() => { if (i === 1) setTest(null); if (i < 3) setI(i + 1); else save(); }}>
      {i === 0 && (<>
        <h3 className="wz-h">Choose a server</h3>
        <div className="seg" role="radiogroup" aria-label="How the server runs">
          <span className="ind" aria-hidden="true" style={{ width: "calc(50% - 3px)", transform: `translateX(${cfg.transport === "http" ? "100%" : 0})` }} />
          <button role="radio" aria-checked={cfg.transport === "stdio"} onClick={() => setCfg({ ...cfg, transport: "stdio" })}>Command · stdio</button>
          <button role="radio" aria-checked={cfg.transport === "http"} onClick={() => { setCfg({ ...cfg, transport: "http" }); setPreset("custom"); }}>URL · HTTP</button>
        </div>
        <span className="label">Start from a preset</span>
        <div className="presets" role="radiogroup" aria-label="Presets">
          {(Object.keys(presets) as (keyof typeof presets)[]).map((k) => (
            <button key={k} className="preset" role="radio" aria-checked={preset === k} onClick={() => choose(k)}>
              <Icon name={presets[k].icon} /><b>{presets[k].name || "Custom"}</b><span className="meta">{presets[k].sub}</span>
            </button>
          ))}
        </div>
        <span className="help">npx presets need {ext("https://nodejs.org", "Node.js")}; uvx presets need {ext("https://docs.astral.sh/uv/", "uv")}.</span>
      </>)}
      {i === 1 && (<>
        <h3 className="wz-h">Configure it</h3>
        <div className="field"><label htmlFor="m-name">Name</label><input className="input" id="m-name" value={cfg.name} placeholder="My server" onChange={(e) => setCfg({ ...cfg, name: e.target.value })} /></div>
        {cfg.transport === "http" ? (
          <div className="field"><label htmlFor="m-url">Server URL</label><input className="input mono" id="m-url" value={cfg.url} placeholder="http://127.0.0.1:3001/mcp" onChange={(e) => setCfg({ ...cfg, url: e.target.value })} /></div>
        ) : (
          <div className="two">
            <div className="field"><label htmlFor="m-cmd">Command</label><input className="input mono" id="m-cmd" value={cfg.command} placeholder="npx" onChange={(e) => setCfg({ ...cfg, command: e.target.value })} /></div>
            <div className="field"><label htmlFor="m-args">Arguments</label><input className="input mono" id="m-args" value={cfg.args} placeholder="-y package-name" onChange={(e) => setCfg({ ...cfg, args: e.target.value })} /></div>
          </div>
        )}
        <span className="label">Environment variables (optional, stored in Credential Manager)</span>
        {envRows.map(([k, v], n) => (
          <div key={n} className="two">
            <input className="input mono" aria-label="Variable name" placeholder="API_KEY" value={k} onChange={(e) => setEnvRows(envRows.map((r, j) => (j === n ? [e.target.value, r[1]] : r)))} />
            <input className="input mono" aria-label="Variable value" type="password" placeholder="value" value={v} onChange={(e) => setEnvRows(envRows.map((r, j) => (j === n ? [r[0], e.target.value] : r)))} />
          </div>
        ))}
        {envRows.length < 5 && <button className="linkbtn" style={{ alignSelf: "flex-start" }} onClick={() => setEnvRows([...envRows, ["", ""]])}>+ Add variable</button>}
      </>)}
      {i === 2 && (<>
        <h3 className="wz-h">Start it and list its tools</h3>
        <p className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{cfg.transport === "http" ? cfg.url : `${cfg.command} ${cfg.args}`}</p>
        <button className="btn btn-secondary" style={{ alignSelf: "flex-start" }} onClick={runTest} disabled={test?.state === "busy"}>
          <Icon name="terminal" />{test?.state === "busy" ? "Starting… (first run can take a minute)" : test ? "Run test again" : "Run test"}</button>
        {test?.state === "err" && <Result kind="err"><span className="st-err mono" style={{ fontSize: 12 }}>✕ {test.text}</span></Result>}
        {test?.state === "ok" && (<>
          <Result><span className="okline">✓ Started · {test.tools?.length} tool{test.tools?.length === 1 ? "" : "s"} found</span></Result>
          <div className="toolrows">
            {test.tools?.map((t) => (
              <div key={t.name} className="toolrow">
                <div className="grow"><div className="mono">{t.name}</div><div className="d">{t.description}</div></div>
                {t.confirm && <span className="tag warn">asks first</span>}
                <Switch checked={!off.has(t.name)} label={`Enable ${t.name}`} onChange={(v) => { const s = new Set(off); if (v) s.delete(t.name); else s.add(t.name); setOff(s); }} />
              </div>
            ))}
          </div>
        </>)}
      </>)}
      {i === 3 && (<>
        <h3 className="wz-h">Review and save</h3>
        <dl className="kv" style={{ gridTemplateColumns: "110px minmax(0,1fr)" }}>
          <dt>name</dt><dd>{cfg.name}</dd>
          <dt>runs as</dt><dd>{cfg.transport === "http" ? `HTTP · ${cfg.url}` : `stdio · ${cfg.command} ${cfg.args}`}</dd>
          <dt>tools on</dt><dd>{(test?.tools?.length ?? 0) - off.size} of {test?.tools?.length ?? 0}</dd>
          <dt>ask first</dt><dd>{test?.tools?.filter((t) => t.confirm && !off.has(t.name)).length ?? 0} tools wait for approval</dd>
        </dl>
        <p className="help">Donna starts this server when Donna starts and shows its status in Connections. You can turn it off or remove it at any time.</p>
      </>)}
    </Wizard>
  );
}
