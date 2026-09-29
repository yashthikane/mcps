import { useMemo, useState } from "react";
import { api, type Connection, type Settings } from "../api";
import { Icon } from "../icons";
import type { WizardKind } from "./Connections";
import { timezones } from "./Settings";
import { Dialog, DialogHead, Result, StatePill } from "./ui";

const SAMPLES = ["What's the weather in Pune right now?", "Summarize my unread emails", "List my Notion pages"];

export default function Onboarding({ settings, connections, onSettings, onFinish, onWizard, onClose }: {
  settings: Settings; connections: Connection[]; onSettings: (s: Settings) => void;
  onFinish: (prompt?: string) => void; onWizard: (k: WizardKind) => void; onClose: () => void;
}) {
  const [i, setI] = useState(0);
  const [key, setKey] = useState("");
  const [res, setRes] = useState<{ kind: "ok" | "err" | "busy"; text: string } | null>(
    settings.groq_key_set ? { kind: "ok", text: `Key saved (${settings.groq_key_hint})` } : null);
  const zones = useMemo(() => timezones(settings.timezone), [settings.timezone]);
  const steps = ["Groq key", "Timezone", "Connect", "Try it"];
  const state = (id: string) => connections.find((c) => c.id === id)?.state ?? "not_set_up";

  const test = async () => {
    setRes({ kind: "busy", text: "Checking the key with Groq…" });
    try {
      const r = await api.saveGroqKey(key.trim() || undefined);
      setRes({ kind: "ok", text: `Key works (${r.hint}) · ${r.models.slice(0, 2).join(", ")} available` });
      onSettings(await api.settings());
    } catch (e) {
      setRes({ kind: "err", text: (e as Error).message });
    }
  };

  return (
    <Dialog open onClose={onClose} labelledBy="ob-title">
      <div className="dlg">
        <DialogHead id="ob-title" eyebrow="■ First-time setup" title="Welcome to Donna" onClose={onClose} />
        <ol className="stepper">
          {steps.map((s, k) => (
            <li key={s} className={k < i ? "done" : k === i ? "cur" : ""}><span className="bar" /><span className="sl">{String(k + 1).padStart(2, "0")} {s}</span></li>
          ))}
        </ol>
        <div className="dlg-body">
          {i === 0 && (<>
            <h3 className="wz-h">Add your Groq API key</h3>
            <p>Donna's AI runs on Groq's free tier. Paste your key and Donna checks it.</p>
            <div className="field">
              <label htmlFor="ob-key">Groq API key</label>
              <div className="srow">
                <input className="input mono grow" id="ob-key" type="password" autoComplete="off" placeholder={settings.groq_key_set ? "Leave empty to keep the saved key" : "gsk_…"}
                  value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") test(); }} />
                <button className="btn btn-secondary" onClick={test} disabled={res?.kind === "busy" || (!key.trim() && !settings.groq_key_set)}>Test</button>
              </div>
              <span className="help">Get one free at <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer">console.groq.com/keys</a>. It's stored in Windows Credential Manager.</span>
            </div>
            {res && <Result kind={res.kind === "ok" ? "ok" : res.kind === "busy" ? "busy" : "err"}>
              <span className={res.kind === "ok" ? "okline" : res.kind === "err" ? "st-err mono" : "help"} style={{ fontSize: 12 }}>{res.kind === "ok" ? "✓ " : res.kind === "err" ? "✕ " : ""}{res.text}</span></Result>}
          </>)}
          {i === 1 && (<>
            <h3 className="wz-h">Confirm your timezone</h3>
            <p>Donna uses it for “today”, “tomorrow” and meeting times.</p>
            <div className="field"><label htmlFor="ob-tz">Timezone</label>
              <select className="select" id="ob-tz" value={settings.timezone}
                onChange={async (e) => onSettings(await api.saveSettings({ timezone: e.target.value }))}>
                {zones.map((z) => <option key={z}>{z}</option>)}
              </select></div>
          </>)}
          {i === 2 && (<>
            <h3 className="wz-h">Connect your apps</h3>
            <p>Connect now, or skip and do it later from Connections.</p>
            <div className="toolrows">
              <Row icon="mail" title="Google" sub="Gmail + Calendar · 17 tools" state={state("gmail")} onSetup={() => onWizard("google")} />
              <Row icon="doc" title="Notion" sub="Pages and databases · 8 tools" state={state("notion")} onSetup={() => onWizard("notion")} />
              <Row icon="terminal" title="MCP servers" sub="Filesystem, Git, Fetch and more" state="optional" onSetup={() => onWizard("mcp")} />
            </div>
          </>)}
          {i === 3 && (<>
            <h3 className="wz-h">Ask your first question</h3>
            <p>Pick one to see Donna work. Tool calls appear in the activity panel on the right.</p>
            <div className="sample-prompts">
              {SAMPLES.map((s) => (
                <button key={s} className="sugg" onClick={() => onFinish(s)}><span className="ic"><Icon name="arrow" /></span><span><b>{s}</b></span></button>
              ))}
            </div>
          </>)}
        </div>
        <div className="dlg-foot">
          <span className="meta grow">Step {i + 1} of {steps.length}</span>
          <button className="btn btn-ghost" onClick={() => setI(i - 1)} disabled={i === 0}>Back</button>
          <button className="btn btn-primary" disabled={i === 0 && res?.kind !== "ok"} onClick={() => (i < 3 ? setI(i + 1) : onFinish())}>
            {i === 3 ? "Start chatting" : "Continue"}</button>
        </div>
      </div>
    </Dialog>
  );
}

function Row({ icon, title, sub, state, onSetup }: { icon: string; title: string; sub: string; state: string; onSetup: () => void }) {
  return (
    <div className="toolrow">
      <Icon name={icon} />
      <div className="grow"><div style={{ fontWeight: 500 }}>{title}</div><div className="d">{sub}</div></div>
      {state === "connected" ? <StatePill state="connected" /> : <button className="btn btn-secondary btn-sm" onClick={onSetup}>Set up</button>}
    </div>
  );
}
