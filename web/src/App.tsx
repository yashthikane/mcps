import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, streamChat, type Connection, type Conversation, type Health, type Message, type Settings, type ToolEvent } from "./api";
import { Icon, PixelD } from "./icons";
import Chat, { type Live } from "./components/Chat";
import ConnectionsPage, { GoogleWizard, McpWizard, NotionWizard, ToolsDialog, type WizardKind } from "./components/Connections";
import Onboarding from "./components/Onboarding";
import Palette, { type Command } from "./components/Palette";
import RunPanel from "./components/RunPanel";
import SettingsPage from "./components/Settings";
import Sidebar from "./components/Sidebar";
import { useToast } from "./components/ui";

type View = "chat" | "connections" | "settings";
const narrow = () => window.matchMedia("(max-width:1180px)").matches;

export default function App() {
  const toast = useToast();
  const [view, setView] = useState<View>("chat");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [drawer, setDrawer] = useState<"side" | "run" | null>(null);
  const [runOpen, setRunOpen] = useState(true);
  const [palette, setPalette] = useState(false);
  const [wizard, setWizard] = useState<{ kind: WizardKind; startAt: number } | null>(null);
  const [toolsDlg, setToolsDlg] = useState<{ open: boolean; only?: Connection }>({ open: false });
  const [onboarding, setOnboarding] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeId;

  /* ------------------------------------------------------------------ loading */
  const reloadConnections = useCallback(async () => {
    try { setConnections(await api.connections()); } catch (e) { toast((e as Error).message, "err"); }
  }, [toast]);
  const reloadConversations = useCallback(async (q = query) => {
    try { setConversations(await api.conversations(q)); } catch { /* shown by the offline banner */ }
  }, [query]);
  const refreshHealth = useCallback(async () => {
    try { setHealth(await api.health()); } catch { setHealth({ groq: "unreachable", online: false, usage: { date: "", requests: 0, tokens: 0 } }); }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        let s = await api.settings();
        if (!s.timezone) s = await api.saveSettings({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" });
        setSettings(s);
        if (!s.onboarded) setOnboarding(true);
      } catch (e) {
        toast((e as Error).message, "err");
      }
    })();
    reloadConnections();
    refreshHealth();
    const t = setInterval(refreshHealth, 20000);
    const up = () => { setOnline(true); refreshHealth(); };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => { clearInterval(t); window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, [reloadConnections, refreshHealth, toast]);

  useEffect(() => {
    const t = setTimeout(() => reloadConversations(query), query ? 200 : 0);
    return () => clearTimeout(t);
  }, [query, reloadConversations]);

  useEffect(() => {
    if (!activeId) { setMessages([]); return; }
    if (live?.conversationId === activeId) return;
    api.messages(activeId).then(setMessages).catch((e) => toast((e as Error).message, "err"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  useEffect(() => {
    document.body.classList.toggle("grid-on", settings?.pixel_grid ?? true);
    document.body.classList.toggle("reduce-motion", settings?.reduce_motion ?? false);
  }, [settings?.pixel_grid, settings?.reduce_motion]);

  /* ------------------------------------------------------------------ chat */
  const upsertConv = (c: Conversation) =>
    setConversations((xs) => [c, ...xs.filter((x) => x.id !== c.id)].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at - a.updated_at));

  const send = async (text: string, forced: string[]) => {
    if (live) return;
    let cid = activeId;
    if (!cid) {
      try {
        const c = await api.createConversation();
        cid = c.id;
        upsertConv(c);
        setActiveId(cid);
      } catch (e) {
        toast((e as Error).message, "err");
        return;
      }
    }
    const conversationId = cid;
    setMessages((m) => [...m, { id: `tmp-${Date.now()}`, conversation_id: conversationId, role: "user", content: text, tool_events: [], created_at: Date.now() / 1000 }]);
    setLive({ conversationId, text: "", tools: [] });
    const update = (fn: (l: Live) => Live) => setLive((l) => (l && l.conversationId === conversationId ? fn(l) : l));
    const upsertTool = (t: ToolEvent) => update((l) => ({ ...l, status: undefined, tools: [...l.tools.filter((x) => x.call_id !== t.call_id), t] }));
    let pending = "";
    let frame = 0;
    const flush = () => { frame = 0; const chunk = pending; pending = ""; update((l) => ({ ...l, status: undefined, text: l.text + chunk })); };

    try {
      await streamChat(conversationId, text, forced, (ev) => {
        switch (ev.type) {
          case "run.start": update((l) => ({ ...l, runId: ev.run_id })); upsertConv(ev.conversation); break;
          case "delta": pending += ev.text; if (!frame) frame = requestAnimationFrame(flush); break;
          case "status": update((l) => ({ ...l, status: ev.text })); break;
          case "confirm.request":
            upsertTool({ call_id: ev.call_id, name: ev.name, server: ev.server, args: ev.args, status: "waiting", ok: false, preview: "", ms: 0, description: ev.description });
            if (narrow()) setDrawer(null);
            break;
          case "tool.start": case "tool.end": { const { type: _t, ...rest } = ev; void _t; upsertTool(rest as ToolEvent); break; }
          case "error": toast(ev.message, "err"); break;
          case "done": upsertConv(ev.conversation); break;
        }
      });
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      if (frame) cancelAnimationFrame(frame);
      if (activeRef.current === conversationId) {
        try { setMessages(await api.messages(conversationId)); } catch { /* keep what we have */ }
      }
      setLive(null);
      refreshHealth();
    }
  };

  const decide = (callId: string, approved: boolean) => {
    if (!live?.runId) return;
    setLive((l) => (l ? { ...l, tools: l.tools.map((t) => (t.call_id === callId ? { ...t, status: approved ? "running" : "rejected" } : t)) } : l));
    api.confirm(live.runId, callId, approved).catch((e) => toast((e as Error).message, "err"));
  };
  const stop = () => { if (live?.runId) api.cancel(live.runId); };

  const newChat = () => {
    setActiveId(null);
    setMessages([]);
    setView("chat");
    setDrawer(null);
    setTimeout(() => document.getElementById("prompt")?.focus(), 0);
  };

  /* ------------------------------------------------------------------ commands */
  const openWizard = useCallback((kind: WizardKind) => {
    const g = connections.find((c) => c.id === "gmail");
    const startAt = kind === "google" && g && ["needs_auth", "needs_reauth"].includes(g.auth_state ?? g.state) ? 3 : 0;
    setWizard({ kind, startAt });
  }, [connections]);

  const toggleRun = () => {
    if (narrow()) setDrawer(drawer === "run" ? null : "run");
    else setRunOpen(!runOpen);
  };

  const command = (cmd: string) => {
    if (cmd.startsWith("wizard:")) return openWizard(cmd.slice(7) as WizardKind);
    switch (cmd) {
      case "new": return newChat();
      case "tools": return setToolsDlg({ open: true });
      case "connections": return setView("connections");
      case "settings": return setView("settings");
      case "search": setView("chat"); if (narrow()) setDrawer("side"); setTimeout(() => searchRef.current?.focus(), 50); return;
      case "setup": return setOnboarding(true);
      case "run": setView("chat"); return toggleRun();
    }
  };

  const commands: Command[] = useMemo(() => [
    { id: "new", label: "New chat", icon: "plus", run: () => command("new") },
    { id: "search", label: "Search conversations", icon: "search", run: () => command("search") },
    { id: "chat", label: "Open Chat", icon: "chat", run: () => setView("chat") },
    { id: "conn", label: "Open Connections", icon: "plug", run: () => setView("connections") },
    { id: "settings", label: "Open Settings", icon: "sliders", run: () => setView("settings") },
    { id: "google", label: "Connect Google (Gmail + Calendar)", icon: "mail", run: () => openWizard("google") },
    { id: "notion", label: "Connect Notion", icon: "doc", run: () => openWizard("notion") },
    { id: "mcp", label: "Add MCP server", icon: "terminal", run: () => openWizard("mcp") },
    { id: "tools", label: "Show all tools", icon: "wrench", run: () => setToolsDlg({ open: true }) },
    { id: "run", label: "Toggle activity panel", icon: "panel", run: () => command("run") },
    { id: "export", label: "Export conversations", icon: "download", run: () => { window.location.href = "/api/v1/export"; } },
    { id: "setup", label: "Run first-time setup", icon: "key", run: () => setOnboarding(true) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [openWizard, drawer, runOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette(true); }
      if (e.key === "Escape" && drawer) setDrawer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer]);

  /* ------------------------------------------------------------------ status */
  const offline = !online || health?.groq === "unreachable";
  const blocked = offline ? "You're offline. History and search still work."
    : health?.groq === "no_key" ? "Add your Groq API key in Settings to start chatting."
    : health?.groq === "invalid_key" ? "Groq rejected your API key. Update it in Settings." : null;
  const active = conversations.find((c) => c.id === activeId) ?? null;
  const showLive = live && live.conversationId === activeId ? live : null;

  return (
    <div className="shell">
      <TopNav view={view} setView={(v) => { setView(v); setDrawer(null); }} health={health} offline={offline} onPalette={() => setPalette(true)} />
      {offline ? (
        <div className="banner" role="status">
          <b>■ OFFLINE</b><span>Donna can't reach Groq. Your history and search still work, and you can send again once you're back online.</span>
          <button className="btn btn-sm btn-secondary" style={{ marginLeft: "auto" }} onClick={refreshHealth}>Retry</button>
        </div>
      ) : health && health.groq !== "ok" ? (
        <div className="banner" role="status">
          <b>■ SETUP</b><span>{health.groq === "no_key" ? "Add your free Groq API key to start chatting." : "Groq rejected your API key."}</span>
          <button className="btn btn-sm btn-secondary" style={{ marginLeft: "auto" }} onClick={() => setView("settings")}>Open Settings</button>
        </div>
      ) : null}

      <section className={`view${runOpen ? "" : " run-closed"}${drawer === "side" ? " side-open" : ""}${drawer === "run" ? " run-open" : ""}`}
        id="view-chat" hidden={view !== "chat"} aria-label="Chat">
        <Sidebar conversations={conversations} activeId={activeId} runningId={live?.conversationId ?? null} query={query}
          usage={health?.usage ?? settings?.usage ?? null} searchRef={searchRef} onQuery={setQuery}
          onOpen={(id) => { setActiveId(id); setDrawer(null); }} onNew={newChat}
          onPin={async (c) => { upsertConv(await api.updateConversation(c.id, { pinned: !c.pinned })); }}
          onRename={async (c, title) => { upsertConv(await api.updateConversation(c.id, { title })); }}
          onDelete={async (c) => {
            await api.deleteConversation(c.id);
            setConversations((xs) => xs.filter((x) => x.id !== c.id));
            if (c.id === activeId) newChat();
            toast(`Deleted “${c.title}”.`);
          }} />
        <Chat conversation={active} messages={messages} live={showLive} connections={connections} blocked={blocked} runOpen={runOpen}
          onSend={send} onStop={stop} onDecide={decide} onCommand={command}
          onOpenSide={() => setDrawer("side")} onToggleRun={toggleRun}
          onOpenRun={() => { if (narrow()) setDrawer("run"); else setRunOpen(true); }} />
        <RunPanel live={showLive} messages={messages} usage={health?.usage ?? null} model={settings?.model ?? ""} onClose={() => setDrawer(null)} />
        <div className="scrim" onClick={() => setDrawer(null)} />
      </section>

      {view === "connections" && (
        <section className="view scroll" id="view-connections" aria-label="Connections">
          <ConnectionsPage connections={connections} reload={reloadConnections} openWizard={openWizard} onTools={(c) => setToolsDlg({ open: true, only: c })} />
        </section>
      )}
      {view === "settings" && settings && (
        <section className="view scroll" id="view-settings" aria-label="Settings">
          <SettingsPage settings={settings} onChange={(s) => { setSettings(s); refreshHealth(); }}
            onWiped={() => { setConversations([]); newChat(); setView("settings"); }} onRunSetup={() => setOnboarding(true)} />
        </section>
      )}

      <Palette open={palette} commands={commands} onClose={() => setPalette(false)} />
      <ToolsDialog open={toolsDlg.open} only={toolsDlg.only} connections={connections} reload={reloadConnections} onClose={() => setToolsDlg({ open: false })} />
      {wizard?.kind === "google" && <GoogleWizard startAt={wizard.startAt} onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {wizard?.kind === "notion" && <NotionWizard onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {wizard?.kind === "mcp" && <McpWizard home={settings?.home ?? "C:\\Users\\you"} onClose={() => setWizard(null)} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {onboarding && settings && !wizard && (
        <Onboarding settings={settings} connections={connections} onSettings={(s) => { setSettings(s); refreshHealth(); }}
          onWizard={(k) => openWizard(k)}
          onClose={() => { setOnboarding(false); api.saveSettings({ onboarded: true }).then(setSettings); }}
          onFinish={(prompt) => {
            setOnboarding(false);
            api.saveSettings({ onboarded: true }).then(setSettings);
            refreshHealth();
            setView("chat");
            if (prompt) { newChat(); setTimeout(() => send(prompt, []), 50); }
          }} />
      )}
    </div>
  );
}

function TopNav({ view, setView, health, offline, onPalette }: {
  view: View; setView: (v: View) => void; health: Health | null; offline: boolean; onPalette: () => void;
}) {
  const tabs = useRef<HTMLElement>(null);
  const [ind, setInd] = useState({ w: 0, x: 0 });
  useLayoutEffect(() => {
    const measure = () => {
      const el = tabs.current?.querySelector<HTMLElement>('[aria-selected="true"]');
      if (el) setInd({ w: el.offsetWidth, x: el.offsetLeft });
    };
    measure();
    document.fonts?.ready.then(measure);
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [view]);
  const status = offline ? ["warn", "Offline"] : health?.groq === "ok" ? ["ok live", "Groq · Online"]
    : health?.groq === "no_key" ? ["warn", "No API key"] : health?.groq === "invalid_key" ? ["err", "Key rejected"] : ["off", "Checking…"];
  const items: [View, string, string][] = [["chat", "chat", "Chat"], ["connections", "plug", "Connections"], ["settings", "sliders", "Settings"]];
  return (
    <header className="topnav">
      <a className="brand" href="#" aria-label="Donna home" onClick={(e) => { e.preventDefault(); setView("chat"); }}>
        <span className="logo"><PixelD size={18} /></span><span className="word">DONNA</span>
      </a>
      <nav className="tabs" role="tablist" aria-label="Sections" ref={tabs}>
        <span className="tab-ind" aria-hidden="true" style={{ width: ind.w, transform: `translateX(${ind.x}px)` }} />
        {items.map(([v, icon, label]) => (
          <button key={v} className="tab" role="tab" aria-selected={view === v} onClick={() => setView(v)}>
            <Icon name={icon} /><span className="lbl">{label}</span>
          </button>
        ))}
      </nav>
      <div className="nav-right">
        <span className={`pill nav-status ${status[0]}`}>{status[1]}</span>
        <button className="nav-k" onClick={onPalette} aria-label="Open command palette"><Icon name="search" /><span className="lbl">Commands</span><kbd>Ctrl K</kbd></button>
      </div>
    </header>
  );
}
