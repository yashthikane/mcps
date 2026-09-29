import { useState } from "react";
import type { Message, ToolEvent, Usage } from "../api";
import { Icon, toolIcon } from "../icons";
import type { Live } from "./Chat";
import { fmtMs } from "./ui";

const STATE: Record<string, [string, string]> = {
  running: ["st-proc", "Running"], done: ["st-ok", "Done"], error: ["st-err", "Failed"],
  rejected: ["st-off", "Rejected"], waiting: ["st-warn", "Needs approval"],
};

export default function RunPanel({ live, messages, usage, model, onClose }: {
  live: Live | null; messages: Message[]; usage: Usage | null; model: string; onClose: () => void;
}) {
  const [tab, setTab] = useState<"activity" | "usage">("activity");
  const last = [...messages].reverse().find((m) => m.role === "assistant" && m.tool_events.length);
  const tools: ToolEvent[] = live?.tools.length ? live.tools : last?.tool_events ?? [];
  const running = !!live;
  const waiting = tools.some((t) => t.status === "waiting");
  const pill = running ? (waiting ? ["warn", "Waiting for you"] : ["proc", "Running"]) : tools.length ? ["ok", "Complete"] : null;
  const total = tools.reduce((n, t) => n + (t.ms || 0), 0);

  return (
    <aside className="runpanel" id="runpanel" aria-label="Activity">
      <div className="rp-head">
        <div className="rp-top">
          <span className="eyebrow"><span className="sq">■</span> Activity{live?.runId ? ` // ${live.runId}` : ""}</span>
          {pill && <span className={`pill ${pill[0]}`}>{pill[1]}</span>}
          <button className="btn btn-ghost btn-icon rp-close" onClick={onClose} aria-label="Close activity panel"><Icon name="x" /></button>
        </div>
        <div className="meta" id="rp-sub">{tools.length ? `${tools.length} tool call${tools.length === 1 ? "" : "s"} · ${fmtMs(total)} in tools` : "No tool calls yet in this chat"}</div>
        <div className="seg" role="tablist" aria-label="Activity details">
          <span className="ind" aria-hidden="true" style={{ width: "calc(50% - 3px)", transform: `translateX(${tab === "activity" ? 0 : "100%"})` }} />
          <button role="tab" aria-selected={tab === "activity"} onClick={() => setTab("activity")}>Tool calls</button>
          <button role="tab" aria-selected={tab === "usage"} onClick={() => setTab("usage")}>Usage</button>
        </div>
      </div>
      <div className="rp-body">
        {tab === "activity" ? (
          tools.length ? (
            <ul className="tree">
              {tools.map((t) => {
                const [cls, label] = STATE[t.status] ?? ["st-off", t.status];
                return (
                  <li key={t.call_id} className={`node s-${t.status === "waiting" ? "approval" : t.status === "error" ? "failed" : t.status}`}>
                    <span className="mk" aria-hidden="true" />
                    <div className="nb">
                      <span className="nt"><Icon name={toolIcon(t.name)} size={13} />{t.name}</span>
                      {Object.keys(t.args).length > 0 && <span className="nm">{JSON.stringify(t.args)}</span>}
                      {t.preview && <details className="nres"><summary className="nm">Result</summary><pre>{t.preview}</pre></details>}
                    </div>
                    <div className="ns"><span className={cls}>{label}</span><span className="el">{t.ms ? fmtMs(t.ms) : "—"}</span></div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="rp-empty"><Icon name="spark" size={18} /><span>Every tool Donna uses shows up here, with its arguments, result and timing.</span></div>
          )
        ) : (
          <>
            <div className="stats">
              <div className="stat"><span className="eyebrow">Requests today</span><span className="num">{usage?.requests ?? 0}<small> /1K</small></span><span className="chg">{1000 - (usage?.requests ?? 0)} left</span></div>
              <div className="stat"><span className="eyebrow">Tokens today</span><span className="num">{fmtK(usage?.tokens ?? 0)}</span><span className="chg">Groq free tier</span></div>
            </div>
            <div className="rp-sec">
              <span className="eyebrow">Daily requests</span>
              <div className={`meter ${(usage?.requests ?? 0) >= 800 ? "warn" : ""}`}><i style={{ width: `${Math.min(100, (usage?.requests ?? 0) / 10)}%` }} /></div>
              <span className="meta">Model: {model}. Free tier per model: 30 requests/min, 1,000/day, 8K tokens/min.</span>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function fmtK(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K` : String(n);
}
