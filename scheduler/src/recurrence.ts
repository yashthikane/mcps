// Recurrence math. This is the only place occurrences are computed: FastAPI validates schedules
// through POST /preview instead of re-implementing it.
//
//   once      run_at
//   interval  every N minutes/hours/days ("30m", "2h", "1d"), anchored at run_at
//   cron      5-field cron in the task's own timezone ("0 9 * * 1-5")
import { CronExpressionParser } from "cron-parser";
import type { Schedule } from "./types.js";

export class ScheduleError extends Error {}

const INTERVAL = /^(\d+)\s*(m|h|d)$/i;
const UNIT_MS: Record<string, number> = { m: 60_000, h: 3_600_000, d: 86_400_000 };

export function intervalMs(rule: string): number {
  const m = INTERVAL.exec(rule.trim());
  if (!m) throw new ScheduleError(`Interval must look like 30m, 2h or 1d (got "${rule}").`);
  const ms = Number(m[1]) * UNIT_MS[m[2].toLowerCase()];
  if (ms < 60_000) throw new ScheduleError("Interval must be at least 1 minute.");
  return ms;
}

function validTimezone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

function cronAfter(rule: string, tz: string, after: Date): Date {
  try {
    return CronExpressionParser.parse(rule, { currentDate: after, tz }).next().toDate();
  } catch (e) {
    throw new ScheduleError(`Invalid cron expression "${rule}": ${(e as Error).message}`);
  }
}

/** Throws ScheduleError with a user-facing message when the schedule can't work. */
export function validate(s: Schedule): void {
  if (!s.timezone || !validTimezone(s.timezone)) throw new ScheduleError(`Unknown timezone "${s.timezone}".`);
  switch (s.recurrence_type) {
    case "once":
      if (!s.run_at) throw new ScheduleError("A one-time task needs run_at.");
      return;
    case "interval":
      if (!s.run_at) throw new ScheduleError("An interval task needs run_at (the first run).");
      intervalMs(s.recurrence_rule ?? "");
      return;
    case "cron":
      if (!s.recurrence_rule || s.recurrence_rule.trim().split(/\s+/).length !== 5) {
        throw new ScheduleError("Cron rules need 5 fields: minute hour day-of-month month day-of-week.");
      }
      cronAfter(s.recurrence_rule, s.timezone, new Date());
      return;
    default:
      throw new ScheduleError(`Unknown recurrence type "${String(s.recurrence_type)}".`);
  }
}

/** The occurrence to schedule when a task is (re)activated at `now`. A one-time task keeps its
 *  run_at even when it is in the past, so the worker's catch-up policy decides what happens. */
export function firstOccurrence(s: Schedule, now: Date): Date {
  validate(s);
  if (s.recurrence_type === "once") return s.run_at!;
  if (s.recurrence_type === "interval") return s.run_at! >= now ? s.run_at! : nextOccurrence(s, now)!;
  // cron: the first match at or after both now and run_at
  const from = s.run_at && s.run_at > now ? s.run_at : now;
  return cronAfter(s.recurrence_rule!, s.timezone, new Date(from.getTime() - 1));
}

/** The first occurrence strictly after `after`, or null when the task doesn't repeat. */
export function nextOccurrence(s: Schedule, after: Date): Date | null {
  switch (s.recurrence_type) {
    case "once":
      return null;
    case "interval": {
      const step = intervalMs(s.recurrence_rule ?? "");
      const anchor = s.run_at!.getTime();
      if (anchor > after.getTime()) return new Date(anchor);
      const n = Math.floor((after.getTime() - anchor) / step) + 1;
      return new Date(anchor + n * step);
    }
    case "cron": {
      const from = s.run_at && s.run_at > after ? new Date(s.run_at.getTime() - 1) : after;
      return cronAfter(s.recurrence_rule!, s.timezone, from);
    }
  }
}

/** The next `count` occurrences from `now`, for the UI preview. */
export function preview(s: Schedule, now: Date, count = 3): Date[] {
  const out = [firstOccurrence(s, now)];
  while (out.length < count) {
    const next = nextOccurrence(s, out[out.length - 1]);
    if (!next) break;
    out.push(next);
  }
  return out;
}
