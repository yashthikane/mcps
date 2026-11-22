"""Audit log: security-relevant events and action results (SQLite `audit_log`).

Actors: owner, system, donna, contact:<id>, channel:<id>. Outcomes: ok, denied, failed, rejected.
"""
import json
import logging
import time

log = logging.getLogger("donna.audit")
KEEP_DAYS = 90


def record(store, actor: str, action: str, target: str = "", outcome: str = "ok", **detail) -> None:
    try:
        store.execute("INSERT INTO audit_log (at, actor, action, target, outcome, detail) VALUES (?,?,?,?,?,?)",
                      (time.time(), actor, action, str(target)[:200], outcome, json.dumps(detail, default=str)[:4000]))
    except Exception as e:  # noqa: BLE001 - auditing must never break the action it records
        log.warning("Couldn't write audit record %s: %s", action, e)


def recent(store, limit: int = 200, outcome: str | None = None) -> list[dict]:
    sql = "SELECT * FROM audit_log" + (" WHERE outcome=?" if outcome else "") + " ORDER BY id DESC LIMIT ?"
    rows = store.rows(sql, ((outcome, limit) if outcome else (limit,)))
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return rows


def prune(store) -> None:
    store.execute("DELETE FROM audit_log WHERE at < ?", (time.time() - KEEP_DAYS * 86400,))
