"""Jira tools against a fake Jira Cloud (httpx.MockTransport): helpers, requests sent, reports."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from donna import vault
from tools import jira_tools as jt

IST = timezone(timedelta(hours=5, minutes=30))


def issue(key, summary="Fix login", status="In Progress", assignee="Priya", **fields):
    return {"key": key, "fields": {"summary": summary, "status": {"name": status, "statusCategory": {"key": "indeterminate"}},
                                   "assignee": {"displayName": assignee} if assignee else None,
                                   "priority": {"name": "Medium"}, **fields}}


@pytest.fixture
def jira(monkeypatch):
    """Saved credentials plus a fake Jira; returns (routes, calls). routes maps 'METHOD /path' → JSON or callable."""
    vault.set(vault.JIRA, json.dumps({"site": "https://acme.atlassian.net", "email": "me@acme.com", "token": "t" * 30}))
    routes, calls = {}, []

    def handler(req: httpx.Request):
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, dict(req.url.params), body))
        route = routes.get(f"{req.method} {req.url.path}")
        if route is None:
            return httpx.Response(404, json={"errorMessages": [f"no route {req.method} {req.url.path}"]})
        res = route(req, body) if callable(route) else route
        return res if isinstance(res, httpx.Response) else httpx.Response(200, json=res)

    monkeypatch.setattr(jt, "_TRANSPORT", httpx.MockTransport(handler))
    return routes, calls


def test_helpers():
    assert jt.normalize_site("acme") == "https://acme.atlassian.net"
    assert jt.normalize_site("https://Acme.atlassian.net/jira/software/projects/DON/boards/1") == "https://acme.atlassian.net"
    with pytest.raises(ValueError):
        jt.normalize_site("jira.acme.com")

    adf = jt.text_to_adf("Steps:\n- open app\n- tap login\n\nExpected: works")
    assert [b["type"] for b in adf["content"]] == ["paragraph", "bulletList", "paragraph"]
    assert jt.adf_to_text(adf) == "Steps:\n- open app\n- tap login\nExpected: works\n"

    t = jt.today()
    assert jt.parse_day("yesterday") == t - timedelta(days=1)
    assert jt.parse_day("7d") == t - timedelta(days=7)
    assert jt.parse_day("week").weekday() == 0
    assert jt.parse_day("2026-10-01") == date(2026, 10, 1)
    with pytest.raises(ValueError):
        jt.parse_day("someday")
    assert jt.parse_time("2026-10-24T10:15:00.000+0530") == datetime(2026, 10, 24, 10, 15, tzinfo=IST)
    assert jt.line(issue("DON-1", duedate="2026-11-01")) == "DON-1 | Fix login | In Progress | Priya | Medium | due 2026-11-01"


async def test_not_connected():
    assert (await jt.jira_list_projects()).startswith("Error: Jira isn't connected")


async def test_search_builds_jql_and_pages(jira):
    routes, calls = jira
    routes["GET /rest/api/3/user/search"] = [{"accountId": "acc-priya", "displayName": "Priya Shah", "accountType": "atlassian"}]
    pages = iter([{"issues": [issue("DON-1")], "nextPageToken": "p2"}, {"issues": [issue("DON-2")]}])
    routes["POST /rest/api/3/search/jql"] = lambda req, body: next(pages)
    out = await jt.jira_search_issues(project="don", assignee="Priya", status="open", text='login "bug"')
    searches = [c for c in calls if c[1] == "/rest/api/3/search/jql"]
    assert searches[0][3]["jql"] == ('project in ("don") AND text ~ "login \\"bug\\"" AND assignee = "acc-priya" '
                                     'AND statusCategory != Done ORDER BY updated DESC')
    assert searches[1][3]["nextPageToken"] == "p2"
    assert "DON-1 | Fix login" in out and "DON-2" in out


async def test_create_issue_sends_fields(jira):
    routes, calls = jira
    routes["GET /rest/api/3/myself"] = {"accountId": "acc-me", "displayName": "Me"}
    routes["POST /rest/api/3/issue"] = {"key": "DON-9"}
    out = await jt.jira_create_issue("don", "Add Jira", description="- one\n- two", issue_type="Story", assignee="me",
                                     priority="High", labels="backend, q4 goals", due_date="2026-11-15")
    fields = calls[-1][3]["fields"]
    assert fields["project"] == {"key": "DON"} and fields["issuetype"] == {"name": "Story"}
    assert fields["assignee"] == {"accountId": "acc-me"} and fields["labels"] == ["backend", "q4-goals"]
    assert fields["duedate"] == "2026-11-15" and fields["description"]["content"][0]["type"] == "bulletList"
    assert out == "Created DON-9: Add Jira | https://acme.atlassian.net/browse/DON-9"


async def test_transition_matches_status_or_lists_options(jira):
    routes, calls = jira
    routes["GET /rest/api/3/issue/DON-1/transitions"] = {"transitions": [
        {"id": "11", "name": "Start", "to": {"name": "In Progress"}}, {"id": "31", "name": "Finish", "to": {"name": "Done"}}]}
    routes["POST /rest/api/3/issue/DON-1/transitions"] = httpx.Response(204)
    assert await jt.jira_transition_issue("don-1", "done") == "Moved DON-1 to Done."
    assert calls[-1][3] == {"transition": {"id": "31"}}
    out = await jt.jira_transition_issue("DON-1", "Blocked")
    assert out.startswith("Error") and "Done, In Progress" in out


async def test_errors_are_strings(jira):
    routes, _ = jira
    routes["GET /rest/api/3/project/search"] = httpx.Response(401, json={"errorMessages": ["Unauthorized"]})
    out = await jt.jira_list_projects()
    assert out.startswith("Error listing Jira projects: Unauthorized") and "Connections → Jira" in out
    out = await jt.jira_get_issue("DON-404")  # no route → 404
    assert out.startswith("Error reading DON-404") and "issue key" in out


def test_digest_groups_by_person():
    start, end = datetime(2026, 10, 23, tzinfo=IST), datetime(2026, 10, 24, tzinfo=IST)
    issues = [{"key": "DON-1", "fields": {
        "summary": "Fix login", "created": "2026-10-23T09:00:00.000+0530", "reporter": {"displayName": "Priya"},
        "comment": {"comments": [{"author": {"displayName": "Rahul"}, "created": "2026-10-23T12:00:00.000+0530",
                                  "body": jt.text_to_adf("Looks good")},
                                 {"author": {"displayName": "Rahul"}, "created": "2026-10-20T12:00:00.000+0530", "body": "old"}]}},
        "changelog": {"histories": [
            {"author": {"displayName": "Priya"}, "created": "2026-10-23T15:00:00.000+0530",
             "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
            {"author": {"displayName": "Priya"}, "created": "2026-10-25T15:00:00.000+0530",  # after the window
             "items": [{"field": "status", "fromString": "Done", "toString": "Reopened"}]}]}}]
    out = jt.digest(issues, start, end, [])
    assert "## Priya (1 created, 1 completed)" in out and "DON-1 Fix login: In Progress → Done" in out
    assert "## Rahul (1 commented)" in out and "Looks good" in out and "Reopened" not in out and "old" not in out
    assert "Rahul" not in jt.digest(issues, start, end, ["priya"])


def test_workload_flags_overdue_and_stale():
    now = datetime(2026, 10, 24, 12, tzinfo=IST)
    issues = [issue("DON-1", duedate="2026-10-20", updated="2026-10-23T10:00:00.000+0530"),
              issue("DON-2", status="To Do", updated="2026-10-01T10:00:00.000+0530"),
              issue("DON-3", assignee=None, updated="2026-10-24T10:00:00.000+0530")]
    out = jt.workload(issues, 7, now)
    assert out.startswith("3 open issue(s)") and "- Priya: 2 open | 1 In Progress, 1 To Do" in out
    assert out.index("Priya") < out.index("Unassigned")
    assert "Overdue (1):\n- DON-1" in out and "Not updated in 7+ days (1):\n- DON-2" in out


async def test_worklogs_filter_by_window(jira):
    routes, calls = jira
    routes["POST /rest/api/3/search/jql"] = {"issues": [{"key": "DON-1", "fields": {"summary": "Fix login"}}]}
    routes["GET /rest/api/3/issue/DON-1/worklog"] = {"worklogs": [
        {"author": {"displayName": "Priya", "accountId": "a1"}, "started": "2026-10-23T10:00:00.000+0530", "timeSpentSeconds": 5400},
        {"author": {"displayName": "Rahul", "accountId": "a2"}, "started": "2026-10-23T11:00:00.000+0530", "timeSpentSeconds": 3600}]}
    out = await jt.jira_get_worklogs(since="2026-10-23", until="2026-10-23", project="DON")
    jql = next(c[3]["jql"] for c in calls if c[1] == "/rest/api/3/search/jql")
    assert jql == 'project in ("DON") AND worklogDate >= "2026-10-23" AND worklogDate <= "2026-10-23"'
    assert "## Priya: 1.5h total" in out and "By day: 2026-10-23 1.5h" in out and "## Rahul: 1.0h total" in out
    wl = next(c for c in calls if c[1].endswith("/worklog"))
    assert int(wl[2]["startedBefore"]) - int(wl[2]["startedAfter"]) == 86_400_000


async def test_sprint_status_summary(jira):
    routes, _ = jira
    routes["GET /rest/agile/1.0/board"] = {"values": [{"id": 7, "name": "DON board", "type": "scrum"}]}
    routes["GET /rest/agile/1.0/board/7/sprint"] = {"values": [
        {"id": 3, "name": "Sprint 12", "goal": "Ship Jira", "endDate": (datetime.now(IST) + timedelta(days=3)).isoformat()}]}
    routes["GET /rest/api/3/field"] = [{"id": "customfield_10016", "name": "Story point estimate"}]
    done = issue("DON-1", status="Done", customfield_10016=3)
    done["fields"]["status"]["statusCategory"]["key"] = "done"
    blocked = issue("DON-2", status="Blocked", assignee="Rahul", customfield_10016=5)
    routes["GET /rest/agile/1.0/sprint/3/issue"] = {"issues": [done, blocked], "total": 2}
    jt._points_field.clear()
    out = await jt.jira_sprint_status(project="DON")
    assert out.startswith("Sprint: Sprint 12 on board DON board |") and "Goal: Ship Jira" in out
    assert "2 issues: 1 Done, 1 In Progress, 0 To Do (50% done)" in out and "Story points: 3 Done, 5 In Progress, 0 To Do" in out
    assert "Possible blockers (1):\n- DON-2" in out


async def test_log_work(jira):
    routes, calls = jira
    routes["POST /rest/api/3/issue/DON-1/worklog"] = {"id": "1"}
    assert await jt.jira_log_work("DON-1", "1h 30m", comment="pairing", started="2026-10-24T09:00") == "Logged 1h 30m on DON-1."
    body = calls[-1][3]
    assert body["timeSpent"] == "1h 30m" and body["started"].startswith("2026-10-24T09:00:00.000") and body["comment"]["type"] == "doc"
