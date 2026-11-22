/* Which Thinking Orb state and label to show for a live run (spec §6.3).
   Only real run data is used: tool events, streamed text and the backend's status line. "listening" and
   "shaping" are never used because Donna has no voice input and no structured-output event. */
import type { OrbState } from "thinking-orbs";
import type { PermissionMode, ToolEvent } from "../api";

const READS = /^(search|list|get|read|query|check)/;

export interface LiveLike { text: string; tools: ToolEvent[]; status?: string }

export function liveOrb(live: LiveLike, mode: PermissionMode): { state: OrbState; label: string } {
  if (live.tools.some((t) => t.status === "waiting")) return { state: "breathing", label: "Waiting for your approval" };
  const running = [...live.tools].reverse().find((t) => t.status === "running");
  if (running) {
    const name = running.name.split("__").pop() ?? running.name;
    return READS.test(name) ? { state: "searching", label: `Looking it up · ${name}` } : { state: "working", label: `Working · ${name}` };
  }
  if (live.status) return { state: "solving", label: live.status };
  if (live.text) return { state: "composing", label: "Writing…" };
  return mode === "plan" ? { state: "weaving", label: "Planning…" } : { state: "solving", label: "Thinking…" };
}
