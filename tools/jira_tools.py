# tools/jira_tools.py — Jira Cloud (the client lives in donna/jira.py)
#
# Every tool takes `projects`: on a contact's turn Donna overwrites it with the projects the owner
# shared (Connections → Jira), whatever the model wrote, so contacts can't reach other projects.

from donna import jira
from mcp_instance import mcp


async def _run(coro) -> str:
    try:
        return await coro
    except jira.JiraError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Error: Jira failed ({type(e).__name__}: {str(e)[:200]})."


@mcp.tool()
async def list_jira_projects(projects: str = "") -> str:
    """List the Jira projects (key and name). projects: leave empty."""
    async def go():
        found = await jira.list_projects(jira.projects_of(projects))
        return "\n".join(f"- {p['key']} · {p['name']}" for p in found) or "No projects."
    return await _run(go())


@mcp.tool()
async def search_jira_issues(query: str = "", project: str = "", status: str = "", assignee: str = "",
                             jql: str = "", max_results: int = 10, projects: str = "") -> str:
    """
    Find Jira issues, newest update first. Each line: KEY · summary | status | assignee | priority | due | updated.
    - query: words to search, or an issue key like EDTR-142.
    - project: a project key like EDTR. status: e.g. "In Progress", "Done". assignee: a name, "me" or "unassigned".
    - jql: optional raw JQL for anything else (e.g. "updated >= -7d"). projects: leave empty.
    Use get_jira_issue for the details, subtasks and comments of one issue.
    """
    async def go():
        found = await jira.search(query, jql, project, status, assignee, max_results, jira.projects_of(projects))
        return "\n".join(jira.line(i) for i in found) or "No matching issues."
    return await _run(go())


@mcp.tool()
async def get_jira_issue(key: str, projects: str = "") -> str:
    """One Jira issue in full: status, assignee, description, subtasks (with progress) and the latest comments.
    key: like EDTR-142. projects: leave empty."""
    return await _run(jira.issue(key, jira.projects_of(projects)))


@mcp.tool()
async def my_jira_issues(projects: str = "") -> str:
    """The open Jira issues assigned to the owner (the connected account). projects: leave empty."""
    async def go():
        found = await jira.search(assignee="me", jql="statusCategory != Done", max_results=25,
                                  allowed=jira.projects_of(projects))
        return "\n".join(jira.line(i) for i in found) or "No open issues assigned to you."
    return await _run(go())


@mcp.tool()
async def add_jira_comment(key: str, text: str, projects: str = "") -> str:
    """Add a comment to a Jira issue (posted from the owner's account). key: like EDTR-142. projects: leave empty."""
    return await _run(jira.comment(key, text, jira.projects_of(projects)))


@mcp.tool()
async def move_jira_issue(key: str, status: str, projects: str = "") -> str:
    """Move a Jira issue to another status, e.g. "In Progress" or "Done". projects: leave empty."""
    return await _run(jira.move(key, status, jira.projects_of(projects)))
