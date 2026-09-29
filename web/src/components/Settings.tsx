import { useMemo, useState } from "react";
import { api, type Settings } from "../api";
import { Icon } from "../icons";
import { Switch, useToast } from "./ui";

export const MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"];

export function timezones(current: string): string[] {
  let list: string[] = [];
  try {
    list = (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone");
  } catch {
    list = ["Asia/Kolkata", "Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "UTC"];
  }
  return current && !list.includes(current) ? [current, ...list] : list;
}

export default function SettingsPage({ settings, onChange, onWiped, onRunSetup }: {
  settings: Settings; onChange: (s: Settings) => void; onWiped: () => void; onRunSetup: () => void;
}) {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const [keyState, setKeyState] = useState<{ kind: "ok" | "err" | "busy"; text: string } | null>(
    settings.groq_key_set ? { kind: "ok", text: `✓ Key saved (${settings.groq_key_hint})` } : null);
  const [wipe, setWipe] = useState(false);
  const zones = useMemo(() => timezones(settings.timezone), [settings.timezone]);

  const save = async (patch: Partial<Settings>, msg?: string) => {
    try {
      onChange(await api.saveSettings(patch));
      if (msg) toast(msg);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  const testKey = async () => {
    setKeyState({ kind: "busy", text: "Checking with Groq…" });
    try {
      const r = await api.saveGroqKey(key.trim() || undefined);
      setKeyState({ kind: "ok", text: `✓ Key works (${r.hint}) · ${r.models.length} chat models available` });
      setKey("");
      onChange(await api.settings());
    } catch (e) {
      setKeyState({ kind: "err", text: `✕ ${(e as Error).message}` });
    }
  };

  return (
    <div className="page">
      <div className="phead"><div>
        <span className="eyebrow"><span className="sq">■</span> Preferences // local</span>
        <h1>Settings</h1>
        <p>Everything stays on this computer. Keys and sign-ins are stored in Windows Credential Manager, never in plain files.</p>
      </div></div>
      <div className="sgrid">
        <article className="card scard">
          <span className="eyebrow">01 // Model provider</span>
          <h2>Groq API key</h2>
          <div className="field">
            <label htmlFor="groq-key">{settings.groq_key_set ? `Replace key (current ${settings.groq_key_hint})` : "API key"}</label>
            <div className="srow">
              <input className="input mono grow" id="groq-key" type={show ? "text" : "password"} autoComplete="off"
                placeholder={settings.groq_key_set ? "Paste a new key to replace it" : "gsk_…"} value={key}
                onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") testKey(); }} />
              <button className="btn btn-secondary" type="button" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button>
            </div>
            <span className="help">Free key from <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer">console.groq.com/keys</a></span>
          </div>
          <div className="srow">
            <span className={`grow ${keyState?.kind === "ok" ? "okline" : keyState?.kind === "err" ? "st-err mono" : "meta"}`} style={{ fontSize: 12 }}>{keyState?.text}</span>
            <button className="btn btn-secondary btn-sm" onClick={testKey} disabled={keyState?.kind === "busy" || (!key.trim() && !settings.groq_key_set)}>
              {key.trim() ? "Save and test" : "Test key"}</button>
          </div>
        </article>

        <article className="card scard">
          <span className="eyebrow">02 // Model</span>
          <h2>Model and reasoning</h2>
          <div className="field">
            <label htmlFor="m-model">Chat model</label>
            <select className="select" id="m-model" value={settings.model} onChange={(e) => save({ model: e.target.value }, "Model updated.")}>
              {[...new Set([settings.model, ...MODELS])].map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="m-effort">Reasoning effort</label>
            <select className="select" id="m-effort" value={settings.reasoning_effort} onChange={(e) => save({ reasoning_effort: e.target.value })}>
              <option value="low">Low · fastest</option><option value="medium">Medium · balanced</option><option value="high">High · most careful</option>
            </select>
          </div>
          <span className="meta">Free tier per model: 30 requests/min · 1,000/day · 8K tokens/min. Today: {settings.usage.requests} requests.</span>
        </article>

        <article className="card scard">
          <span className="eyebrow">03 // Time</span>
          <h2>Timezone</h2>
          <p>Used for “today”, “tomorrow” and meeting times. Calendar events use your Google Calendar's own timezone.</p>
          <div className="field">
            <label htmlFor="tz">Timezone</label>
            <select className="select" id="tz" value={settings.timezone} onChange={(e) => save({ timezone: e.target.value }, "Timezone updated.")}>
              {zones.map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          </div>
        </article>

        <article className="card scard">
          <span className="eyebrow">04 // Appearance</span>
          <h2>Look and motion</h2>
          <div className="srow"><div className="grow"><b>Pixel grid background</b><span>A faint 24 px grid behind the interface.</span></div>
            <Switch checked={settings.pixel_grid} label="Pixel grid background" onChange={(v) => save({ pixel_grid: v })} /></div>
          <div className="srow"><div className="grow"><b>Reduce motion</b><span>Turns off animations. Your system setting is also respected.</span></div>
            <Switch checked={settings.reduce_motion} label="Reduce motion" onChange={(v) => save({ reduce_motion: v })} /></div>
        </article>

        <article className="card scard">
          <span className="eyebrow">05 // Data</span>
          <h2>Your data</h2>
          <p>Conversations are stored in <span className="mono">{settings.data_dir}\donna.db</span>.</p>
          <div className="srow">
            <a className="btn btn-secondary" href="/api/v1/export" download><Icon name="download" />Export conversations</a>
            <button className="btn btn-danger" onClick={() => setWipe(true)}>Wipe all conversations</button>
          </div>
          {wipe && (
            <div className="inline-confirm" role="alert">
              <span>This permanently deletes every conversation on this computer. Connections and keys are kept.</span>
              <div className="actions">
                <button className="btn btn-ghost btn-sm" onClick={() => setWipe(false)} autoFocus>Cancel</button>
                <button className="btn btn-danger btn-sm" onClick={async () => {
                  const r = await api.wipe();
                  setWipe(false);
                  toast(`Deleted ${r.deleted} conversation${r.deleted === 1 ? "" : "s"}.`, "warn");
                  onWiped();
                }}>Wipe everything</button>
              </div>
            </div>
          )}
        </article>

        <article className="card scard">
          <span className="eyebrow">06 // Advanced</span>
          <h2>Setup and diagnostics</h2>
          <div className="srow"><div className="grow"><b>First-time setup</b><span>Go through the welcome steps again.</span></div>
            <button className="btn btn-secondary btn-sm" onClick={onRunSetup}>Run setup</button></div>
          <div className="srow"><div className="grow"><b>Data and logs folder</b><span className="mono">{settings.data_dir}</span></div>
            <button className="btn btn-secondary btn-sm" onClick={() => api.openDataFolder().then(() => toast("Opened in File Explorer."))}>Open folder</button></div>
        </article>
      </div>
    </div>
  );
}
