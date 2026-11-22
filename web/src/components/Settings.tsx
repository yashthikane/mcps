import { useEffect, useMemo, useState } from "react";
import { api, type Memory, type Settings } from "../api";
import { Icon } from "../icons";
import { AwaySettings } from "./Availability";
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

function MemoryCard() {
  const toast = useToast();
  const [items, setItems] = useState<Memory[] | null>(null);
  const [text, setText] = useState("");
  const load = () => api.memories().then(setItems).catch((e) => toast((e as Error).message, "err"));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); toast(msg); load(); } catch (e) { toast((e as Error).message, "err"); }
  };
  return (
    <article className="card scard">
      <span className="eyebrow">08 // Memory</span>
      <h2>What Donna remembers about you</h2>
      <p>Used in your own chats only (web and your own WhatsApp/Telegram), never shown to contacts. Donna adds to it when you say “remember…”.</p>
      {items === null ? <span className="meta">Loading…</span> : items.length === 0 ? <span className="meta">Nothing yet.</span> : (
        <div className="toolrows">
          {items.map((m) => (
            <div key={m.id} className="toolrow">
              <span className="d grow">{m.content}</span>
              <button className="btn btn-ghost btn-icon btn-sm" aria-label="Forget" onClick={() => run(() => api.deleteMemory(m.id), "Forgotten.")}><Icon name="trash" /></button>
            </div>
          ))}
        </div>
      )}
      <div className="srow">
        <input className="input grow" placeholder="e.g. I'm vegetarian; my office is in Pune" value={text} onChange={(e) => setText(e.target.value)} aria-label="New memory" />
        <button className="btn btn-secondary btn-sm" disabled={!text.trim()} onClick={() => run(async () => { await api.addMemory(text); setText(""); }, "Saved.")}>Add</button>
      </div>
    </article>
  );
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
            <label htmlFor="m-provider">Donna thinks with</label>
            <select className="select" id="m-provider" value={settings.llm_provider}
              onChange={(e) => save({ llm_provider: e.target.value as Settings["llm_provider"] }, e.target.value === "claude_code"
                ? "Donna now uses Claude through Claude Code on this computer." : "Donna now uses Groq.")}>
              <option value="groq">Groq (free tier, fastest)</option>
              <option value="claude_code">Claude, through Claude Code (your Claude subscription)</option>
            </select>
            {settings.llm_provider === "claude_code" && (
              <span className="help">Uses the <span className="mono">claude</span> program on this computer, signed in with your own
                Claude account (run <span className="mono">claude</span> once in a terminal to sign in). Donna never sees that sign-in.
                Each step takes a few seconds longer than Groq and counts toward your Claude plan's usage.</span>
            )}
          </div>
          {settings.llm_provider === "claude_code" && (
            <div className="field">
              <label htmlFor="m-claude">Claude model</label>
              <select className="select" id="m-claude" value={settings.claude_model} onChange={(e) => save({ claude_model: e.target.value }, "Claude model updated.")}>
                {[...new Set([settings.claude_model, "opus", "sonnet", "fable", "haiku"])].map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
          <div className="field">
            <label htmlFor="m-model">Groq model</label>
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

        <article className="card scard">
          <span className="eyebrow">07 // Messaging</span>
          <h2>WhatsApp and Telegram</h2>
          <div className="srow"><div className="grow"><b>Messaging on</b><span>Kill switch: off stops every reply and message on every channel at once.</span></div>
            <Switch checked={settings.messaging_enabled} label="Messaging on" onChange={(v) => save({ messaging_enabled: v }, v ? "Messaging is on." : "Messaging is off. Nothing is answered or sent.")} /></div>
          <div className="srow"><div className="grow"><b>Only answer when someone writes {settings.trigger_word || "@donna"}</b>
            <span>On your own WhatsApp, people keep chatting with you as usual; Donna replies only to messages with the trigger.
              Each enabled contact is told this once.</span></div>
            <Switch checked={settings.require_trigger} label="Require trigger word" onChange={(v) => save({ require_trigger: v },
              v ? `Donna now answers only to ${settings.trigger_word || "@donna"}.` : "Donna answers every message from enabled contacts.")} /></div>
          {settings.require_trigger && (
            <div className="field"><label htmlFor="ms-trigger">Trigger word</label>
              <input className="input mono" id="ms-trigger" defaultValue={settings.trigger_word}
                onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== settings.trigger_word && save({ trigger_word: e.target.value.trim() }, "Trigger saved.")} /></div>
          )}
          <div className="srow"><div className="grow"><b>Automations on</b><span>Off: scheduled tasks are skipped until you turn this back on.</span></div>
            <Switch checked={settings.automations_enabled} label="Automations on" onChange={(v) => save({ automations_enabled: v }, v ? "Automations are on." : "Automations are paused.")} /></div>
          <div className="field"><label htmlFor="ms-owner">Your name</label>
            <input className="input" id="ms-owner" defaultValue={settings.owner_name} placeholder="How Donna refers to you, e.g. Yashraj"
              onBlur={(e) => e.target.value !== settings.owner_name && save({ owner_name: e.target.value }, "Saved.")} /></div>
          <div className="field"><label htmlFor="ms-pron">Donna refers to you as</label>
            <select className="select" id="ms-pron" value={settings.owner_pronouns} onChange={(e) => save({ owner_pronouns: e.target.value as Settings["owner_pronouns"] }, "Saved.")}>
              <option value="">Your name only (no gendered words)</option><option value="he">He / him</option>
              <option value="she">She / her</option><option value="they">They / them</option></select>
            <span className="help">Used for Hindi and Marathi grammar too (e.g. "karto" vs "karte"). Donna is always "she".</span></div>
          <div className="field"><label htmlFor="ms-profile">What Donna may tell contacts about you</label>
            <textarea className="input textarea" id="ms-profile" rows={2} defaultValue={settings.assistant_profile}
              placeholder="e.g. Founder at Curlscape. Prefers meetings in the afternoon. For urgent things, call the office."
              onBlur={(e) => e.target.value !== settings.assistant_profile && save({ assistant_profile: e.target.value }, "Saved.")} /></div>
          <div className="two">
            <div className="field"><label htmlFor="ms-lang">Default language</label>
              <input className="input" id="ms-lang" defaultValue={settings.default_language} placeholder="auto"
                onBlur={(e) => e.target.value !== settings.default_language && save({ default_language: e.target.value || "auto" }, "Saved.")} /></div>
            <div className="field"><label htmlFor="ms-tone">Default tone</label>
              <input className="input" id="ms-tone" defaultValue={settings.default_tone}
                onBlur={(e) => e.target.value !== settings.default_tone && save({ default_tone: e.target.value }, "Saved.")} /></div>
          </div>
          <div className="two">
            <div className="field"><label htmlFor="ms-rate">Auto replies per contact / hour</label>
              <input className="input mono" id="ms-rate" type="number" min={1} max={500} defaultValue={settings.contact_rate_per_hour}
                onBlur={(e) => Number(e.target.value) !== settings.contact_rate_per_hour && save({ contact_rate_per_hour: Number(e.target.value) }, "Saved.")} /></div>
            <div className="field"><label htmlFor="ms-grate">Auto replies in total / hour</label>
              <input className="input mono" id="ms-grate" type="number" min={1} max={2000} defaultValue={settings.global_rate_per_hour}
                onBlur={(e) => Number(e.target.value) !== settings.global_rate_per_hour && save({ global_rate_per_hour: Number(e.target.value) }, "Saved.")} /></div>
          </div>
          <div className="two">
            <div className="field"><label htmlFor="ms-ttl">Approvals expire after (hours)</label>
              <input className="input mono" id="ms-ttl" type="number" min={1} max={168} defaultValue={settings.approval_ttl_hours}
                onBlur={(e) => Number(e.target.value) !== settings.approval_ttl_hours && save({ approval_ttl_hours: Number(e.target.value) }, "Saved.")} /></div>
            <div className="field"><label htmlFor="ms-unknown">Someone not in Contacts writes</label>
              <select className="select" id="ms-unknown" value={settings.unknown_contact_action}
                onChange={(e) => save({ unknown_contact_action: e.target.value as Settings["unknown_contact_action"] }, "Saved.")}>
                <option value="ignore">Ignore (list under Discovered)</option><option value="notify">Notify me</option></select></div>
          </div>
          <div className="two">
            <div className="field"><label htmlFor="ms-ownerch">Alerts and briefings go to</label>
              <select className="select" id="ms-ownerch" value={settings.owner_channel} onChange={(e) => save({ owner_channel: e.target.value }, "Saved.")}>
                <option value="whatsapp">My WhatsApp</option><option value="telegram">My Telegram</option></select></div>
            <div className="field"><label htmlFor="ms-openwa">OpenWA address</label>
              <input className="input mono" id="ms-openwa" defaultValue={settings.openwa_url}
                onBlur={(e) => e.target.value !== settings.openwa_url && save({ openwa_url: e.target.value }, "Saved.")} /></div>
          </div>
        </article>

        <AwaySettings settings={settings} save={save} />
        <MemoryCard />
      </div>
    </div>
  );
}
