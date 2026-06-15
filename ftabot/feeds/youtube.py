"""YouTube channel uploads (Atom) — surface new videos in Discord.

YouTube publishes a keyless Atom feed per channel at
``/feeds/videos.xml?channel_id=<ID>``. Point it at the guild's own channel (raid
kill VODs, recruitment pitches) and/or a couple of theorycrafters, and Calcifer
posts each new upload. One source polls every configured channel and concatenates
the results; the video *title* is relayed verbatim (``authored=False``).

Finding a channel id: on the channel page, View Source and search for
``"channelId"`` (or use the channel's RSS link). It looks like ``UC...``.
"""

from __future__ import annotations

import asyncio
import logging
from xml.etree import ElementTree

import aiohttp

from .base import FeedEvent, FeedSource

log = logging.getLogger("ftabot.feeds")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# Atom + YouTube + Media RSS namespaces used in the channel feed.
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def channel_url(channel_id: str) -> str:
    return FEED_URL.format(channel_id=channel_id)


def _entry_link(entry) -> str | None:
    link = entry.find("a:link", _NS)
    return link.get("href") if link is not None else None


def parse_atom(xml: str) -> list[FeedEvent]:
    """Parse a YouTube channel Atom feed into FeedEvents (pure, offline-testable)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    events: list[FeedEvent] = []
    for entry in root.findall("a:entry", _NS):
        title = (entry.findtext("a:title", namespaces=_NS) or "").strip()
        video_id = (entry.findtext("yt:videoId", namespaces=_NS) or "").strip()
        link = _entry_link(entry)
        if not title or not (video_id or link):
            continue
        author = (entry.findtext("a:author/a:name", namespaces=_NS) or "").strip()
        published = (entry.findtext("a:published", namespaces=_NS) or "").strip() or None
        thumb_el = entry.find("media:group/media:thumbnail", _NS)
        thumb = thumb_el.get("url") if thumb_el is not None else None

        events.append(
            FeedEvent(
                source="youtube",
                event_type="youtube.video",
                external_id=f"yt:{video_id}" if video_id else (link or title),
                title=title,
                url=link or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
                # Calcifer's own framing (no em dashes); the title above is relayed.
                summary=f"New video from {author}." if author else "New video.",
                categories=["YouTube"],
                published_at=published,
                image_url=thumb,
                # The video title is the creator's text, relayed verbatim.
                authored=False,
                extra={"channel": author, "video_id": video_id},
            )
        )
    return events


class YouTubeSource(FeedSource):
    name = "youtube"

    def __init__(self, config=None, *, channel_ids=None):
        self.config = config
        self.channel_ids = tuple(
            channel_ids
            if channel_ids is not None
            else getattr(config, "youtube_channel_ids", ()) or ()
        )

    async def fetch(self, session: aiohttp.ClientSession) -> list[FeedEvent]:
        events: list[FeedEvent] = []
        for channel_id in self.channel_ids:
            try:
                async with session.get(channel_url(channel_id)) as resp:
                    resp.raise_for_status()
                    xml = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # Isolate per channel: one bad id (404/5xx/timeout) must not sink
                # the others. The runner only catches at the whole-source level,
                # so without this a single typo'd channel silently blocks every
                # configured channel's uploads on every poll.
                log.warning("youtube channel %s fetch failed: %s", channel_id, exc)
                continue
            events.extend(parse_atom(xml))
        return events
