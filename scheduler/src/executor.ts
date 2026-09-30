// Asks Donna (FastAPI) to execute an occurrence. The scheduler never runs tools or calls Groq itself.
import { isRetryable } from "./policy.js";
import type { ExecuteResult } from "./types.js";

export class ExecutorError extends Error {
  constructor(message: string, readonly retryable: boolean) {
    super(message);
  }
}

export class Executor {
  constructor(private apiUrl: string, private token: string, private timeoutMs: number) {}

  async execute(taskId: string, executionId: string, scheduledFor: Date, mode: "run" | "missed"): Promise<ExecuteResult> {
    let res: Response;
    try {
      res = await fetch(`${this.apiUrl}/api/v1/internal/scheduled-tasks/${taskId}/execute`, {
        method: "POST",
        headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json" },
        body: JSON.stringify({ execution_id: executionId, scheduled_for: scheduledFor.toISOString(), mode }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (e) {
      const why = (e as Error).name === "TimeoutError" ? "didn't answer in time" : "isn't reachable";
      throw new ExecutorError(`Donna's API ${why}.`, isRetryable({ network: true }));
    }
    let body: Partial<ExecuteResult> & { detail?: unknown } = {};
    try {
      body = (await res.json()) as typeof body;
    } catch {
      // non-JSON error page
    }
    if (!res.ok) {
      const detail = typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
      throw new ExecutorError(detail, isRetryable({ status: res.status }));
    }
    if (!body.success) {
      throw new ExecutorError(body.result || "The task failed.", isRetryable({ status: res.status, retryable: Boolean(body.retryable) }));
    }
    return body as ExecuteResult;
  }
}
