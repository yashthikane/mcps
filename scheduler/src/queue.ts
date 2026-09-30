// Redis connection and the BullMQ queue. Jobs carry only {taskId, scheduledFor, manual}.
import { Queue } from "bullmq";
import { Redis } from "ioredis";
import type { JobData } from "./types.js";

export const QUEUE_NAME = "donna-tasks";

export const JOB_OPTIONS = {
  attempts: 3,
  backoff: { type: "exponential" as const, delay: 5_000 },
  removeOnComplete: { count: 500 },
  removeOnFail: { count: 1_000 },
};

/** BullMQ needs maxRetriesPerRequest: null so blocking commands survive reconnects. */
export function redisConnection(url: string): Redis {
  const conn = new Redis(url, { maxRetriesPerRequest: null, retryStrategy: (n) => Math.min(n * 500, 5_000) });
  conn.on("error", (e) => console.error(`[redis] ${e.message}`));
  return conn;
}

export function createQueue(connection: Redis): Queue<JobData> {
  return new Queue<JobData>(QUEUE_NAME, { connection, defaultJobOptions: JOB_OPTIONS });
}

/** Resolve within `ms` or reject, so an HTTP request fails clearly while Redis is down instead of hanging. */
export function within<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: NodeJS.Timeout;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out: is Redis running?`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
