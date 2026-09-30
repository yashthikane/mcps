import assert from "node:assert/strict";
import { test } from "node:test";
import { firstOccurrence, intervalMs, nextOccurrence, preview, ScheduleError, validate } from "./recurrence.js";
import type { Schedule } from "./types.js";

const d = (iso: string) => new Date(iso);
const once = (run_at: string): Schedule => ({ run_at: d(run_at), timezone: "Asia/Kolkata", recurrence_type: "once", recurrence_rule: null });

test("once: runs at run_at (even if past) and never repeats", () => {
  const s = once("2026-10-01T09:00:00+05:30");
  assert.equal(firstOccurrence(s, d("2026-10-02T00:00:00Z")).toISOString(), "2026-10-01T03:30:00.000Z");
  assert.equal(nextOccurrence(s, d("2026-10-01T03:30:00Z")), null);
});

test("interval: anchored at run_at, strictly after", () => {
  const s: Schedule = { run_at: d("2026-10-01T09:00:00Z"), timezone: "UTC", recurrence_type: "interval", recurrence_rule: "30m" };
  assert.equal(firstOccurrence(s, d("2026-10-01T08:00:00Z")).toISOString(), "2026-10-01T09:00:00.000Z");
  assert.equal(nextOccurrence(s, d("2026-10-01T09:00:00Z"))!.toISOString(), "2026-10-01T09:30:00.000Z");
  assert.equal(nextOccurrence(s, d("2026-10-01T10:10:00Z"))!.toISOString(), "2026-10-01T10:30:00.000Z");
  assert.equal(firstOccurrence(s, d("2026-10-01T10:10:00Z")).toISOString(), "2026-10-01T10:30:00.000Z");
  assert.equal(intervalMs("2h"), 7_200_000);
  assert.throws(() => intervalMs("0m"), ScheduleError);
  assert.throws(() => intervalMs("every day"), ScheduleError);
});

test("cron: evaluated in the task's timezone", () => {
  const s: Schedule = { run_at: null, timezone: "Asia/Kolkata", recurrence_type: "cron", recurrence_rule: "0 9 * * *" };
  // 09:00 IST = 03:30 UTC
  assert.equal(firstOccurrence(s, d("2026-10-01T00:00:00Z")).toISOString(), "2026-10-01T03:30:00.000Z");
  assert.equal(nextOccurrence(s, d("2026-10-01T03:30:00Z"))!.toISOString(), "2026-10-02T03:30:00.000Z");
  // an exact match at `now` is still due now
  assert.equal(firstOccurrence(s, d("2026-10-01T03:30:00Z")).toISOString(), "2026-10-01T03:30:00.000Z");
});

test("cron: keeps local wall-clock time across a DST change", () => {
  const s: Schedule = { run_at: null, timezone: "America/New_York", recurrence_type: "cron", recurrence_rule: "0 9 * * *" };
  const runs = preview(s, d("2026-10-31T12:00:00Z"), 3); // US DST ends on Nov 1 2026
  assert.deepEqual(runs.map((r) => r.toISOString()), [
    "2026-10-31T13:00:00.000Z", // 09:00 EDT
    "2026-11-01T14:00:00.000Z", // 09:00 EST
    "2026-11-02T14:00:00.000Z",
  ]);
});

test("cron: doesn't start before run_at", () => {
  const s: Schedule = { run_at: d("2026-10-05T00:00:00Z"), timezone: "UTC", recurrence_type: "cron", recurrence_rule: "0 9 * * *" };
  assert.equal(firstOccurrence(s, d("2026-10-01T00:00:00Z")).toISOString(), "2026-10-05T09:00:00.000Z");
});

test("validation errors are user-facing", () => {
  assert.throws(() => validate({ ...once("2026-10-01T09:00:00Z"), timezone: "Mars/Olympus" }), /Unknown timezone/);
  assert.throws(() => validate({ run_at: null, timezone: "UTC", recurrence_type: "once", recurrence_rule: null }), /needs run_at/);
  assert.throws(() => validate({ run_at: null, timezone: "UTC", recurrence_type: "cron", recurrence_rule: "0 9 * *" }), /5 fields/);
  assert.throws(() => validate({ run_at: null, timezone: "UTC", recurrence_type: "cron", recurrence_rule: "99 9 * * *" }), /Invalid cron/);
});
