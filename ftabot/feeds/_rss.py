"""Shared, pure RSS parsing helpers for the news feed sources.

Both the Wowhead source and the generic :mod:`ftabot.feeds.rss_news` source read
the same RSS shape (``<item>`` with title/link/guid/category/description/pubDate),
so the low-level extraction lives here once. Pure and offline-testable: no I/O,
no source-specific classification (that stays in each source).
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(html: str | None, *, limit: int = 280) -> str | None:
    """Flatten an RSS ``<description>`` to a trimmed, tag-free summary line."""
    if not html:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub("", html)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_pubdate(pub_date: str | None) -> str | None:
    """Convert an RFC-822 ``pubDate`` to ISO-8601, or ``None`` on a bad value."""
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def iter_rss_items(xml: str) -> list[dict]:
    """Extract the raw ``<item>`` records from an RSS document.

    Returns a list of ``{title, link, guid, categories, description, pubdate}``
    dicts (skipping items with no title), or ``[]`` on a parse error so a
    malformed feed never crashes a poll.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip() or None
        guid = (item.findtext("guid") or "").strip() or link or title
        categories = [
            (c.text or "").strip()
            for c in item.findall("category")
            if (c.text or "").strip()
        ]
        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "categories": categories,
                "description": item.findtext("description"),
                "pubdate": item.findtext("pubDate"),
            }
        )
    return items
