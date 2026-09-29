# tools/notion_tools.py — Notion workspace tools (notion-client 3.x, Notion API 2025-09-03)
#
# API notes: databases hold one or more "data sources"; rows are queried with
# data_sources.query, search filters on "page" / "data_source", and pages are
# deleted by moving them to the trash with in_trash=True.

import os
import re

from dotenv import load_dotenv
from notion_client import APIResponseError, AsyncClient

from donna import vault
from mcp_instance import mcp

load_dotenv()

NOT_CONNECTED = "Notion isn't connected yet. Open Donna → Connections → Notion and paste your integration secret."
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")


def token() -> str | None:
    return vault.get(vault.NOTION_TOKEN) or os.getenv("NOTION_API_KEY") or os.getenv("INTERNAL_INTERGRATION_TOKEN")


def _client() -> AsyncClient:
    t = token()
    if not t:
        raise LookupError(NOT_CONNECTED)
    return AsyncClient(auth=t)


def _id(value: str) -> str:
    """Accept a raw ID, a dashed UUID or a full Notion URL (…/Page-Name-<32 hex>?v=…)."""
    segment = value.strip().split("?")[0].split("#")[0].rstrip("/").split("/")[-1].replace("-", "")
    raw = segment[-32:]
    if len(raw) == 32 and _HEX32.fullmatch(raw):
        raw = raw.lower()
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return value.strip()


def _plain(rich: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich or [])


def _title(obj: dict) -> str:
    if obj.get("object") in ("data_source", "database"):
        return _plain(obj.get("title", [])) or "Untitled"
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title", [])) or "Untitled"
    return "Untitled"


def _err(e: Exception, what: str) -> str:
    if isinstance(e, LookupError):
        return f"Error: {e}"
    if isinstance(e, APIResponseError):
        hint = ""
        if e.code in ("object_not_found", "restricted_resource"):
            hint = " Make sure the page is shared with your integration (••• → Connections → your integration)."
        elif e.code == "unauthorized":
            hint = " The Notion secret is invalid; update it in Donna → Connections → Notion."
        return f"Error {what}: {e}{hint}"
    return f"Error {what}: {e}"


def _rt(text: str) -> list:
    """Rich text, split into 2000-character pieces (Notion's per-object limit)."""
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)] or [
        {"type": "text", "text": {"content": ""}}]


def blocks_from_text(content: str) -> list:
    """Markdown-ish lines → Notion blocks: #/##/### headings, - bullets, 1. numbers, [ ]/[x] to-dos, > quotes."""
    blocks = []
    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("### "):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": _rt(s[4:])}})
        elif s.startswith("## "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": _rt(s[3:])}})
        elif s.startswith("# "):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": _rt(s[2:])}})
        elif re.match(r"^(- )?\[( |x|X)\] ", s):
            checked = "[x]" in s[:6].lower()
            text = re.sub(r"^(- )?\[( |x|X)\] ", "", s)
            blocks.append({"type": "to_do", "to_do": {"rich_text": _rt(text), "checked": checked}})
        elif s.startswith(("- ", "* ")):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt(s[2:])}})
        elif re.match(r"^\d+[.)] ", s):
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt(re.sub(r'^\d+[.)] ', '', s))}})
        elif s.startswith("> "):
            blocks.append({"type": "quote", "quote": {"rich_text": _rt(s[2:])}})
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": _rt(s)}})
    return blocks


def _block_line(b: dict) -> str:
    t = b.get("type", "")
    data = b.get(t, {}) or {}
    text = _plain(data.get("rich_text", []))
    return {
        "heading_1": f"# {text}", "heading_2": f"## {text}", "heading_3": f"### {text}",
        "bulleted_list_item": f"- {text}", "numbered_list_item": f"1. {text}",
        "to_do": f"[{'x' if data.get('checked') else ' '}] {text}", "quote": f"> {text}",
        "code": f"```\n{text}\n```", "callout": f"💡 {text}", "toggle": f"▸ {text}",
        "child_page": f"📄 {data.get('title', '')} (page ID: {b.get('id')})",
        "child_database": f"🗃 {data.get('title', '')} (database ID: {b.get('id')})",
        "divider": "---",
    }.get(t, text)


