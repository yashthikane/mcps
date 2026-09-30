import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../icons";
import type { ConnState } from "../api";

/* ------------------------------------------------------------------ toasts */
type ToastKind = "ok" | "warn" | "err";
interface ToastAction { label: string; run: () => void }
interface ToastItem { id: number; text: string; kind: ToastKind; action?: ToastAction }
const ToastCtx = createContext<(text: string, kind?: ToastKind, action?: ToastAction) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((text: string, kind: ToastKind = "ok", action?: ToastAction) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-2), { id, text, kind, action }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), action ? 9000 : kind === "err" ? 6000 : 3600);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "err" ? "alert" : "status"}>
            <span className="dot" aria-hidden="true" />
            <span>{t.text}</span>
            {t.action && (
              <button className="btn btn-ghost btn-sm toast-act" onClick={() => {
                t.action!.run();
                setItems((xs) => xs.filter((x) => x.id !== t.id));
              }}>{t.action.label}</button>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------------ dialog (native <dialog>: focus trap + Esc) */
export function Dialog({ open, onClose, labelledBy, className, children }: {
  open: boolean; onClose: () => void; labelledBy?: string; className?: string; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={className}
      aria-labelledby={labelledBy}
      onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
    >
      {open && children}
    </dialog>
  );
}

export function DialogHead({ id, eyebrow, title, onClose }: { id: string; eyebrow: string; title: string; onClose: () => void }) {
  return (
    <div className="dlg-head">
      <div className="t">
        <span className="eyebrow">{eyebrow}</span>
        <h2 id={id}>{title}</h2>
      </div>
      <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
    </div>
  );
}

/* ------------------------------------------------------------------ small pieces */
export function Switch({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean;
}) {
  return (
    <button type="button" className="switch" role="switch" aria-checked={checked} aria-label={label}
      disabled={disabled} onClick={() => onChange(!checked)} />
  );
}

const STATE_PILL: Record<string, [string, string]> = {
  connected: ["ok", "Connected"],
  not_set_up: ["off", "Not set up"],
  needs_auth: ["warn", "Sign in"],
  needs_reauth: ["warn", "Needs re-auth"],
  error: ["err", "Error"],
  off: ["off", "Off"],
  connecting: ["proc", "Starting"],
  stopped: ["off", "Stopped"],
};

export function StatePill({ state }: { state: ConnState | string }) {
  const [cls, label] = STATE_PILL[state] ?? ["off", state];
  return <span className={`pill ${cls}`}>{label}</span>;
}

export function Result({ kind = "ok", children }: { kind?: "ok" | "busy" | "err"; children: ReactNode }) {
  return (
    <div className={`result ${kind === "ok" ? "" : kind}`} role={kind === "err" ? "alert" : undefined}>
      {kind === "busy" && <span className="pill proc">Working</span>}
      {children}
    </div>
  );
}

export function timeAgo(t: number): string {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function fmtMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
