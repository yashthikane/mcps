// Pure decision rules for the worker: what to do with a late occurrence, and which failures to retry.
import type { CatchUp, TaskType } from "./types.js";

export interface CatchUpLimits {
  reminderGraceMin: number;
  reminderMissedNotifyH: number;
  aiCatchupMin: number;
}

/**
 * Downtime shouldn't make Donna fire a pile of stale work.
 * - Reminders run if at most `reminderGraceMin` late. Up to `reminderMissedNotifyH` late they
 *   become a "missed reminder" notification; older ones are recorded as missed silently.
 * - AI tasks run within `aiCatchupMin`; later ones are recorded as missed (they cost Groq quota).
 * - Manual runs always run.
 */
export function catchUp(type: TaskType, scheduledFor: Date, now: Date, limits: CatchUpLimits, manual = false): CatchUp {
  if (manual) return "run";
  const lateMin = (now.getTime() - scheduledFor.getTime()) / 60_000;
  if (type === "reminder") {
    if (lateMin <= limits.reminderGraceMin) return "run";
    return lateMin <= limits.reminderMissedNotifyH * 60 ? "missed_notify" : "missed_silent";
  }
  return lateMin <= limits.aiCatchupMin ? "run" : "missed_silent";
}

/** Whether a failed call to FastAPI's execute endpoint is worth retrying. */
export function isRetryable(failure: { status?: number; network?: boolean; retryable?: boolean }): boolean {
  if (failure.network) return true; // FastAPI down or restarting
  if (failure.retryable !== undefined) return failure.retryable; // FastAPI classified it
  const s = failure.status ?? 0;
  return s >= 500 || s === 409 || s === 429 || s === 408; // busy, rate-limited, transient
}
