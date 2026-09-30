"""PostgreSQL for scheduling (tasks, executions, notifications). Everything else stays in SQLite.

Uses psycopg's synchronous pool from worker threads (`asyncio.to_thread`): psycopg's async mode
can't run on Windows' Proactor event loop, which uvicorn uses and stdio MCP servers need.

The pool opens lazily and Donna starts without PostgreSQL: only the scheduling endpoints fail
(503) until it is reachable.
"""
import asyncio
import logging
import threading
import time

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import ROOT, vault

log = logging.getLogger("donna.pg")
MIGRATIONS = ROOT / "migrations" / "postgres"
RETRY_AFTER = 15  # seconds between reconnect attempts while PostgreSQL is down
_LOCK_ID = 7_243_551  # pg_advisory_xact_lock key for migrations


class Unavailable(Exception):
    """PostgreSQL isn't configured or reachable."""


class Postgres:
    def __init__(self, url: str | None):
        self.url = url
        self.pool: ConnectionPool | None = None
        self.error = "" if url else "PostgreSQL isn't set up. Run scripts\\setup-scheduler.ps1."
        self._lock = threading.Lock()
        self._failed_at = 0.0  # don't retry a dead server on every request

    # ------------------------------------------------------------------ lifecycle (blocking)
    def _open(self) -> bool:
        if not self.url:
            return False
        with self._lock:
            if self.pool is not None and not self.pool.closed:
                return True
            if time.monotonic() - self._failed_at < RETRY_AFTER:
                return False
            pool = ConnectionPool(self.url, min_size=1, max_size=4, open=False,
                                  kwargs={"row_factory": dict_row, "autocommit": True})
            try:
                pool.open(wait=True, timeout=5)
            except Exception as e:  # noqa: BLE001 - keep Donna running without scheduling
                pool.close()
                self.error = f"PostgreSQL isn't reachable ({type(e).__name__})."
                self._failed_at = time.monotonic()
                log.warning("PostgreSQL unavailable: %s", e)
                return False
            self.pool, self.error = pool, ""
            return True

    async def open(self) -> bool:
        return await asyncio.to_thread(self._open)

    async def close(self) -> None:
        if self.pool:
            await asyncio.to_thread(self.pool.close)

    async def ensure(self) -> None:
        """Connect on demand (PostgreSQL may have started after Donna)."""
        if not await self.open():
            raise Unavailable(self.error or "PostgreSQL isn't reachable.")

    async def available(self) -> bool:
        try:
            await self.one("SELECT 1 AS ok")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ queries
    def _run(self, fn):
        if not self._open():
            raise Unavailable(self.error or "PostgreSQL isn't reachable.")
        with self.pool.connection(timeout=5) as conn:
            return fn(conn)

    async def all(self, sql: str, params=None) -> list[dict]:
        return await asyncio.to_thread(self._run, lambda c: c.execute(sql, params).fetchall())

    async def one(self, sql: str, params=None) -> dict | None:
        return await asyncio.to_thread(self._run, lambda c: c.execute(sql, params).fetchone())

    async def run(self, sql: str, params=None) -> int:
        """Execute a write; returns the affected row count."""
        return await asyncio.to_thread(self._run, lambda c: c.execute(sql, params).rowcount)

    # ------------------------------------------------------------------ migrations
    def _migrate(self, conn) -> list[str]:
        applied: list[str] = []
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                         "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if path.stem in done:
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
                applied.append(path.stem)
        return applied

    async def migrate(self) -> list[str]:
        """Apply migrations/postgres/*.sql that haven't run yet, in order. Returns what was applied."""
        applied = await asyncio.to_thread(self._run, self._migrate)
        if applied:
            log.info("Applied PostgreSQL migrations: %s", ", ".join(applied))
        return applied


def from_vault() -> Postgres:
    return Postgres(vault.postgres_url())
