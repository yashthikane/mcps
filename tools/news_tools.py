# tools/news_tools.py — Headlines from Google News RSS (free, no key, no account)

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

from mcp_instance import mcp

FEED = "https://news.google.com/rss"
MAX_TOPICS, MAX_PER_TOPIC = 6, 10


def _ago(when: datetime | None) -> str:
    if not when:
        return ""
    hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return f"{max(1, round(hours * 60))} min ago" if hours < 1 else f"{round(hours)} h ago" if hours < 48 else f"{round(hours / 24)} days ago"


def parse(xml: bytes, limit: int) -> list[dict]:
    """RSS items → [{title, source, link, published}] (newest first)."""
    if b"<!DOCTYPE" in xml[:500] or b"<!ENTITY" in xml:  # an RSS feed never needs these
        raise ValueError("unexpected feed content")
    out = []
    for item in ET.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        if title:
            out.append({"title": title, "source": source, "link": (item.findtext("link") or "").strip(), "published": when})
    out.sort(key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out[:limit]


async def _fetch(client: httpx.AsyncClient, topic: str, region: str, days: int) -> list[dict]:
    loc = f"hl=en-{region}&gl={region}&ceid={region}:en"
    url = f"{FEED}/search?q={quote_plus(f'{topic} when:{days}d')}&{loc}" if topic else f"{FEED}?{loc}"
    r = await client.get(url)
    r.raise_for_status()
    return parse(r.content, MAX_PER_TOPIC * 2)


@mcp.tool()
async def get_news(topics: str = "", per_topic: int = 5, region: str = "IN", days: int = 1, with_links: bool = False) -> str:
    """
    Latest news headlines from Google News, grouped by topic.
    - topics: comma-separated, e.g. "AI, Indian startups, edtech". Empty = top headlines.
    - per_topic: headlines per topic (1-10). region: country code, e.g. IN, US, GB.
    - days: only stories from the last N days (1-30). with_links: add each story's link (longer output).
    Each line: headline (source, age). Summarise from these; don't invent stories.
    """
    names = [t.strip() for t in topics.split(",") if t.strip()][:MAX_TOPICS] or [""]
    per_topic = max(1, min(MAX_PER_TOPIC, int(per_topic or 5)))
    days = max(1, min(30, int(days or 1)))
    region = re.sub(r"[^A-Z]", "", (region or "IN").upper())[:2] or "IN"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Donna news reader)"}) as c:
            results = await asyncio.gather(*(_fetch(c, t, region, days) for t in names), return_exceptions=True)
    except httpx.HTTPError:
        return "Error: can't reach Google News. Check the internet connection."
    lines, seen = [], set()
    for name, items in zip(names, results):
        lines.append(f"## {name or 'Top headlines'}")
        if isinstance(items, Exception):
            lines.append("(couldn't load this topic)")
            continue
        picked = [i for i in items if i["title"].lower() not in seen][:per_topic]
        seen.update(i["title"].lower() for i in picked)
        if not picked:
            lines.append("(no stories found)")
        for i in picked:
            meta = ", ".join(x for x in (i["source"], _ago(i["published"])) if x)
            lines.append(f"- {i['title']}" + (f" ({meta})" if meta else "") + (f" {i['link']}" if with_links and i["link"] else ""))
    if all(isinstance(r, Exception) for r in results):
        return "Error: Google News didn't answer. Try again in a minute."
    return "\n".join(lines)
