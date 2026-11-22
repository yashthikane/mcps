// Icons (Hugeicons free set, stroke-rounded, MIT) and Donna's orb logo.
import { useId } from "react";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import {
  AlarmClockIcon, Alert02Icon, ArrowDown01Icon, ArrowRight01Icon, ArrowRight02Icon, ArrowUp02Icon, Brain01Icon, Calendar03Icon,
  Call02Icon, Cancel01Icon, CommandIcon, ComputerTerminal01Icon, Copy01Icon, Database01Icon, Delete02Icon, Download01Icon, File01Icon,
  Folder01Icon, GitBranchIcon, Globe02Icon, InboxIcon, Key01Icon, Mail01Icon, Menu01Icon, Message01Icon, NoteEditIcon, Notion01Icon,
  PencilEdit02Icon, PinIcon, PlugSocketIcon, PlusSignIcon, RefreshIcon, ReplyIcon, Search01Icon, SentIcon, Settings01Icon, ShieldCheckIcon,
  SidebarLeftIcon, SlashIcon, SmileIcon, SparklesIcon, StopIcon, SunCloud01Icon, TaskDaily01Icon, Tick02Icon, Upload01Icon,
  UserGroupIcon, UserMultipleIcon, WhatsappIcon, Wrench01Icon, ZapIcon,
} from "@hugeicons/core-free-icons";

/* Short names used across the app → Hugeicons. Unknown names fall back to the sparkle. */
const ICONS: Record<string, IconSvgElement> = {
  chat: Message01Icon, inbox: InboxIcon, users: UserMultipleIcon, tasks: TaskDaily01Icon, plug: PlugSocketIcon, sliders: Settings01Icon,
  search: Search01Icon, plus: PlusSignIcon, send: ArrowUp02Icon, sent: SentIcon, stop: StopIcon, mail: Mail01Icon, cal: Calendar03Icon,
  doc: File01Icon, notion: Notion01Icon, sun: SunCloud01Icon, spark: SparklesIcon, check: Tick02Icon, shield: ShieldCheckIcon,
  note: NoteEditIcon, pen: PencilEdit02Icon, newchat: PencilEdit02Icon, pin: PinIcon, trash: Delete02Icon, panel: SidebarLeftIcon,
  menu: Menu01Icon, x: Cancel01Icon, folder: Folder01Icon, wrench: Wrench01Icon, bolt: ZapIcon, key: Key01Icon, globe: Globe02Icon,
  terminal: ComputerTerminal01Icon, upload: Upload01Icon, download: Download01Icon, db: Database01Icon, git: GitBranchIcon,
  smile: SmileIcon, refresh: RefreshIcon, arrow: ArrowRight02Icon, clock: AlarmClockIcon, brain: Brain01Icon, chevron: ArrowRight01Icon,
  chevdown: ArrowDown01Icon, alert: Alert02Icon, command: CommandIcon, slash: SlashIcon, copy: Copy01Icon, reply: ReplyIcon,
  whatsapp: WhatsappIcon, callback: Call02Icon, meeting: UserGroupIcon,
};

export function Icon({ name, size = 15, className, strokeWidth = 1.6 }: { name: string; size?: number; className?: string; strokeWidth?: number }) {
  return <HugeiconsIcon icon={ICONS[name] ?? SparklesIcon} size={size} strokeWidth={strokeWidth} className={`i ${className ?? ""}`} aria-hidden="true" />;
}

/* Donna's logo: the "Companion" orb (docs/design/donna-logo-final.html).
   Below 28px it drops the blur filters and uses gradient blobs with bigger eyes, so it stays crisp in the nav,
   chat avatars and the favicon. `thinking` speeds the colour drift and makes the eyes glance; `static` stops all motion. */
const ORB = { violet: "#8B5CF6", cyan: "#22D3EE", pink: "#F472B6", core: "#2A1F6E", deep: "#140F3A" };

