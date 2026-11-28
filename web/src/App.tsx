import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamChat, type Availability, type PermissionMode, type Connection, type Conversation, type Health, type InboxCounts, type Message, type Settings, type ToolEvent } from "./api";
import { Icon } from "./icons";
import Chat, { MODES, type Live } from "./components/Chat";
import { StatusCard, StatusPill } from "./components/Availability";
import ChannelsSection from "./components/Channels";
import ContactsPage from "./components/Contacts";
import InboxPage from "./components/Inbox";
import ConnectionsPage, { GoogleWizard, ImagesWizard, JiraWizard, McpWizard, NotionWizard, ToolsDialog, type WizardKind } from "./components/Connections";
import Onboarding from "./components/Onboarding";
import Palette, { type Command } from "./components/Palette";
import SettingsPage from "./components/Settings";
import TasksPage from "./components/Tasks";
import Sidebar, { type View } from "./components/Sidebar";
import { useToast } from "./components/ui";

const narrow = () => window.matchMedia("(max-width:860px)").matches;

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
  const [drawer, setDrawer] = useState<"side" | null>(null);
  const [rail, setRail] = useState(() => localStorage.getItem("donna.rail") === "1");
  const [palette, setPalette] = useState(false);
  const [wizard, setWizard] = useState<{ kind: WizardKind; startAt: number } | null>(null);
  const [toolsDlg, setToolsDlg] = useState<{ open: boolean; only?: Connection }>({ open: false });
  const [onboarding, setOnboarding] = useState(false);
  const [taskSignal, setTaskSignal] = useState(0);
  const [counts, setCounts] = useState<InboxCounts | null>(null);
  const [avail, setAvail] = useState<Availability | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeId;

  /* ------------------------------------------------------------------ loading */
  const refreshAvailability = useCallback(async () => {
    try { setAvail(await api.availability()); } catch { /* the card shows "Loading…" until the API answers */ }
    // "donna off" sent from the phone flips the same switch: keep the top-bar button in step
    api.settings().then((s) => setSettings((cur) => (cur && cur.messaging_enabled !== s.messaging_enabled ? { ...cur, messaging_enabled: s.messaging_enabled } : cur))).catch(() => {});
  }, []);
  useEffect(() => {  // a status set from the phone or the calendar shows up within a minute
    refreshAvailability();
    const t = setInterval(() => { if (!document.hidden) refreshAvailability(); }, 60000);
    window.addEventListener("focus", refreshAvailability);
    return () => { clearInterval(t); window.removeEventListener("focus", refreshAvailability); };
  }, [refreshAvailability]);
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

  /* Inbox badge: pending approvals and messages waiting for the owner. */
  const refreshCounts = useCallback(async () => {
    try { setCounts(await api.inboxCounts()); } catch { /* the badge is optional */ }
  }, []);
  useEffect(() => {
    refreshCounts();
    const t = setInterval(() => { if (!document.hidden) refreshCounts(); }, 20000);
    return () => clearInterval(t);
  }, [refreshCounts]);

  /* Scheduled-task notifications (the in-app channel): show each once as a toast, then mark it read. */
  const openConversation = (id: string) => { setView("chat"); setDrawer(null); setActiveId(id); reloadConversations(); };
  const openConvRef = useRef(openConversation);
  openConvRef.current = openConversation;
  useEffect(() => {
    const poll = async () => {
      if (document.hidden) return;
      try {
        const notes = await api.notifications();
        if (!notes.length) return;
        for (const n of notes) {
          const text = n.body ? `${n.title}: ${n.body}` : n.title;
          const cid = n.conversation_id;
          toast(text, n.level === "info" ? "ok" : n.level === "warn" ? "warn" : "err",
            cid ? { label: "Open chat", run: () => openConvRef.current(cid) } : undefined);
        }
        await api.markNotificationsRead(notes.map((n) => n.id));
        if (notes.some((n) => n.conversation_id)) reloadConversations();
      } catch { /* scheduling is optional; the Tasks page explains what's down */ }
    };
    poll();
    const t = setInterval(poll, 20000);
    return () => clearInterval(t);
  }, [toast, reloadConversations]);

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
      }, mode);
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

  const mode = settings?.permission_mode ?? "manual";
  const setMode = (m: PermissionMode) => {
    if (m === mode) return;
    setSettings((s) => (s ? { ...s, permission_mode: m } : s));
    api.saveSettings({ permission_mode: m }).catch((e) => toast((e as Error).message, "err"));
    toast(MODES.find((x) => x.id === m)!.hint, m === "auto" ? "warn" : "ok");
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
    const drive = connections.find((c) => c.id === "drive");
    const img = connections.find((c) => c.id === "images");
    const jira = connections.find((c) => c.id === "jira");
    const signIn = (c?: Connection) => !!c && ["needs_auth", "needs_reauth"].includes(c.auth_state ?? c.state);
    // Google with a saved client (or only Drive missing): straight to the sign-in step
    const startAt = kind === "google" && (signIn(g) || (g?.auth_state === "connected" && signIn(drive))) ? 3
      : kind === "images" && img?.auth_state === "connected" ? 2  // already connected: straight to the model picker
      : kind === "jira" && jira?.auth_state === "connected" ? 2 : 0;  // straight to the shared projects
    setWizard({ kind, startAt });
  }, [connections]);

  const toggleRail = () => setRail((r) => { localStorage.setItem("donna.rail", r ? "0" : "1"); return !r; });

  const command = (cmd: string) => {
    if (cmd.startsWith("wizard:")) return openWizard(cmd.slice(7) as WizardKind);
    switch (cmd) {
      case "new": return newChat();
      case "tools": return setToolsDlg({ open: true });
      case "connections": return setView("connections");
      case "settings": return setView("settings");
      case "tasks": return setView("tasks");
      case "inbox": return setView("inbox");
      case "contacts": return setView("contacts");
      case "new-task": setView("tasks"); setTaskSignal((n) => n + 1); return;
      case "search": if (rail) toggleRail(); if (narrow()) setDrawer("side"); setTimeout(() => searchRef.current?.focus(), 50); return;
      case "setup": return setOnboarding(true);
    }
  };

  const commands: Command[] = useMemo(() => [
    { id: "new", label: "New chat", icon: "newchat", run: () => command("new") },
    { id: "search", label: "Search conversations", icon: "search", run: () => command("search") },
    { id: "chat", label: "Open Chat", icon: "chat", run: () => setView("chat") },
    ...MODES.map((m) => ({ id: `mode-${m.id}`, label: `Mode: ${m.label}`, icon: m.icon, run: () => setMode(m.id) })),
    { id: "inbox", label: "Open Inbox (WhatsApp, Telegram, approvals)", icon: "inbox", run: () => setView("inbox") },
    { id: "contacts", label: "Open Contacts", icon: "users", run: () => setView("contacts") },
    { id: "tasks", label: "Open Tasks", icon: "tasks", run: () => setView("tasks") },
    { id: "new-task", label: "New scheduled task", icon: "clock", run: () => { setView("tasks"); setTaskSignal((n) => n + 1); } },
    { id: "conn", label: "Open Connections", icon: "plug", run: () => setView("connections") },
    { id: "settings", label: "Open Settings", icon: "sliders", run: () => setView("settings") },
    { id: "google", label: "Connect Google (Gmail + Calendar)", icon: "mail", run: () => openWizard("google") },
    { id: "notion", label: "Connect Notion", icon: "doc", run: () => openWizard("notion") },
    { id: "images", label: "Image generation: connect or change the model", icon: "image", run: () => openWizard("images") },
    { id: "jira", label: "Connect Jira", icon: "jira", run: () => openWizard("jira") },
    { id: "mcp", label: "Add MCP server", icon: "terminal", run: () => openWizard("mcp") },
    { id: "tools", label: "Show all tools", icon: "wrench", run: () => setToolsDlg({ open: true }) },
    { id: "sidebar", label: "Collapse or expand the sidebar", icon: "panel", run: toggleRail },
    { id: "export", label: "Export conversations", icon: "download", run: () => { window.location.href = "/api/v1/export"; } },
    { id: "setup", label: "Run first-time setup", icon: "key", run: () => setOnboarding(true) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [openWizard, drawer, rail, mode]);

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
    : health?.groq === "no_key" ? (health.provider === "claude_code" ? "Claude Code isn't installed on this computer. Install it or switch to Groq in Settings."
      : "Add your Groq API key in Settings to start chatting.")
    : health?.groq === "invalid_key" ? "Groq rejected your API key. Update it in Settings." : null;
  const active = conversations.find((c) => c.id === activeId) ?? null;
  const showLive = live && live.conversationId === activeId ? live : null;
  const claude = health?.provider === "claude_code";
  const status: [string, string] = offline ? ["warn", "Offline"] : health?.groq === "ok" ? ["ok", claude ? "Claude · Ready" : "Groq · Online"]
    : health?.groq === "no_key" ? ["warn", claude ? "Claude Code missing" : "No API key"] : health?.groq === "invalid_key" ? ["err", "Key rejected"] : ["off", "Checking…"];
  const go = (v: View) => { setView(v); setDrawer(null); };

  return (
    <div className={`shell${rail ? " rail" : ""}${drawer === "side" ? " side-open" : ""}`}>
      <Sidebar view={view} onView={go} status={status} badge={(counts?.approvals ?? 0) + (counts?.escalated ?? 0) + (counts?.requests ?? 0)}
        conversations={conversations} activeId={activeId} runningId={live?.conversationId ?? null} query={query}
        usage={health?.usage ?? settings?.usage ?? null} searchRef={searchRef} onQuery={setQuery} onCollapse={toggleRail}
        statusCard={<StatusCard data={avail} open={statusOpen} onOpen={() => setStatusOpen(true)} onClose={() => setStatusOpen(false)} onSaved={setAvail} />}
        onOpen={(id) => { setActiveId(id); setView("chat"); setDrawer(null); }} onNew={newChat}
        onPin={async (c) => { upsertConv(await api.updateConversation(c.id, { pinned: !c.pinned })); }}
        onRename={async (c, title) => { upsertConv(await api.updateConversation(c.id, { title })); }}
        onDelete={async (c) => {
          await api.deleteConversation(c.id);
          setConversations((xs) => xs.filter((x) => x.id !== c.id));
          if (c.id === activeId) newChat();
          toast(`Deleted “${c.title}”.`);
        }} />
      <div className="scrim" onClick={() => setDrawer(null)} />

      <main className="mainpanel">
        <TopBar view={view} title={view === "chat" ? active?.title ?? null : null} model={claude ? "Claude Code" : (settings?.model ?? "").split("/").pop() ?? ""}
          tone={status[0]} rail={rail} messaging={settings ? settings.messaging_enabled : null}
          onMessaging={async (on) => {
            try {
              setSettings(await api.saveSettings({ messaging_enabled: on }));
              toast(on ? "Donna is on: she answers on WhatsApp and Telegram again." : "Donna is off: nothing is answered or sent. Messages are still saved.");
            } catch (e) { toast((e as Error).message, "err"); }
          }}
          statusPill={<StatusPill data={avail} onClick={() => { if (narrow()) setDrawer("side"); setStatusOpen(true); }} />}
          onMenu={() => setDrawer("side")} onExpand={toggleRail} onModel={() => go("settings")} onPalette={() => setPalette(true)} />
        {offline ? (
          <div className="banner" role="status">
            <Icon name="alert" size={15} /><span>Donna can't reach Groq. Your history and search still work, and you can send again once you're back online.</span>
            <button className="btn btn-sm btn-secondary" style={{ marginLeft: "auto" }} onClick={refreshHealth}>Retry</button>
          </div>
        ) : health && health.groq !== "ok" ? (
          <div className="banner" role="status">
            <Icon name="key" size={15} /><span>{health.groq === "no_key" ? (health.provider === "claude_code" ? "Claude Code isn't installed. Install it, or switch to Groq in Settings."
              : "Add your free Groq API key to start chatting.") : "Groq rejected your API key."}</span>
            <button className="btn btn-sm btn-secondary" style={{ marginLeft: "auto" }} onClick={() => go("settings")}>Open Settings</button>
          </div>
        ) : null}

        <section className="view" id="view-chat" hidden={view !== "chat"} aria-label="Chat">
          <Chat conversation={active} messages={messages} live={showLive} connections={connections} blocked={blocked}
            reducedMotion={settings?.reduce_motion ?? false}
            onSend={send} onStop={stop} onDecide={decide} onCommand={command} mode={mode} onMode={setMode} />
        </section>

      {view === "inbox" && (
        <section className="view scroll" id="view-inbox" aria-label="Inbox">
          <InboxPage onCounts={refreshCounts} />
        </section>
      )}
      {view === "contacts" && (
        <section className="view scroll" id="view-contacts" aria-label="Contacts">
          <ContactsPage />
        </section>
      )}
      {view === "tasks" && (
        <section className="view scroll" id="view-tasks" aria-label="Tasks">
          <TasksPage defaultTimezone={settings?.timezone || "UTC"} newSignal={taskSignal} onOpenConversation={openConversation}
            automationsOff={settings?.automations_enabled === false} />
        </section>
      )}
      {view === "connections" && (
        <section className="view scroll" id="view-connections" aria-label="Connections">
          <ConnectionsPage connections={connections} reload={reloadConnections} openWizard={openWizard} onTools={(c) => setToolsDlg({ open: true, only: c })}
            channels={<ChannelsSection />} />
        </section>
      )}
      {view === "settings" && settings && (
        <section className="view scroll" id="view-settings" aria-label="Settings">
          <SettingsPage settings={settings} onChange={(s) => { setSettings(s); refreshHealth(); }}
            onWiped={() => { setConversations([]); newChat(); setView("settings"); }} onRunSetup={() => setOnboarding(true)} />
        </section>
      )}

      </main>

      <Palette open={palette} commands={commands} onClose={() => setPalette(false)} />
      <ToolsDialog open={toolsDlg.open} only={toolsDlg.only} connections={connections} reload={reloadConnections} onClose={() => setToolsDlg({ open: false })} />
      {wizard?.kind === "google" && <GoogleWizard startAt={wizard.startAt} onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {wizard?.kind === "notion" && <NotionWizard onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {wizard?.kind === "jira" && <JiraWizard startAt={wizard.startAt} onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
      {wizard?.kind === "images" && <ImagesWizard startAt={wizard.startAt} onClose={() => { setWizard(null); reloadConnections(); }} onDone={() => { setWizard(null); reloadConnections(); }} />}
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

/** Slim bar at the top of the main panel: the model in use (new chat) or the chat title, and the command palette. */
function TopBar({ view, title, model, tone, rail, messaging, onMessaging, statusPill, onMenu, onExpand, onModel, onPalette }: {
  view: View; title: string | null; model: string; tone: string; rail: boolean; statusPill?: React.ReactNode;
  messaging: boolean | null; onMessaging: (on: boolean) => void;
  onMenu: () => void; onExpand: () => void; onModel: () => void; onPalette: () => void;
}) {
  return (
    <header className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Show navigation"><Icon name="menu" size={17} /></button>
      {rail && <button className="icon-btn expand-btn" onClick={onExpand} aria-label="Expand sidebar" title="Expand sidebar"><Icon name="panel" size={17} /></button>}
      {view === "chat" && (title
        ? <h1 className="tb-title">{title}</h1>
        : <button className="tb-pill" onClick={onModel} title="Model · change it in Settings"><span className={`dot ${tone}`} />{model || "Model"}<Icon name="chevdown" size={13} /></button>)}
      <span className="tb-sp" />
      {statusPill}
      {messaging !== null && (  // the global switch for WhatsApp/Telegram replies (settings.messaging_enabled)
        <button className={`tb-pill msg-switch ${messaging ? "on" : "off"}`} role="switch" aria-checked={messaging}
          onClick={() => onMessaging(!messaging)}
          title={messaging ? "Donna answers on WhatsApp and Telegram. Click to turn her off." : "Donna is off: nothing is answered or sent. Click to turn her on."}>
          <span className="msg-knob" aria-hidden /><span className="lb">Donna {messaging ? "on" : "off"}</span>
        </button>
      )}
      <button className="tb-pill" onClick={onPalette} aria-label="Open command palette"><Icon name="command" size={14} /><span className="lb">Commands</span><kbd>Ctrl K</kbd></button>
    </header>
  );
}