async def _children(notion: AsyncClient, block_id: str, depth: int = 0, limit: int = 300) -> list[str]:
    lines, cursor = [], None
    while len(lines) < limit:
        kwargs = {"block_id": block_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        res = await notion.blocks.children.list(**kwargs)
        for b in res.get("results", []):
            line = _block_line(b)
            if line:
                lines.append("  " * depth + line)
            if b.get("has_children") and depth < 2 and b["type"] not in ("child_page", "child_database"):
                lines.extend(await _children(notion, b["id"], depth + 1, limit - len(lines)))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return lines


async def _data_source_id(notion: AsyncClient, some_id: str) -> str:
    """Accept either a database ID or a data source ID and return a data source ID."""
    try:
        db = await notion.databases.retrieve(database_id=some_id)
        sources = db.get("data_sources") or []
        if sources:
            return sources[0]["id"]
    except APIResponseError:
        pass
    return some_id


def _prop_text(p: dict) -> str:
    t = p.get("type")
    v = p.get(t)
    if t in ("title", "rich_text"):
        return _plain(v)
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(o.get("name", "") for o in v or [])
    if t == "date":
        return (v or {}).get("start", "") or ""
    if t in ("number", "checkbox", "url", "email", "phone_number"):
        return "" if v is None else str(v)
    if t == "people":
        return ", ".join(x.get("name", "") for x in v or [])
    return ""


@mcp.tool()
async def search_notion(query: str = "", kind: str = "page", max_results: int = 20) -> str:
    """
    Search pages (kind="page") or databases (kind="database") shared with Donna's integration.
    Leave query empty to list everything. Results include IDs used by the other Notion tools.
    """
    try:
        notion = _client()
        value = "data_source" if kind.startswith("data") else "page"
        res = await notion.search(query=query, filter={"property": "object", "value": value},
                                  page_size=max(1, min(max_results, 50)))
        items = res.get("results", [])
        if not items:
            return "Nothing found. Share pages with the integration in Notion (••• → Connections) to make them visible."
        out = []
        for it in items:
            if it["object"] == "data_source":
                db_id = (it.get("parent") or {}).get("database_id", "")
                out.append(f"[DATABASE] {_title(it)} | database ID: {db_id} | data source ID: {it['id']}")
            else:
                out.append(f"[PAGE] {_title(it)} | ID: {it['id']} | {it.get('url', '')}")
        return "\n".join(out)
    except Exception as e:
        return _err(e, "searching Notion")


@mcp.tool()
async def list_pages() -> str:
    """List all pages and databases shared with Donna's Notion integration."""
    pages = await search_notion(kind="page", max_results=50)
    dbs = await search_notion(kind="database", max_results=50)
    return f"{pages}\n\n{dbs}"


@mcp.tool()
async def read_page_content(page_id: str) -> str:
    """Read a Notion page (ID or URL) as plain text, including its properties."""
    try:
        notion = _client()
        pid = _id(page_id)
        page = await notion.pages.retrieve(page_id=pid)
        props = [f"{name}: {_prop_text(p)}" for name, p in page.get("properties", {}).items()
                 if p.get("type") != "title" and _prop_text(p)]
        body = await _children(notion, pid)
        head = f"# {_title(page)}\nID: {pid} | {page.get('url', '')}"
        return "\n".join([head, *props, "", *(body or ["(empty page)"])])[:8000]
    except Exception as e:
        return _err(e, "reading page")


@mcp.tool()
async def create_page(parent_id: str, title: str, content: str = "", parent_type: str = "page") -> str:
    """
    Create a Notion page inside a parent page (parent_type="page") or as a new row in a
    database (parent_type="database"). `content` is optional text; lines starting with
    #, ##, -, 1., [ ] become headings, bullets, numbered items and to-dos.
    """
    try:
        notion = _client()
        pid = _id(parent_id)
        children = blocks_from_text(content)[:100] if content else []
        if parent_type.startswith("data"):
            ds_id = await _data_source_id(notion, pid)
            ds = await notion.data_sources.retrieve(data_source_id=ds_id)
            title_prop = next((n for n, p in ds.get("properties", {}).items() if p.get("type") == "title"), "Name")
            page = await notion.pages.create(parent={"type": "data_source_id", "data_source_id": ds_id},
                                             properties={title_prop: {"title": _rt(title)}}, children=children)
        else:
            page = await notion.pages.create(parent={"page_id": pid},
                                             properties={"title": {"title": _rt(title)}}, children=children)
        return f"Created page '{title}'. ID: {page['id']} | {page.get('url', '')}"
    except Exception as e:
        return _err(e, "creating page")


@mcp.tool()
async def update_page_title(page_id: str, new_title: str) -> str:
    """Rename a Notion page (works for plain pages; database rows keep their other properties)."""
    try:
        notion = _client()
        pid = _id(page_id)
        page = await notion.pages.retrieve(page_id=pid)
        title_prop = next((n for n, p in page.get("properties", {}).items() if p.get("type") == "title"), "title")
        await notion.pages.update(page_id=pid, properties={title_prop: {"title": _rt(new_title)}})
        return f"Renamed page {pid} to '{new_title}'."
    except Exception as e:
        return _err(e, "renaming page")


@mcp.tool()
async def append_text_to_page(page_id: str, text_content: str) -> str:
    """
    Add content to the end of a Notion page. Lines starting with #, ##, -, 1., [ ] or >
    become headings, bullets, numbered items, to-dos or quotes; other lines are paragraphs.
    """
    try:
        notion = _client()
        blocks = blocks_from_text(text_content)
        pid = _id(page_id)
        for i in range(0, len(blocks), 100):
            await notion.blocks.children.append(block_id=pid, children=blocks[i:i + 100])
        return f"Added {len(blocks)} block(s) to page {pid}."
    except Exception as e:
        return _err(e, "appending to page")


@mcp.tool()
async def delete_page(page_id: str) -> str:
    """Move a Notion page to the trash (restorable from Notion's Trash)."""
    try:
        notion = _client()
        pid = _id(page_id)
        await notion.pages.update(page_id=pid, in_trash=True)
        return f"Moved page {pid} to the trash."
    except Exception as e:
        return _err(e, "deleting page")


@mcp.tool()
async def query_database(database_id: str, text: str = "", max_results: int = 20) -> str:
    """
    List rows of a Notion database (database ID, data source ID or URL). Optional `text`
    keeps only rows whose properties contain it (case-insensitive). Shows each row's ID and properties.
    """
    try:
        notion = _client()
        ds_id = await _data_source_id(notion, _id(database_id))
        res = await notion.data_sources.query(data_source_id=ds_id, page_size=100)
        rows = []
        needle = text.lower().strip()
        for r in res.get("results", []):
            props = {n: _prop_text(p) for n, p in r.get("properties", {}).items()}
            line = " | ".join(f"{n}: {v}" for n, v in props.items() if v)
            if needle and needle not in line.lower():
                continue
            rows.append(f"ID: {r['id']} | {line}")
            if len(rows) >= max(1, min(max_results, 100)):
                break
        return "\n".join(rows) if rows else "No matching rows."
    except Exception as e:
        return _err(e, "querying database")
