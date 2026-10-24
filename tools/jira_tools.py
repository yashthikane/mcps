# tools/jira_tools.py — Jira Cloud tools (REST API v3 + Agile API 1.0, email + API token)
#
# API notes: issue search is POST /rest/api/3/search/jql (paged with nextPageToken; the
# old /search endpoint is gone). Descriptions, comments and worklog comments are Atlassian
# Document Format (ADF) JSON. People are referenced by accountId, never by name or email.

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import httpx

from donna import vault
from mcp_instance import mcp

NOT_CONNECTED = "Jira isn't connected yet. Open Donna → Connections → Jira and add your site, email and API token."
FIELDS = ["summary", "status", "assignee", "priority", "duedate", "updated", "issuetype"]
_TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport


class JiraError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def normalize_site(value: str) -> str:
    """'acme', 'acme.atlassian.net' or a full Jira URL → 'https://acme.atlassian.net'."""
    v = value.strip().rstrip("/")
    if v and "." not in v and "/" not in v:
        v += ".atlassian.net"
    host = urlparse(v if "://" in v else "https://" + v).netloc.lower()
    if not host.endswith(".atlassian.net") or host == ".atlassian.net":
        raise ValueError("Use your Jira Cloud site address, like yourteam.atlassian.net.")
    return "https://" + host


def creds() -> dict:
    raw = vault.get(vault.JIRA)
    if not raw:
        raise LookupError(NOT_CONNECTED)
    return json.loads(raw)


def _client(c: dict | None = None) -> httpx.AsyncClient:
    c = c or creds()
    return httpx.AsyncClient(base_url=c["site"], auth=(c["email"], c["token"]), timeout=30,
                             headers={"Accept": "application/json"}, transport=_TRANSPORT)


