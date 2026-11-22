import { useCallback, useEffect, useState } from "react";
import { api, type Channel, type GatewaySetup } from "../api";
import { Icon } from "../icons";
import { Dialog, DialogHead, Result, timeAgo, useToast } from "./ui";

const STATUS: Record<string, [string, string]> = {
  not_set_up: ["off", "Not set up"], connected: ["ok", "Connected"], qr: ["warn", "Scan QR"], connecting: ["proc", "Connecting"],
  disconnected: ["off", "Disconnected"], error: ["err", "Error"],
};

/** Messaging channels (WhatsApp via OpenWA, Telegram bot): shown at the top of Connections. */
export default function ChannelsSection() {
  const toast = useToast();
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [connecting, setConnecting] = useState<{ start: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setChannels(await api.channels()); } catch (e) { toast((e as Error).message, "err"); }
  }, [toast]);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 15000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (id: string, fn: () => Promise<Channel | unknown>, ok: string) => {
    setBusy(id);
    try {
      const r = (await fn()) as Channel | undefined;
      toast(ok);
      if (r && r.status === "qr") setConnecting({ start: false });
    } catch (e) {
      toast((e as Error).message, "err");
    }
    setBusy(null);
    load();
  };

  return (
    <>
      <div className="sec-h"><h2>Messaging channels</h2><span className="meta">who can reach Donna is set per person in Contacts</span></div>
      <div className="cgrid">
        {(channels ?? []).map((c) => (
          <article key={c.id} className="card ccard">
            <div className="cc-top">
              <span className="cc-ic"><Icon name={c.id === "whatsapp" ? "chat" : "send"} size={16} /></span>
              <div className="grow"><b>{c.label}</b><span className="meta">{c.id === "whatsapp" ? "OpenWA gateway · local" : "Telegram bot · long polling"}</span></div>
              <span className={`pill ${(STATUS[c.status] ?? ["off"])[0]}`}>{(STATUS[c.status] ?? ["", c.status])[1]}</span>
              {c.paused && <span className="pill warn">Paused</span>}
            </div>
            <p className="help">
              {c.account ? <>Account <span className="mono">{c.account}</span>. </> : null}
              {c.last_activity_at ? `Last activity ${timeAgo(c.last_activity_at)}. ` : ""}
              {c.id === "whatsapp" && !c.enabled && <>Connect sets everything up (the first time it installs the OpenWA gateway, a few
                minutes), then shows a QR code to scan with the WhatsApp Donna should use. A dedicated number is safest.</>}
              {c.id === "whatsapp" && c.setup?.busy && <> Setting up: {c.setup.detail}…</>}
              {!c.credentials && c.id === "telegram" && "Create a bot with @BotFather in Telegram and paste its token."}
            </p>
            {c.last_error && <span className="st-err help">{c.last_error}</span>}
            {c.id === "telegram" && !c.enabled && <TelegramToken onSaved={load} />}
            <div className="cc-foot">
              {!c.enabled ? (
                c.id === "whatsapp" ? (
                  <button className="btn btn-primary btn-sm" onClick={() => setConnecting({ start: true })}>
                    <Icon name="plug" />{c.setup?.busy ? "Show progress" : "Connect"}</button>
                ) : c.credentials &&
                <button className="btn btn-primary btn-sm" disabled={busy === c.id}
                  onClick={() => act(c.id, () => api.connectChannel(c.id), `${c.label} connected.`)}><Icon name="plug" />Connect</button>
              ) : (<>
                {c.id === "whatsapp" && c.status !== "connected" &&
                  <button className="btn btn-primary btn-sm" onClick={() => setConnecting({ start: true })}>Show QR</button>}
                <button className="btn btn-secondary btn-sm" disabled={busy === c.id}
                  onClick={() => act(c.id, () => api.pauseChannel(c.id, !c.paused), c.paused ? "Resumed." : "Paused: nothing is answered or sent.")}>
                  {c.paused ? "Resume" : "Pause"}</button>
                <button className="btn btn-ghost btn-sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.channelStatus(c.id), "Status refreshed.")}>
                  <Icon name="refresh" />Check</button>
                <span className="grow" />
                <button className="btn btn-ghost btn-sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.disconnectChannel(c.id), "Disconnected.")}>Disconnect</button>
              </>)}
            </div>
          </article>
        ))}
      </div>
      {connecting && <WhatsAppConnect start={connecting.start} onClose={() => { setConnecting(null); load(); }} />}
    </>
  );
}

function TelegramToken({ onSaved }: { onSaved: () => void }) {
  const toast = useToast();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="srow">
      <input className="input mono grow" type="password" autoComplete="off" placeholder="123456789:AAH…" value={token}
        onChange={(e) => setToken(e.target.value)} aria-label="Telegram bot token" />
      <button className="btn btn-secondary btn-sm" disabled={!token.trim() || busy} onClick={async () => {
        setBusy(true);
        try { await api.telegramToken(token.trim()); setToken(""); toast("Telegram connected."); onSaved(); } catch (e) { toast((e as Error).message, "err"); }
        setBusy(false);
      }}>Save and connect</button>
    </div>
  );
}

