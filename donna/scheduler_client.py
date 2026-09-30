"""HTTP client for the Node scheduler service (127.0.0.1:8766). FastAPI tells it when a task's
schedule changed; the scheduler owns recurrence math and the BullMQ queue."""
import os

import httpx

from . import vault

SCHEDULER_URL = os.getenv("DONNA_SCHEDULER_URL", "http://127.0.0.1:8766")


class SchedulerUnavailable(Exception):
    """The scheduler service (or the Redis behind it) isn't answering."""


class ScheduleInvalid(ValueError):
    """The scheduler rejected the schedule (bad cron, timezone, interval…)."""


class SchedulerClient:
    def __init__(self, base_url: str = SCHEDULER_URL):
        self.base_url = base_url.rstrip("/")

    async def _post(self, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {vault.internal_token()}"}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(self.base_url + path, json=body or {}, headers=headers)
        except httpx.HTTPError as e:
            raise SchedulerUnavailable("The scheduler isn't running. Start Donna with scripts\\start.ps1.") from e
        data = _json(r)
        if r.status_code == 400:
            raise ScheduleInvalid(data.get("error") or "Invalid schedule.")
        if r.status_code == 404:
            raise LookupError(data.get("error") or "Task not found.")
        if r.status_code >= 300:
            raise SchedulerUnavailable(data.get("error") or f"Scheduler error {r.status_code}.")
        return data

    async def preview(self, schedule: dict, count: int = 3) -> list[str]:
        return (await self._post("/preview", {**schedule, "count": count}))["runs"]

    async def sync(self, task_id: str) -> dict:
        return await self._post(f"/tasks/{task_id}/sync")

    async def unschedule(self, task_id: str) -> None:
        await self._post(f"/tasks/{task_id}/unschedule")

    async def run(self, task_id: str) -> str:
        return (await self._post(f"/tasks/{task_id}/run"))["job_id"]

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(self.base_url + "/health")
            return _json(r) or {"status": "down"}
        except httpx.HTTPError:
            return {"status": "down", "redis": "unknown", "postgres": "unknown", "worker": "stopped"}


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
