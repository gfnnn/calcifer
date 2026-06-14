"""Generic RSS news source — archive any external WoW news feed for the board.

The News Highlights board renders from the ``news_items`` archive; Wowhead used
to be the only thing writing to it. This generic source lets any RSS feed (the
default wiring adds Blizzard's official news) flow into the same archive, so the
board simply shows more. Each instance is a fixed ``(name, url, event_type,
tier)``; the parsing is the shared, pure RSS reader so it stays offline-testable.

Adding another feed is one line in ``runner.build_sources`` (or a new config
entry) — no new parser, no new board code.
"""

from __future__ import annotations

import aiohttp

from ._rss import iter_rss_items, parse_pubdate, strip_html
from .base import FeedEvent, FeedSource


def parse_news_rss(xml: str, *, source: str, event_type: str) -> list[FeedEvent]:
    """Parse a generic news RSS document into relayed FeedEvents (pure)."""
    events: list[FeedEvent] = []
    for item in iter_rss_items(xml):
        events.append(
            FeedEvent(
                source=source,
                event_type=event_type,
                external_id=item["guid"],
                title=item["title"],
                url=item["link"],
                summary=strip_html(item["description"]),
                categories=item["categories"],
                published_at=parse_pubdate(item["pubdate"]),
                # Relayed third-party headlines: shown verbatim, not rewritten.
                authored=False,
            )
        )
    return events


class RssNewsSource(FeedSource):
    """A configurable RSS feed that archives into the News board's ``news_items``.

    ``tier`` is fixed per source ("high" leads the board, "low" sits in the
    lower zone), so a curated feed lands where the guild wants it without the
    per-item classification Wowhead needs.
    """

    def __init__(
        self,
        config=None,
        *,
        name: str,
        url: str,
        event_type: str,
        tier: str = "high",
    ):
        self.config = config
        self.name = name
        self.url = url
        self.event_type = event_type
        self.tier = tier

    async def fetch(self, session: aiohttp.ClientSession) -> list[FeedEvent]:
        async with session.get(self.url) as resp:
            resp.raise_for_status()
            xml = await resp.text()
        events = parse_news_rss(xml, source=self.name, event_type=self.event_type)
        await self._archive(events)
        return events

    async def _archive(self, events: list[FeedEvent]) -> None:
        """Persist items into the news archive the board renders from.

        Best-effort and decoupled from posting (runs on every poll, including
        the silent first prime), so the board has data from the first fetch.
        No-op without a Config to locate the database.
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
                "tier": self.tier,
                "published_at": e.published_at,
            }
            for e in events
        ]
        async with Database(self.config.database_path) as db:
            await db.record_news(rows)
