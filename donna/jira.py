"""Jira Cloud through its REST API (v3), signed in with the owner's email and an Atlassian API token.

The site, email and token live in the vault. The token sees everything the owner sees, so what
contacts may ask about is limited in code: agent._run_tool sets every Jira call's `projects`
argument to the `jira_projects` setting on a contact's turn, and the functions here refuse or drop
anything outside those projects (keys are checked again on each issue that comes back).
"""
from __future__ import annotations

import re

import httpx

from . import vault

TIMEOUT = 20
KEY = re.compile(r"[A-Z][A-Z0-9_]{0,19}-\d{1,9}")
PROJECT = re.compile(r"[A-Z][A-Z0-9_]{0,19}")
ORDER_BY = re.compile(r"\border\s+by\b.*$", re.I | re.S)
NOT_CONNECTED = "Jira isn't connected yet. Open Donna → Connections → Jira and add your site and API token."
FIELDS = ["summary", "status", "assignee", "priority", "updated", "issuetype", "duedate", "project", "parent"]


class JiraError(Exception):
    """A failure the user can act on; the message is shown as it is."""


def normalize_site(site: str) -> str:
    host = re.sub(r"^https?://", "", (site or "").strip().lower()).split("/")[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net", host):
        raise JiraError("Enter your Jira Cloud site, like yourteam.atlassian.net.")
    return host


def connected() -> bool:
    return all(vault.get(k) for k in (vault.JIRA_SITE, vault.JIRA_EMAIL, vault.JIRA_TOKEN))


def projects_of(value: str | list | None) -> list[str]:
    """'EDTR, ops' → ['EDTR', 'OPS']; invalid keys are dropped."""
    items = value if isinstance(value, list) else str(value or "").split(",")
    return [p for p in (str(x).strip().upper() for x in items) if PROJECT.fullmatch(p)]


async def request(method: str, path: str, *, site: str = "", email: str = "", token: str = "", **kwargs):
    site = site or vault.get(vault.JIRA_SITE) or ""
    email, token = email or vault.get(vault.JIRA_EMAIL) or "", token or vault.get(vault.JIRA_TOKEN) or ""
    if not (site and email and token):
        raise JiraError(NOT_CONNECTED)
    try:
        async with httpx.AsyncClient(base_url=f"https://{site}", auth=(email, token), timeout=TIMEOUT,
                                     headers={"Accept": "application/json"}) as c:
            r = await c.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise JiraError(f"Can't reach {site}. Check the internet connection.") from e
    if r.status_code == 401:
        raise JiraError("Jira rejected the email or API token. Create a new token and reconnect Jira.")
    if r.status_code == 403:
        raise JiraError("Your Jira account isn't allowed to do that.")
    if r.status_code == 404:
        raise JiraError("Jira couldn't find that (check the issue key).")
    if r.status_code >= 400:
        try:
            body = r.json()
            detail = "; ".join(body.get("errorMessages") or []) or "; ".join(f"{k}: {v}" for k, v in (body.get("errors") or {}).items())
        except ValueError:
            detail = ""
        raise JiraError(f"Jira answered {r.status_code}: {(detail or r.text)[:300]}")
    return r.json() if r.content else {}


# ------------------------------------------------------------------ formatting
def adf_text(node, limit: int = 1500) -> str:
    """Atlassian Document Format → plain text."""
    out: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "text":
                out.append(n.get("text", ""))
            elif n.get("type") in ("mention", "emoji"):
                out.append((n.get("attrs") or {}).get("text", ""))
            elif n.get("type") == "hardBreak":
                out.append("\n")
            for child in n.get("content") or []:
                walk(child)
            if n.get("type") in ("paragraph", "heading", "listItem", "codeBlock", "blockquote"):
                out.append("\n")
        elif isinstance(n, list):
            for child in n:
                walk(child)
    walk(node)
    text = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def adf(text: str) -> dict:
    """Plain text → an ADF document, one paragraph per line."""
    paras = [{"type": "paragraph", "content": [{"type": "text", "text": line}]} if line.strip() else {"type": "paragraph"}
             for line in text.strip().splitlines()]
    return {"type": "doc", "version": 1, "content": paras or [{"type": "paragraph"}]}


def _who(user) -> str:
    return (user or {}).get("displayName") or "unassigned"


def line(issue: dict) -> str:
    f = issue.get("fields") or {}
    bits = [f"{issue['key']} · {f.get('summary', '')}", f"status: {(f.get('status') or {}).get('name', '?')}",
            f"assignee: {_who(f.get('assignee'))}"]
    if f.get("priority"):
        bits.append(f"priority: {f['priority'].get('name')}")
    if f.get("duedate"):
        bits.append(f"due {f['duedate']}")
    if f.get("updated"):
        bits.append(f"updated {f['updated'][:10]}")
    return " | ".join(bits)


def _in_scope(key: str, allowed: list[str]) -> bool:
    return not allowed or key.split("-")[0] in allowed


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ------------------------------------------------------------------ operations (the tools call these)
async def myself(**creds) -> dict:
    return await request("GET", "/rest/api/3/myself", **creds)


async def list_projects(allowed: list[str] | None = None, **creds) -> list[dict]:
    data = await request("GET", "/rest/api/3/project/search", params={"maxResults": 100, "orderBy": "key"}, **creds)
    out = [{"key": p["key"], "name": p.get("name", "")} for p in data.get("values", [])]
    return [p for p in out if _in_scope(f"{p['key']}-1", allowed or [])]


async def search(query: str = "", jql: str = "", project: str = "", status: str = "", assignee: str = "",
                 max_results: int = 10, allowed: list[str] | None = None) -> list[dict]:
    allowed = allowed or []
    clauses = []
    wanted = projects_of(project)
    if allowed:
        wanted = [p for p in wanted if p in allowed] if wanted else allowed
        if project and not wanted:
            raise JiraError(f"{project} isn't one of the projects shared here.")
        jql = ""  # a contact's turn never runs raw JQL (it could reach outside the shared projects)
    if wanted:
        clauses.append(f"project in ({', '.join(wanted)})")
    if query.strip():
        q = query.strip()
        clauses.append(f"key = {q.upper()}" if KEY.fullmatch(q.upper()) else f"text ~ {_quote(q[:200])}")
    if status.strip():
        clauses.append(f"status = {_quote(status.strip()[:60])}")
    if assignee.strip():
        a = assignee.strip()
        clauses.append("assignee = currentUser()" if a.lower() in ("me", "myself", "currentuser()") else
                       "assignee is EMPTY" if a.lower() in ("none", "unassigned") else f"assignee ~ {_quote(a[:80])}")
    if jql.strip():
        clauses.append("(" + ORDER_BY.sub("", jql.strip()) + ")")
    final = " AND ".join(clauses) or "created >= -30d"
    data = await request("POST", "/rest/api/3/search/jql",
                         json={"jql": f"{final} ORDER BY updated DESC", "maxResults": max(1, min(50, max_results)), "fields": FIELDS})
    return [i for i in data.get("issues", []) if _in_scope(i["key"], allowed)]


async def issue(key: str, allowed: list[str] | None = None) -> str:
    key = key.strip().upper()
    if not KEY.fullmatch(key):
        raise JiraError(f"'{key}' isn't an issue key (like EDTR-142).")
    if not _in_scope(key, allowed or []):
        raise JiraError(f"{key} is in a project that isn't shared here.")
    data = await request("GET", f"/rest/api/3/issue/{key}",
                         params={"fields": ",".join(FIELDS + ["description", "subtasks", "comment", "labels", "reporter"])})
    if not _in_scope(data["key"], allowed or []):  # the key moved to another project
        raise JiraError(f"{key} is in a project that isn't shared here.")
    f = data.get("fields") or {}
    out = [line(data)]
    if f.get("parent"):
        out.append(f"Parent: {f['parent']['key']} · {(f['parent'].get('fields') or {}).get('summary', '')}")
    if f.get("labels"):
        out.append("Labels: " + ", ".join(f["labels"]))
    if f.get("description"):
        out.append("Description:\n" + adf_text(f["description"]))
    subs = f.get("subtasks") or []
    if subs:
        done = sum(1 for s in subs if ((s.get("fields") or {}).get("status") or {}).get("statusCategory", {}).get("key") == "done")
        out.append(f"Subtasks ({done} of {len(subs)} done):")
        out += [f"- {s['key']} · {(s.get('fields') or {}).get('summary', '')} ({((s.get('fields') or {}).get('status') or {}).get('name', '?')})"
                for s in subs[:20]]
    comments = ((f.get("comment") or {}).get("comments") or [])[-5:]
    if comments:
        out.append("Latest comments:")
        out += [f"- {c.get('updated', c.get('created', ''))[:16].replace('T', ' ')} {_who(c.get('author'))}: {adf_text(c.get('body'), 400)}"
                for c in comments]
    return "\n".join(out)


async def comment(key: str, text: str, allowed: list[str] | None = None) -> str:
    key = key.strip().upper()
    if not KEY.fullmatch(key) or not _in_scope(key, allowed or []):
        raise JiraError(f"{key} isn't an issue in a project shared here.")
    if not text.strip():
        raise JiraError("The comment is empty.")
    await request("POST", f"/rest/api/3/issue/{key}/comment", json={"body": adf(text[:5000])})
    return f"Comment added to {key}."


async def move(key: str, status: str, allowed: list[str] | None = None) -> str:
    key = key.strip().upper()
    if not KEY.fullmatch(key) or not _in_scope(key, allowed or []):
        raise JiraError(f"{key} isn't an issue in a project shared here.")
    data = await request("GET", f"/rest/api/3/issue/{key}/transitions")
    options = data.get("transitions", [])
    want = status.strip().lower()
    pick = next((t for t in options if want in (t.get("name", "").lower(), (t.get("to") or {}).get("name", "").lower())), None)
    if not pick:
        names = ", ".join(sorted({(t.get("to") or {}).get("name", t.get("name", "")) for t in options}))
        raise JiraError(f"{key} can't move to '{status}' from its current status. It can move to: {names or 'nothing'}.")
    await request("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": pick["id"]}})
    return f"{key} moved to {(pick.get('to') or {}).get('name', pick.get('name'))}."
