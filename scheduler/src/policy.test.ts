import assert from "node:assert/strict";
import { test } from "node:test";
import { catchUp, isRetryable } from "./policy.js";

const limits = { reminderGraceMin: 5, reminderMissedNotifyH: 12, aiCatchupMin: 60 };
const at = new Date("2026-10-01T09:00:00Z");
const late = (min: number) => new Date(at.getTime() + min * 60_000);

test("reminders: on time, missed with a notice, missed silently", () => {
  assert.equal(catchUp("reminder", at, late(0), limits), "run");
  assert.equal(catchUp("reminder", at, late(4), limits), "run");
  assert.equal(catchUp("reminder", at, late(30), limits), "missed_notify");
  assert.equal(catchUp("reminder", at, late(13 * 60), limits), "missed_silent");
});

test("AI tasks only catch up within their window; manual runs always run", () => {
  assert.equal(catchUp("ai_task", at, late(59), limits), "run");
  assert.equal(catchUp("ai_task", at, late(6 * 60), limits), "missed_silent");
  assert.equal(catchUp("ai_task", at, late(6 * 60), limits, true), "run");
  assert.equal(catchUp("reminder", at, late(-1), limits), "run"); // early (clock skew)
});

test("retry classification", () => {
  assert.equal(isRetryable({ network: true }), true);
  assert.equal(isRetryable({ status: 503 }), true);
  assert.equal(isRetryable({ status: 409 }), true);
  assert.equal(isRetryable({ status: 404 }), false);
  assert.equal(isRetryable({ status: 401 }), false);
  assert.equal(isRetryable({ status: 200, retryable: false }), false);
  assert.equal(isRetryable({ status: 200, retryable: true }), true);
});
