"""Wowhead news RSS source — blue posts, patch notes, and general WoW news.

Wowhead exposes RSS for its news; we poll it ourselves (rather than using their
direct-to-Discord webhook) so Calcifer can reformat, brand and route each item.
This replaces the role PatchBot played.
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import aiohttp

from .base import FeedEvent, FeedSource

DEFAULT_RSS_URL = "https://www.wowhead.com/news/rss/all"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _classify(title: str, categories: list[str]) -> str:
    """Map a Wowhead item onto an event type used for routing/branding."""
    haystack = " ".join([title, *categories]).lower()
    if "patch notes" in haystack or "ptr" in haystack:
        return "wowhead.patch-notes"
    if "blue post" in haystack or "blue tracker" in haystack:
        return "wowhead.blue-post"
    return "wowhead.news"


# Event types that earn the News Highlights board's top "highlight" zone — the
# things worth surfacing (patch notes, blue posts, PTR). Everything else is the
# low-tier latest-list filler (design-system §4c).
_HIGH_TIER_TYPES = {"wowhead.patch-notes", "wowhead.blue-post"}


def news_tier(event_type: str | None) -> str:
    """'high' for board-highlight news, 'low' for the latest-list filler."""
    return "high" if event_type in _HIGH_TIER_TYPES else "low"


def _clean_summary(html: str | None, *, limit: int = 280) -> str | None:
    if not html:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub("", html)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _to_iso(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(xml: str) -> list[FeedEvent]:
    """Parse a Wowhead RSS document into FeedEvents (pure, offline-testable)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    events: list[FeedEvent] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip() or None
        guid = (item.findtext("guid") or "").strip() or link or title
        categories = [
            (c.text or "").strip() for c in item.findall("category") if (c.text or "").strip()
        ]
        events.append(
            FeedEvent(
                source="wowhead",
                event_type=_classify(title, categories),
                external_id=guid,
                title=title,
                url=link,
                summary=_clean_summary(item.findtext("description")),
                categories=categories,
                published_at=_to_iso(item.findtext("pubDate")),
                # Wowhead headlines/descriptions are relayed verbatim, so the
                # publisher must not rewrite their punctuation.
                authored=False,
            )
        )
    return events


class WowheadSource(FeedSource):
    name = "wowhead"

    def __init__(self, config=None, *, url: str | None = None):
        # Accept a Config (so fetch can archive news for the board) or a bare URL
        # (kept for ad-hoc/manual use). parse_rss stays pure and the only thing
        # the offline tests need.
        self.config = config
        self.url = url or getattr(config, "wowhead_rss_url", None) or DEFAULT_RSS_URL

    async def fetch(self, session: aiohttp.ClientSession) -> list[FeedEvent]:
        async with session.get(self.url) as resp:
            resp.raise_for_status()
            xml = await resp.text()
        events = parse_rss(xml)
        await self._archive(events)
        return events

    async def _archive(self, events: list[FeedEvent]) -> None:
        """Persist parsed items into the news archive the board renders from.

        Best-effort and decoupled from posting: it runs on every poll (including
        the silent first prime), so the News Highlights board has data from the
        very first fetch. No-op without a Config to locate the database.
        """
        if self.config is None or not events:
            return
        from ..db import Database

        rows = [
            {
                "source": e.source,
                "external_id": e.external_id,
                "event_type": e.event_type,
                "title": e.title,
                "url": e.url,
                "summary": e.summary,
                "tier": news_tier(e.event_type),
                "published_at": e.published_at,
            }
            for e in events
        ]
        async with Database(self.config.database_path) as db:
            await db.record_news(rows)