async def _req(http: httpx.AsyncClient, method: str, path: str, **kw):
    r = await http.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            body = r.json()
            msgs = list(body.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
            msg = "; ".join(msgs) or r.text[:300]
        except ValueError:
            msg = r.text[:300]
        raise JiraError(r.status_code, msg)
    return r.json() if r.content else {}


def _err(e: Exception, what: str) -> str:
    if isinstance(e, (LookupError, ValueError)):
        return f"Error: {e}"
    if isinstance(e, JiraError):
        hint = {401: " The Jira token is invalid or expired; update it in Donna → Connections → Jira.",
                403: " Your Jira account doesn't have permission for this.",
                404: " Check the issue key or project key (look it up with jira_search_issues / jira_list_projects).",
                429: " Jira is rate limiting requests; try again in a minute."}.get(e.status, "")
        return f"Error {what}: {e}{hint}"
    if isinstance(e, httpx.HTTPError):
        return f"Error {what}: can't reach Jira ({type(e).__name__}). Check your internet connection."
    return f"Error {what}: {e}"


async def check(site: str, email: str, token: str) -> tuple[str, list[str]]:
    """Validate credentials: returns (display name, up to 10 project names). Raises JiraError/httpx errors."""
    async with _client({"site": site, "email": email, "token": token}) as http:
        me = await _req(http, "GET", "/rest/api/3/myself")
        projects = await _req(http, "GET", "/rest/api/3/project/search", params={"maxResults": 10})
    return me.get("displayName") or email, [f"{p['name']} ({p['key']})" for p in projects.get("values", [])]


# ----------------------------------------------------------------------------- ADF
def adf_to_text(node) -> str:
    """Atlassian Document Format → plain text (paragraphs, lists, mentions, code)."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    kind, content = node.get("type"), node.get("content") or []
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "status", "date"):
        attrs = node.get("attrs") or {}
        return attrs.get("text") or attrs.get("shortName") or attrs.get("timestamp") or ""
    if kind == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")
    if kind == "listItem":
        return "- " + adf_to_text(content).strip() + "\n"
    if kind in ("paragraph", "heading", "codeBlock", "blockquote", "rule"):
        return adf_to_text(content).rstrip() + "\n"
    return adf_to_text(content)


def text_to_adf(text: str) -> dict:
    """Plain text → ADF: blank lines split paragraphs, '- ' / '* ' lines become bullets."""
    blocks, para, bullets = [], [], []

    def flush():
        if para:
            inline = []
            for i, line in enumerate(para):
                if i:
                    inline.append({"type": "hardBreak"})
                if line:
                    inline.append({"type": "text", "text": line})
            blocks.append({"type": "paragraph", "content": inline})
            para.clear()
        if bullets:
            blocks.append({"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": b}]}]}
                for b in bullets]})
            bullets.clear()

    for line in (text or "").splitlines():
        s = line.rstrip()
        if s.lstrip().startswith(("- ", "* ")):
            if para:
                flush()
            bullets.append(s.lstrip()[2:].strip() or " ")
        elif not s.strip():
            flush()
        else:
            if bullets:
                flush()
            para.append(s)
    flush()
    return {"type": "doc", "version": 1, "content": blocks or [{"type": "paragraph", "content": []}]}


# ----------------------------------------------------------------------------- helpers
def today() -> date:
    return datetime.now().astimezone().date()


def parse_day(value: str, default: str = "today") -> date:
    """'today', 'yesterday', 'week' (this Monday), '7d' (7 days ago) or 'YYYY-MM-DD'."""
    v = (value or default).strip().lower()
    t = today()
    if v == "today":
        return t
    if v == "yesterday":
        return t - timedelta(days=1)
    if v in ("week", "this week"):
        return t - timedelta(days=t.weekday())
    if m := re.fullmatch(r"(\d+)\s*d(ays?)?", v):
        return t - timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"Unknown date '{value}'. Use today, yesterday, week, 7d or YYYY-MM-DD.") from None


def parse_time(value: str | None) -> datetime | None:
    """Jira timestamps look like 2026-10-24T10:15:00.000+0530."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _name(user: dict | None, empty: str = "Unassigned") -> str:
    return (user or {}).get("displayName") or empty


def line(issue: dict) -> str:
    f = issue.get("fields") or {}
    parts = [issue.get("key", "?"), (f.get("summary") or "")[:120], (f.get("status") or {}).get("name", ""),
             _name(f.get("assignee")), (f.get("priority") or {}).get("name", "")]
    if f.get("duedate"):
        parts.append(f"due {f['duedate']}")
    if f.get("updated"):
        parts.append(f"updated {f['updated'][:10]}")
    return " | ".join(p for p in parts if p)


async def _user_id(http: httpx.AsyncClient, who: str) -> str | None:
    """Name, email, 'me' or an accountId → accountId. 'unassigned' → None."""
    w = who.strip()
    if w.lower() in ("unassigned", "none", "nobody"):
        return None
    if w.lower() in ("me", "myself", "i"):
        return (await _req(http, "GET", "/rest/api/3/myself"))["accountId"]
    if re.fullmatch(r"[0-9a-f]{24}|\d+:[0-9a-f-]{36}|[0-9a-zA-Z:_-]{20,}", w) and " " not in w and "@" not in w:
        return w
    users = [u for u in await _req(http, "GET", "/rest/api/3/user/search", params={"query": w, "maxResults": 10})
             if u.get("accountType") == "atlassian" and u.get("active", True)]
    if not users:
        raise LookupError(f"No Jira user matches '{who}'. Try jira_find_users.")
    exact = [u for u in users if u.get("displayName", "").lower() == w.lower()]
    if len(exact) == 1 or len(users) == 1:
        return (exact or users)[0]["accountId"]
    names = ", ".join(u.get("displayName", "?") for u in users[:5])
    raise LookupError(f"'{who}' matches several people ({names}). Use the full name.")


async def _jql_user(http: httpx.AsyncClient, who: str) -> str:
    uid = await _user_id(http, who)
    return "EMPTY" if uid is None else _q(uid)


async def _search(http: httpx.AsyncClient, jql: str, fields: list[str], limit: int = 50, expand: str = "") -> list[dict]:
    issues, token = [], None
    while len(issues) < limit:
        body = {"jql": jql, "fields": fields, "maxResults": min(100, limit - len(issues))}
        if expand:
            body["expand"] = expand
        if token:
            body["nextPageToken"] = token
        res = await _req(http, "POST", "/rest/api/3/search/jql", json=body)
        issues += res.get("issues", [])
        token = res.get("nextPageToken")
        if not token:
            break
    return issues[:limit]


def _and(*clauses: str) -> str:
    return " AND ".join(c for c in clauses if c)


def _project_clause(project: str) -> str:
    keys = [p.strip() for p in (project or "").split(",") if p.strip()]
    if not keys:
        return ""
    return f"project in ({', '.join(_q(k) for k in keys)})"


# ----------------------------------------------------------------------------- tools
@mcp.tool()
async def jira_list_projects() -> str:
    """List the Jira projects you can see, with their keys (used by the other Jira tools)."""
    try:
        async with _client() as http:
            res = await _req(http, "GET", "/rest/api/3/project/search", params={"maxResults": 50, "orderBy": "name"})
        rows = [f"{p['key']} | {p['name']} | {p.get('projectTypeKey', '')}" for p in res.get("values", [])]
        return "\n".join(rows) if rows else "No projects visible to this Jira account."
    except Exception as e:
        return _err(e, "listing Jira projects")


@mcp.tool()
async def jira_search_issues(jql: str = "", text: str = "", project: str = "", assignee: str = "",
                             status: str = "", max_results: int = 20) -> str:
    """
    Find Jira issues. Either pass raw `jql`, or combine the simple filters: `text` (words in
    summary/description), `project` (key, e.g. ABC; comma-separate several), `assignee`
    (name, email, "me" or "unassigned"), `status` (e.g. "In Progress", or "open" for not done).
    Newest updates first. Returns one line per issue: key | summary | status | assignee | priority.
    """
    try:
        async with _client() as http:
            if not jql.strip():
                st = status.strip()
                clauses = [_project_clause(project),
                           f"text ~ {_q(text)}" if text.strip() else "",
                           f"assignee = {await _jql_user(http, assignee)}" if assignee.strip() else "",
                           "statusCategory != Done" if st.lower() in ("open", "not done", "active")
                           else f"status = {_q(st)}" if st else ""]
                jql = (_and(*clauses) or "updated >= -30d") + " ORDER BY updated DESC"
            issues = await _search(http, jql, FIELDS, max(1, min(max_results, 50)))
        if not issues:
            return f"No issues match: {jql}"
        return f"{len(issues)} issue(s) for: {jql}\n" + "\n".join(line(i) for i in issues)
    except Exception as e:
        return _err(e, "searching Jira")


@mcp.tool()
async def jira_get_issue(key: str) -> str:
    """Read one Jira issue (key like ABC-123): details, description, subtasks, links and the latest comments."""
    try:
        async with _client() as http:
            i = await _req(http, "GET", f"/rest/api/3/issue/{key.strip().upper()}", params={
                "fields": "summary,status,assignee,reporter,priority,issuetype,labels,duedate,created,updated,"
                          "parent,subtasks,issuelinks,description,comment,timetracking,resolution"})
        f = i["fields"]
        out = [f"# {i['key']}: {f.get('summary', '')}",
               f"Type: {(f.get('issuetype') or {}).get('name', '')} | Status: {(f.get('status') or {}).get('name', '')}"
               f" | Priority: {(f.get('priority') or {}).get('name', '')}",
               f"Assignee: {_name(f.get('assignee'))} | Reporter: {_name(f.get('reporter'), '')}",
               f"Created: {(f.get('created') or '')[:10]} | Updated: {(f.get('updated') or '')[:10]}"
               + (f" | Due: {f['duedate']}" if f.get("duedate") else "")]
        if f.get("labels"):
            out.append("Labels: " + ", ".join(f["labels"]))
        if f.get("parent"):
            out.append(f"Parent: {f['parent']['key']} {f['parent'].get('fields', {}).get('summary', '')}")
        tt = f.get("timetracking") or {}
        if tt.get("timeSpent") or tt.get("originalEstimate"):
            out.append(f"Time: spent {tt.get('timeSpent', '0m')}, estimate {tt.get('originalEstimate', '-')},"
                       f" remaining {tt.get('remainingEstimate', '-')}")
        out += ["", "Description:", adf_to_text(f.get("description")).strip()[:2500] or "(none)"]
        if f.get("subtasks"):
            out += ["", "Subtasks:"] + [f"- {s['key']} {s['fields'].get('summary', '')} ({s['fields']['status']['name']})"
                                        for s in f["subtasks"]]
        links = []
        for ln in f.get("issuelinks") or []:
            other, rel = (ln.get("outwardIssue"), ln["type"]["outward"]) if ln.get("outwardIssue") else (ln.get("inwardIssue"), ln["type"]["inward"])
            if other:
                links.append(f"- {rel} {other['key']} {other['fields'].get('summary', '')}")
        if links:
            out += ["", "Links:"] + links
        comments = (f.get("comment") or {}).get("comments", [])[-5:]
        if comments:
            out += ["", f"Latest comments ({(f.get('comment') or {}).get('total', len(comments))} total):"]
            out += [f"- {_name(c.get('author'), '?')} ({c.get('created', '')[:10]}): {adf_to_text(c.get('body')).strip()[:400]}"
                    for c in comments]
        return "\n".join(out)[:6000]
    except Exception as e:
        return _err(e, f"reading {key}")


@mcp.tool()
async def jira_create_issue(project: str, summary: str, description: str = "", issue_type: str = "Task",
                            assignee: str = "", priority: str = "", labels: str = "", parent: str = "",
                            due_date: str = "") -> str:
    """
    Create a Jira issue. `project` is the project key (e.g. ABC). `issue_type`: Task, Bug, Story,
    Epic or Subtask (Subtask needs `parent`, an issue key). `assignee`: name, email or "me".
    `priority`: e.g. High, Medium, Low. `labels`: comma-separated. `due_date`: YYYY-MM-DD.
    `description`: plain text; lines starting with "- " become bullets.
    """
    try:
        async with _client() as http:
            fields = {"project": {"key": project.strip().upper()}, "summary": summary.strip(),
                      "issuetype": {"name": issue_type.strip() or "Task"}}
            if description.strip():
                fields["description"] = text_to_adf(description)
            if assignee.strip():
                fields["assignee"] = {"accountId": await _user_id(http, assignee)}
            if priority.strip():
                fields["priority"] = {"name": priority.strip()}
            if labels.strip():
                fields["labels"] = [x.strip().replace(" ", "-") for x in labels.split(",") if x.strip()]
            if parent.strip():
                fields["parent"] = {"key": parent.strip().upper()}
            if due_date.strip():
                fields["duedate"] = parse_day(due_date).isoformat()
            res = await _req(http, "POST", "/rest/api/3/issue", json={"fields": fields})
        return f"Created {res['key']}: {summary.strip()} | {creds()['site']}/browse/{res['key']}"
    except Exception as e:
        return _err(e, "creating the Jira issue")


@mcp.tool()
async def jira_update_issue(key: str, summary: str = "", description: str = "", assignee: str = "",
                            priority: str = "", labels: str = "", due_date: str = "") -> str:
    """
    Change fields of a Jira issue (key like ABC-123). Only the fields you pass are changed.
    `assignee`: name, email, "me" or "unassigned". `labels`: comma-separated (replaces the labels).
    `due_date`: YYYY-MM-DD, or "none" to clear it. To change status use jira_transition_issue.
    """
    try:
        k = key.strip().upper()
        async with _client() as http:
            fields: dict = {}
            if summary.strip():
                fields["summary"] = summary.strip()
            if description.strip():
                fields["description"] = text_to_adf(description)
            if priority.strip():
                fields["priority"] = {"name": priority.strip()}
            if labels.strip():
                fields["labels"] = [x.strip().replace(" ", "-") for x in labels.split(",") if x.strip()]
            if due_date.strip():
                fields["duedate"] = None if due_date.strip().lower() in ("none", "clear") else parse_day(due_date).isoformat()
            if assignee.strip():
                await _req(http, "PUT", f"/rest/api/3/issue/{k}/assignee", json={"accountId": await _user_id(http, assignee)})
            if fields:
                await _req(http, "PUT", f"/rest/api/3/issue/{k}", json={"fields": fields})
        changed = list(fields) + (["assignee"] if assignee.strip() else [])
        return f"Updated {k}: {', '.join(changed)}." if changed else "Nothing to change: pass at least one field."
    except Exception as e:
        return _err(e, f"updating {key}")


@mcp.tool()
async def jira_add_comment(key: str, text: str) -> str:
    """Add a comment to a Jira issue (key like ABC-123). Plain text; "- " lines become bullets."""
    try:
        k = key.strip().upper()
        async with _client() as http:
            await _req(http, "POST", f"/rest/api/3/issue/{k}/comment", json={"body": text_to_adf(text)})
        return f"Commented on {k}."
    except Exception as e:
        return _err(e, f"commenting on {key}")


@mcp.tool()
async def jira_transition_issue(key: str, status: str) -> str:
    """Move a Jira issue (key like ABC-123) to another status, e.g. "In Progress" or "Done"."""
    try:
        k = key.strip().upper()
        want = status.strip().lower()
        async with _client() as http:
            options = (await _req(http, "GET", f"/rest/api/3/issue/{k}/transitions")).get("transitions", [])
            pick = next((t for t in options if t["to"]["name"].lower() == want), None) \
                or next((t for t in options if t["name"].lower() == want), None) \
                or next((t for t in options if want and want in t["to"]["name"].lower()), None)
            if not pick:
                names = ", ".join(sorted({t["to"]["name"] for t in options})) or "none"
                return f"Error: {k} can't move to '{status}'. Available statuses from here: {names}."
            await _req(http, "POST", f"/rest/api/3/issue/{k}/transitions", json={"transition": {"id": pick["id"]}})
        return f"Moved {k} to {pick['to']['name']}."
    except Exception as e:
        return _err(e, f"moving {key}")


@mcp.tool()
async def jira_find_users(query: str = "", project: str = "") -> str:
    """Find Jira people by name or email. With `project` (key), lists people who can be assigned there."""
    try:
        async with _client() as http:
            if project.strip():
                users = await _req(http, "GET", "/rest/api/3/user/assignable/search",
                                   params={"project": project.strip().upper(), "query": query, "maxResults": 50})
            else:
                users = await _req(http, "GET", "/rest/api/3/user/search", params={"query": query or ".", "maxResults": 50})
        rows = [" | ".join(x for x in (u.get("displayName", "?"), u.get("emailAddress"), f"accountId: {u['accountId']}") if x)
                for u in users if u.get("accountType") == "atlassian" and u.get("active", True)]
        return "\n".join(rows) if rows else "No matching people."
    except Exception as e:
        return _err(e, "finding Jira users")


def digest(issues: list[dict], start: datetime, end: datetime, people: list[str]) -> str:
    """Group what happened between start and end by person (pure, so it is unit-tested)."""
    acts: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, Counter] = defaultdict(Counter)

    def add(who: str, kind: str, text: str):
        if people and not any(p in who.lower() for p in people):
            return
        acts[who].append(text)
        counts[who][kind] += 1

    for i in issues:
        f, key = i.get("fields") or {}, i.get("key", "?")
        title = f"{key} {(f.get('summary') or '')[:70]}"
        created = parse_time(f.get("created"))
        if created and start <= created < end:
            add(_name(f.get("reporter") or f.get("creator"), "?"), "created", f"created {title}")
        for h in (i.get("changelog") or {}).get("histories", []):
            at = parse_time(h.get("created"))
            if not at or not start <= at < end:
                continue
            who = _name(h.get("author"), "?")
            for it in h.get("items", []):
                field = it.get("field")
                if field == "status":
                    done = (it.get("toString") or "").lower() in ("done", "closed", "resolved")
                    add(who, "completed" if done else "moved", f"{title}: {it.get('fromString')} → {it.get('toString')}")
                elif field == "assignee":
                    add(who, "reassigned", f"{title}: assigned to {it.get('toString') or 'nobody'}")
        for c in (f.get("comment") or {}).get("comments", []):
            at = parse_time(c.get("created"))
            if at and start <= at < end:
                add(_name(c.get("author"), "?"), "commented", f"commented on {title}: {adf_to_text(c.get('body')).strip()[:100]}")
    if not acts:
        return "No Jira activity in this period."
    out = []
    for who in sorted(acts, key=lambda w: -len(acts[w])):
        summary = ", ".join(f"{n} {k}" for k, n in counts[who].most_common())
        out.append(f"## {who} ({summary})")
        out += [f"- {a}" for a in acts[who][:12]]
        if len(acts[who]) > 12:
            out.append(f"- …and {len(acts[who]) - 12} more")
    return "\n".join(out)


