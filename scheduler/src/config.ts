// Scheduler configuration from environment variables. Secrets (the Postgres URL and the internal
// API token) are passed in by scripts/scheduler.ps1 from the vault; they are never logged.

function num(name: string, fallback: number): number {
  const raw = process.env[name];
  const value = raw === undefined || raw === "" ? fallback : Number(raw);
  if (!Number.isFinite(value) || value < 0) throw new Error(`${name} must be a non-negative number`);
  return value;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set. Start the scheduler with scripts\scheduler.ps1.`);
  return value;
}

export interface Config {
  redisUrl: string;
  postgresUrl: string;
  donnaApiUrl: string;
  internalToken: string;
  host: string;
  port: number;
  reminderGraceMin: number;
  reminderMissedNotifyH: number;
  aiCatchupMin: number;
  reconcileSec: number;
  executeTimeoutMs: number;
}

export function loadConfig(): Config {
  const host = process.env.SCHEDULER_HOST || "127.0.0.1";
  if (host !== "127.0.0.1") throw new Error("SCHEDULER_HOST must be 127.0.0.1: the scheduler only listens locally.");
  return {
    redisUrl: process.env.REDIS_URL || "redis://127.0.0.1:6379",
    postgresUrl: required("POSTGRES_URL"),
    donnaApiUrl: (process.env.DONNA_API_URL || "http://127.0.0.1:8765").replace(/\/$/, ""),
    internalToken: required("DONNA_INTERNAL_API_TOKEN"),
    host,
    port: num("SCHEDULER_PORT", 8766),
    reminderGraceMin: num("REMINDER_GRACE_MIN", 5),
    reminderMissedNotifyH: num("REMINDER_MISSED_NOTIFY_H", 12),
    aiCatchupMin: num("AI_CATCHUP_MIN", 60),
    reconcileSec: num("RECONCILE_SEC", 300),
    executeTimeoutMs: num("EXECUTE_TIMEOUT_SEC", 300) * 1000,
  };
}
