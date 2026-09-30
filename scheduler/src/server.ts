// Donna's scheduler service: HTTP API for FastAPI, BullMQ worker and the reconcile loop.
// Listens on 127.0.0.1 only. Start it with scripts\scheduler.ps1 (it passes the secrets in).
import { timingSafeEqual } from "node:crypto";
import Fastify from "fastify";
import { loadConfig } from "./config.js";
import { Db } from "./db.js";
import { Executor } from "./executor.js";
import { createQueue, redisConnection, within } from "./queue.js";
import { taskRoutes } from "./routes/tasks.js";
import { Scheduler } from "./scheduler.js";
import { startWorker } from "./worker.js";

const cfg = loadConfig();
const db = new Db(cfg.postgresUrl);
const queueConn = redisConnection(cfg.redisUrl);
const workerConn = redisConnection(cfg.redisUrl);
const queue = createQueue(queueConn);
const scheduler = new Scheduler(db, queue);
const executor = new Executor(cfg.donnaApiUrl, cfg.internalToken, cfg.executeTimeoutMs);
const worker = startWorker({ db, scheduler, executor, limits: cfg }, workerConn);

const expected = Buffer.from(`Bearer ${cfg.internalToken}`);
function authorized(header: string | undefined): boolean {
  const got = Buffer.from(header ?? "");
  return got.length === expected.length && timingSafeEqual(got, expected);
}

const app = Fastify({ logger: false, bodyLimit: 64 * 1024 });

app.addHook("onRequest", async (req, reply) => {
  // Browsers send Origin; only FastAPI (a server-side client) may call this service.
  if (req.headers.origin) return reply.code(403).send({ error: "Browser requests are not allowed." });
  if (req.url !== "/health" && !authorized(req.headers.authorization)) return reply.code(401).send({ error: "Unauthorized." });
});

app.get("/health", async () => {
  const [redis, postgres] = await Promise.all([
    within(queueConn.ping(), 2_000, "Redis ping").then(() => "ok", () => "down"),
    db.ping().then((ok) => (ok ? "ok" : "down")),
  ]);
  const running = worker.isRunning() ? "running" : "stopped";
  return { status: redis === "ok" && postgres === "ok" && running === "running" ? "ok" : "degraded", redis, postgres, worker: running };
});

taskRoutes(app, scheduler);

let reconciling = false;
async function reconcile(reason: string): Promise<void> {
  if (reconciling) return;
  reconciling = true;
  try {
    const { checked, fixed } = await within(scheduler.reconcile(), 60_000, "Reconcile");
    if (fixed) console.log(`[reconcile] ${reason}: re-queued ${fixed} of ${checked} active task(s)`);
  } catch (e) {
    console.warn(`[reconcile] ${reason}: ${(e as Error).message}`);
  } finally {
    reconciling = false;
  }
}

// Redis coming back (restart, WSL resumed) may have lost jobs: rebuild them from PostgreSQL.
queueConn.on("ready", () => void reconcile("redis connected"));
const timer = setInterval(() => void reconcile("periodic"), cfg.reconcileSec * 1000);

async function shutdown(): Promise<void> {
  clearInterval(timer);
  await app.close();
  await worker.close();
  await queue.close();
  await db.close();
  queueConn.disconnect();
  workerConn.disconnect();
  process.exit(0);
}
process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());

await app.listen({ host: cfg.host, port: cfg.port });
console.log(`Donna scheduler on http://${cfg.host}:${cfg.port} (queue: redis, state: postgres, worker concurrency 1)`);
