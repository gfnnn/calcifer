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


# Wowhead's RSS marks each item with a <category> naming its section (verified
# against the live /news/rss/all feed): "WoW" and "Live" are retail news, "PTR"
# is test-realm coverage, "Blizzard" is company/industry news, and the classic
# games run under "Classic"/"Mists"/"TBC" etc. The category is the authoritative
# signal — titles often omit the section (a PTR datamining post rarely says
# "PTR") — so classification is category-first, keyword fallback.
_RETAIL_SECTIONS = {"wow", "live"}
_PTR_SECTIONS = {"ptr", "beta", "alpha"}
_BLIZZARD_SECTIONS = {"blizzard"}
_CLASSIC_SECTIONS = {
    "classic", "mists", "tbc", "wrath", "cata", "sod",
    "season of discovery", "hardcore", "era", "anniversary",
}

# Keyword fallbacks for items without a recognisable section category.
# Test-realm markers are word-bounded so e.g. "sceptre" can't match "ptr";
# other Blizzard franchises are matched as names (never the bare word
# "blizzard", which appears in ordinary WoW news).
_TEST_REALM_RE = re.compile(r"\b(ptr|beta|alpha)\b")
_OTHER_GAMES = (
    "diablo",
    "overwatch",
    "hearthstone",
    "warcraft rumble",
    "starcraft",
    "heroes of the storm",
)


def _classify(title: str, categories: list[str]) -> str:
    """Map a Wowhead item onto an event type used for routing/branding/tiering.

    Order matters: a blue post stays a blue post even when it discusses the
    PTR, and a recognised section category outranks any stray keyword in the
    title (a "WoW Weekly" roundup mentioning Mists Classic is still WoW news).
    """
    haystack = " ".join([title, *categories]).lower()
    if "blue post" in haystack or "blue tracker" in haystack:
        return "wowhead.blue-post"

    sections = {c.strip().lower() for c in categories if c.strip()}
    if sections & _PTR_SECTIONS:
        return "wowhead.ptr"
    if sections & _CLASSIC_SECTIONS:
        return "wowhead.classic"
    if sections & _BLIZZARD_SECTIONS:
        return "wowhead.blizzard"
    if not sections & _RETAIL_SECTIONS:
        # No recognised section: fall back to keywords (covers feeds without
        # categories and any section names Wowhead adds later).
        if _TEST_REALM_RE.search(haystack):
            return "wowhead.ptr"
        if any(game in haystack for game in _OTHER_GAMES):
            return "wowhead.blizzard"
    if "patch notes" in haystack or "hotfix" in haystack:
        return "wowhead.patch-notes"
    return "wowhead.news"


# The guild's priority split for the News board's two zones: retail blue-tracker
# posts, live patch notes/hotfixes and general WoW news lead the page (high);
# PTR/beta coverage and other-Blizzard-game news sit in the smaller lower zone.
# Classic-section items (Mists/TBC/era …) match none of the guild's subscribed
# feeds, so they are left off the board entirely (tier None = not archived).
_LOW_TIER_TYPES = {"wowhead.ptr", "wowhead.blizzard"}


def news_tier(event_type: str | None) -> str | None:
    """'high' for the board's top zone, 'low' for the PTR/Blizzard lower zone,
    ``None`` for items the board doesn't cover (the classic-games sections)."""
    if not event_type or not event_type.startswith("wowhead."):
        return "low"  # unknown provenance never leads the page
    if event_type == "wowhead.classic":
        return None
    return "low" if event_type in _LOW_TIER_TYPES else "high"


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
            # tier None = a section the board doesn't cover (classic games);
            # the event still flows to the alert router like any other.
            if news_tier(e.event_type) is not None
        ]
        if not rows:
            return
        async with Database(self.config.database_path) as db:
            await db.record_news(rows)