@mcp.tool()
async def jira_team_digest(since: str = "yesterday", until: str = "", project: str = "", people: str = "") -> str:
    """
    What the team did in Jira, grouped by person: issues created, status changes, completed,
    reassigned and comments. `since`/`until`: today, yesterday, week, 7d or YYYY-MM-DD
    (until is inclusive; empty = now). `project`: key(s), comma-separated. `people`: names to keep.
    Good for daily standups ("what did my team do yesterday?").
    """
    try:
        start_day = parse_day(since, "yesterday")
        end_day = parse_day(until) + timedelta(days=1) if until.strip() else today() + timedelta(days=1)
        tz = datetime.now().astimezone().tzinfo
        start = datetime.combine(start_day, datetime.min.time(), tz)
        end = datetime.combine(end_day, datetime.min.time(), tz)
        async with _client() as http:
            jql = _and(_project_clause(project), f'updated >= "{start_day.isoformat()}"') + " ORDER BY updated DESC"
            issues = await _search(http, jql, ["summary", "status", "reporter", "creator", "created", "comment"], 150,
                                   expand="changelog")
        names = [p.strip().lower() for p in people.split(",") if p.strip()]
        head = f"Jira activity {start_day.isoformat()} → {(end_day - timedelta(days=1)).isoformat()} ({len(issues)} issues touched)"
        return head + "\n" + digest(issues, start, end, names)
    except Exception as e:
        return _err(e, "building the team digest")