export function DonnaOrb({ size = 24, state = "idle", title }: { size?: number; state?: "idle" | "thinking" | "static"; title?: string }) {
  const u = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const small = size < 28;
  const url = (n: string) => `url(#${n}${u})`;
  const blob = (n: string, c: string) => (
    <radialGradient id={n + u}><stop offset="0" stopColor={c} /><stop offset="1" stopColor={c} stopOpacity="0" /></radialGradient>
  );
  return (
    <svg className={`orb ${state}`} viewBox={small ? "13 13 74 74" : "0 0 100 100"} width={size} height={size} overflow="visible" style={{ display: "block" }}
      role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <defs>
        <radialGradient id={"dp" + u} cx=".5" cy=".42" r=".6"><stop offset="0" stopColor={ORB.core} /><stop offset="1" stopColor={ORB.deep} /></radialGradient>
        <radialGradient id={"sh" + u}><stop offset=".62" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity=".55" /></radialGradient>
        <radialGradient id={"hl" + u}><stop offset="0" stopColor="#fff" stopOpacity=".85" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></radialGradient>
        <linearGradient id={"rim" + u} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity=".55" /><stop offset=".45" stopColor="#fff" stopOpacity="0" />
          <stop offset=".8" stopColor={ORB.cyan} stopOpacity="0" /><stop offset="1" stopColor={ORB.cyan} stopOpacity=".7" />
        </linearGradient>
        <clipPath id={"cp" + u}><circle cx="50" cy="50" r="36" /></clipPath>
        {small ? <>{blob("bv", ORB.violet)}{blob("bc", ORB.cyan)}{blob("bp", ORB.pink)}</> : <>
          <filter id={"bl" + u} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7" /></filter>
          <filter id={"gl" + u} x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="9" /></filter>
        </>}
      </defs>
      {!small && <circle cx="50" cy="52" r="36" fill={ORB.violet} opacity=".45" filter={url("gl")} />}
      <g className="orb-body">
        <g clipPath={url("cp")}>
          <circle cx="50" cy="50" r="36" fill={url("dp")} />
          {small ? <>
            <circle cx="36" cy="38" r="30" fill={url("bv")} /><circle cx="66" cy="60" r="27" fill={url("bc")} /><circle cx="46" cy="76" r="22" fill={url("bp")} />
          </> : (
            <g filter={url("bl")} style={{ mixBlendMode: "screen" }}>
              <g className="orb-drift"><circle cx="36" cy="38" r="22" fill={ORB.violet} /><circle cx="66" cy="60" r="19" fill={ORB.cyan} /></g>
              <g className="orb-drift-r"><circle cx="48" cy="70" r="15" fill={ORB.pink} /><circle cx="62" cy="32" r="11" fill={ORB.cyan} opacity=".8" /></g>
            </g>
          )}
        </g>
        <circle cx="50" cy="50" r="36" fill={url("sh")} />
        <ellipse cx="38" cy="34" rx="15" ry="8" fill={url("hl")} transform="rotate(-28 38 34)" opacity={small ? 0.6 : 0.8} />
        <circle cx="50" cy="50" r="35.5" fill="none" stroke={url("rim")} strokeWidth={small ? 2 : 1.2} />
        <g className="orb-eyes" fill="#fff">
          {small ? <><rect x="36.5" y="40" width="10" height="20" rx="5" /><rect x="53.5" y="40" width="10" height="20" rx="5" /></>
            : <><rect x="39" y="43" width="7.5" height="15" rx="3.75" /><rect x="53.5" y="43" width="7.5" height="15" rx="3.75" /></>}
        </g>
      </g>
    </svg>
  );
}

/** Icon for a tool, from the connection it belongs to. */
export function toolIcon(name: string): string {
  if (/email|draft|mail/.test(name)) return "mail";
  if (/event/.test(name)) return "cal";
  if (/page|notion|database/.test(name)) return "doc";
  if (/weather/.test(name)) return "sun";
  if (name.includes("__")) return "terminal";
  return "bolt";
}