const STEPS: [GatewaySetup["phase"], string][] = [
  ["checking", "Check Node.js and git"],
  ["installing", "Install the WhatsApp gateway (first time only, a few minutes)"],
  ["starting", "Start the gateway"],
  ["connecting", "Open the WhatsApp session"],
  ["qr", "Scan the QR code"],
];
type Qr = { status: string; qr?: string; error?: string };

/** Connect: runs the setup job (install → start → session) with live progress, then shows the QR until linked. */
function WhatsAppConnect({ start, onClose }: { start: boolean; onClose: () => void }) {
  const [setup, setSetup] = useState<GatewaySetup | null>(null);
  const [qr, setQr] = useState<Qr | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let stop = false;
    let timer = 0;
    const later = (fn: () => void, ms: number) => { if (!stop) timer = window.setTimeout(fn, ms); };
    const pollQr = async () => {
      try {
        const r = await api.channelQr("whatsapp");
        if (stop) return;
        setQr(r);
        if (r.status === "connected") return;
      } catch (e) {
        if (!stop) setQr({ status: "error", error: (e as Error).message });
      }
      later(pollQr, 3000);
    };
    const pollSetup = async () => {
      try {
        const s = await api.whatsappSetupState();
        if (stop) return;
        setSetup(s);
        if (s.busy) return later(pollSetup, 1500);
        if (s.phase !== "error") pollQr();
      } catch {
        later(pollSetup, 3000);
      }
    };
    (async () => {
      setQr(null);
      if (start || attempt > 0) {
        try {
          const s = await api.whatsappSetup();
          if (!stop) setSetup(s);
        } catch (e) {
          if (!stop) setSetup({ phase: "error", detail: "", error: (e as Error).message, busy: false, log: [], installed: false, running: false });
          return;
        }
      }
      pollSetup();
    })();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [start, attempt]);

  const phase = setup?.phase ?? "checking";
  const linked = qr?.status === "connected" || phase === "connected";
  const current = linked ? STEPS.length : Math.max(0, STEPS.findIndex(([p]) => p === phase));
  // After an error, the step that failed is the last one the job announced.
  const failed = phase === "error" ? Math.max(0, ...STEPS.map(([, label], i) =>
    setup?.log.some((l) => l.startsWith("— ") && label.startsWith(l.slice(2).split(" (")[0])) ? i : 0)) : -1;

  return (
    <Dialog open onClose={onClose} labelledBy="qr-title" className="task-dlg">
      <div className="dlg">
        <DialogHead id="qr-title" eyebrow="■ WhatsApp // connect" title={linked ? "WhatsApp is connected" : "Connect WhatsApp"} onClose={onClose} />
        <div className="dlg-body qr-body">
          <ol className="setup-steps">
            {STEPS.map(([p, label], i) => {
              const at = phase === "error" ? failed : current;
              const state = linked || i < at ? "done" : i === at ? (phase === "error" ? "err" : "now") : "todo";
              return (
                <li key={p} className={`step-${state}`}>
                  <span className="step-dot" aria-hidden="true">{state === "done" ? "✓" : state === "err" ? "✕" : i + 1}</span>
                  <span className="grow">{label}</span>
                  {state === "now" && setup?.busy && <span className="pill proc">Working</span>}
                </li>
              );
            })}
          </ol>

          {phase === "error" ? <Result kind="err">{setup?.error}</Result>
            : linked ? <Result kind="ok">Linked. Donna now receives this number's WhatsApp messages. Next, enable people in Contacts,
                starting with yourself (“This is me”).</Result>
            : qr?.qr ? (<>
                <img className="qr" src={qr.qr} alt="WhatsApp QR code" />
                <p className="help">On the phone: WhatsApp → Settings → Linked devices → Link a device, then scan. The code refreshes by itself.</p>
              </>)
            : qr?.error ? <Result kind="err">{qr.error}</Result>
            : !setup?.busy && <Result kind="busy">Waiting for WhatsApp Web to produce a QR code (up to a minute)…</Result>}

          {setup && setup.log.length > 0 && (
            <details className="nres setup-log">
              <summary className="meta">Details</summary>
              <pre>{setup.log.slice(-30).join("\n")}</pre>
            </details>
          )}
        </div>
        <div className="dlg-foot">
          <span className="meta grow">{setup?.busy ? "You can close this; setup keeps going." : ""}</span>
          {phase === "error" && <button className="btn btn-secondary" onClick={() => setAttempt((n) => n + 1)}><Icon name="refresh" />Try again</button>}
          <button className="btn btn-primary" onClick={onClose}>{linked ? "Done" : "Close"}</button>
        </div>
      </div>
    </Dialog>
  );
}