def workload(issues: list[dict], stale_days: int, now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    by: dict[str, list[dict]] = defaultdict(list)
    for i in issues:
        by[_name((i.get("fields") or {}).get("assignee"))].append(i)
    overdue, stale, out = [], [], []
    for who in sorted(by, key=lambda w: (w == "Unassigned", -len(by[w]))):
        items = by[who]
        st = Counter((i["fields"].get("status") or {}).get("name", "?") for i in items)
        pr = Counter((i["fields"].get("priority") or {}).get("name", "?") for i in items)
        out.append(f"- {who}: {len(items)} open | " + ", ".join(f"{n} {s}" for s, n in st.most_common())
                   + " | priority " + ", ".join(f"{n} {p}" for p, n in pr.most_common()))
        for i in items:
            f = i["fields"]
            if f.get("duedate") and date.fromisoformat(f["duedate"]) < now.date():
                overdue.append(f"- {line(i)}")
            up = parse_time(f.get("updated"))
            if up and (now - up).days >= stale_days:
                stale.append(f"- {line(i)}")
    text = [f"{len(issues)} open issue(s) by assignee:", *out]
    if overdue:
        text += ["", f"Overdue ({len(overdue)}):", *overdue[:15]]
    if stale:
        text += ["", f"Not updated in {stale_days}+ days ({len(stale)}):", *stale[:15]]
    return "\n".join(text)


@mcp.tool()
async def jira_workload(project: str = "", assignee: str = "", stale_days: int = 7) -> str:
    """
    Open (not done) Jira issues per person: counts by status and priority, plus overdue issues
    and issues not updated for `stale_days`. `project`: key(s); `assignee`: one person's name or "me".
    """
    try:
        async with _client() as http:
            jql = _and(_project_clause(project), "statusCategory != Done",
                       f"assignee = {await _jql_user(http, assignee)}" if assignee.strip() else "") + " ORDER BY priority DESC"
            issues = await _search(http, jql, FIELDS, 300)
        return workload(issues, max(1, stale_days)) if issues else "No open issues."
    except Exception as e:
        return _err(e, "checking workload")


_points_field: dict = {}


async def _story_points_field(http: httpx.AsyncClient) -> str | None:
    if "id" not in _points_field:
        fields = await _req(http, "GET", "/rest/api/3/field")
        _points_field["id"] = next((f["id"] for f in fields
                                    if f.get("name", "").lower() in ("story points", "story point estimate")), None)
    return _points_field["id"]


@mcp.tool()
async def jira_sprint_status(board: str = "", project: str = "") -> str:
    """
    Progress of the active sprint on a Jira Scrum board: done / in progress / to do (with story
    points if used), per-person breakdown, days left and likely blockers. Pass `board` (name or ID)
    or `project` (key); with neither, the first Scrum board is used.
    """
    try:
        async with _client() as http:
            params = {"maxResults": 50}
            if project.strip():
                params["projectKeyOrId"] = project.strip().upper()
            if board.strip() and not board.strip().isdigit():
                params["name"] = board.strip()
            if board.strip().isdigit():
                boards = [await _req(http, "GET", f"/rest/agile/1.0/board/{board.strip()}")]
            else:
                boards = (await _req(http, "GET", "/rest/agile/1.0/board", params=params)).get("values", [])
            scrum = [b for b in boards if b.get("type") == "scrum"]
            if not scrum:
                names = ", ".join(f"{b['name']} ({b['id']}, {b.get('type')})" for b in boards[:8]) or "none"
                return f"No Scrum board found (sprints need a Scrum board). Boards: {names}"
            b = scrum[0]
            sprints = (await _req(http, "GET", f"/rest/agile/1.0/board/{b['id']}/sprint", params={"state": "active"})).get("values", [])
            if not sprints:
                return f"Board {b['name']} has no active sprint."
            sp = sprints[0]
            pts = await _story_points_field(http)
            fields = FIELDS + ["labels"] + ([pts] if pts else [])
            issues, start = [], 0
            while True:
                page = await _req(http, "GET", f"/rest/agile/1.0/sprint/{sp['id']}/issue",
                                  params={"fields": ",".join(fields), "startAt": start, "maxResults": 100})
                issues += page.get("issues", [])
                start += 100
                if start >= page.get("total", 0) or start >= 400:
                    break
        cat = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
        totals, points = Counter(), Counter()
        people: dict[str, Counter] = defaultdict(Counter)
        blockers = []
        for i in issues:
            f = i["fields"]
            c = cat.get(((f.get("status") or {}).get("statusCategory") or {}).get("key"), "To Do")
            totals[c] += 1
            points[c] += float(f.get(pts) or 0) if pts else 0
            people[_name(f.get("assignee"))][c] += 1
            blocked = (f.get("priority") or {}).get("name", "").lower() in ("highest", "blocker") \
                or "block" in (f.get("status") or {}).get("name", "").lower() \
                or any("block" in lb.lower() for lb in f.get("labels") or [])
            if blocked and c != "Done":
                blockers.append(f"- {line(i)}")
        end = parse_time(sp.get("endDate"))
        left = f" | {(end.date() - today()).days} day(s) left (ends {end.date()})" if end else ""
        done_pct = round(100 * totals["Done"] / len(issues)) if issues else 0
        out = [f"Sprint: {sp['name']} on board {b['name']}{left}",
               *([f"Goal: {sp['goal']}"] if sp.get("goal") else []),
               f"{len(issues)} issues: " + ", ".join(f"{totals[k]} {k}" for k in ("Done", "In Progress", "To Do")) + f" ({done_pct}% done)"]
        if pts and sum(points.values()):
            out.append("Story points: " + ", ".join(f"{points[k]:g} {k}" for k in ("Done", "In Progress", "To Do")))
        out += ["", "Per person:"] + [f"- {w}: " + ", ".join(f"{n} {k}" for k, n in c.most_common())
                                      for w, c in sorted(people.items(), key=lambda x: -sum(x[1].values()))]
        if blockers:
            out += ["", f"Possible blockers ({len(blockers)}):", *blockers[:10]]
        return "\n".join(out)
    except Exception as e:
        return _err(e, "checking sprint status")


def worklog_report(logs: list[tuple[str, str, str, int]]) -> str:
    """logs: (person, day, issue line, seconds) → hours per person per day and per issue."""
    if not logs:
        return "No time logged in this period."
    per_person: dict[str, Counter] = defaultdict(Counter)
    per_issue: dict[str, Counter] = defaultdict(Counter)
    for who, day, issue, secs in logs:
        per_person[who][day] += secs
        per_issue[who][issue] += secs
    h = lambda s: f"{s / 3600:.1f}h"  # noqa: E731
    out = []
    for who in sorted(per_person, key=lambda w: -sum(per_person[w].values())):
        out.append(f"## {who}: {h(sum(per_person[who].values()))} total")
        out.append("By day: " + ", ".join(f"{d} {h(s)}" for d, s in sorted(per_person[who].items())))
        out += [f"- {i}: {h(s)}" for i, s in per_issue[who].most_common(8)]
    return "\n".join(out)


@mcp.tool()
async def jira_get_worklogs(since: str = "today", until: str = "", project: str = "", person: str = "") -> str:
    """
    Time logged in Jira per person, by day and by issue. `since`/`until`: today, yesterday,
    week, 7d or YYYY-MM-DD (until inclusive; empty = today). `project`: key(s); `person`: name or "me".
    """
    try:
        start_day = parse_day(since)
        end_day = parse_day(until) if until.strip() else today()
        tz = datetime.now().astimezone().tzinfo
        t0 = int(datetime.combine(start_day, datetime.min.time(), tz).timestamp() * 1000)
        t1 = int(datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tz).timestamp() * 1000)
        async with _client() as http:
            uid = await _user_id(http, person) if person.strip() else None
            jql = _and(_project_clause(project), f'worklogDate >= "{start_day}"', f'worklogDate <= "{end_day}"',
                       f"worklogAuthor = {_q(uid)}" if uid else "")
            issues = await _search(http, jql, ["summary"], 60)
            sem = asyncio.Semaphore(5)

            async def fetch(i):
                async with sem:
                    res = await _req(http, "GET", f"/rest/api/3/issue/{i['key']}/worklog",
                                     params={"startedAfter": t0, "startedBefore": t1, "maxResults": 1000})
                return i, res.get("worklogs", [])
            results = await asyncio.gather(*(fetch(i) for i in issues))
        logs = []
        for i, wls in results:
            label = f"{i['key']} {(i['fields'].get('summary') or '')[:60]}"
            for w in wls:
                if uid and (w.get("author") or {}).get("accountId") != uid:
                    continue
                started = parse_time(w.get("started"))
                logs.append((_name(w.get("author"), "?"), started.date().isoformat() if started else "?",
                             label, int(w.get("timeSpentSeconds") or 0)))
        return f"Time logged {start_day} → {end_day}\n" + worklog_report(logs)
    except Exception as e:
        return _err(e, "reading worklogs")


@mcp.tool()
async def jira_log_work(key: str, time_spent: str, comment: str = "", started: str = "") -> str:
    """
    Log time on a Jira issue (key like ABC-123). `time_spent` uses Jira format, e.g. "30m", "2h", "1h 30m".
    `started`: when the work started, "YYYY-MM-DDTHH:MM" local time (default now).
    """
    try:
        k = key.strip().upper()
        when = datetime.fromisoformat(started.strip()).astimezone() if started.strip() else datetime.now().astimezone()
        body = {"timeSpent": time_spent.strip(), "started": when.strftime("%Y-%m-%dT%H:%M:%S.000%z")}
        if comment.strip():
            body["comment"] = text_to_adf(comment)
        async with _client() as http:
            await _req(http, "POST", f"/rest/api/3/issue/{k}/worklog", json=body)
        return f"Logged {time_spent.strip()} on {k}."
    except Exception as e:
        return _err(e, f"logging work on {key}")
