"""News archive + the News Highlights living board (offline)."""

from __future__ import annotations

import dataclasses

from ftabot import brand
from ftabot.boards import REGISTRY
from ftabot.boards.news import NEWS_TYPE, NewsHighlightsBoard
from ftabot.config import Config
from ftabot.feeds.wowhead import WowheadSource, news_tier, parse_rss


def _item(ext_id, title, *, tier="low", event_type="wowhead.news", url=None, published_at=None):
    return {
        "source": "wowhead",
        "external_id": ext_id,
        "event_type": event_type,
        "title": title,
        "url": url or f"https://wowhead.com/{ext_id}",
        "summary": None,
        "tier": tier,
        "published_at": published_at,
    }


# --- tier classification ----------------------------------------------------


def test_news_tier_classifies_high_vs_low():
    assert news_tier("wowhead.patch-notes") == "high"
    assert news_tier("wowhead.blue-post") == "high"
    assert news_tier("wowhead.news") == "low"
    assert news_tier(None) == "low"


# --- DB: record_news / recent_news ------------------------------------------


async def test_record_news_inserts_and_is_idempotent(db):
    items = [_item("a", "First"), _item("b", "Second")]
    assert await db.record_news(items) == 2
    # Re-recording the same ids inserts nothing (first record wins).
    assert await db.record_news(items) == 0
    assert len(await db.recent_news(limit=10)) == 2


async def test_recent_news_orders_newest_first_and_filters_tier(db):
    await db.record_news([
        _item("old", "Old", published_at="2026-01-01T00:00:00+00:00"),
        _item("new", "New", published_at="2026-06-01T00:00:00+00:00"),
        _item("hot", "Patch", tier="high", event_type="wowhead.patch-notes",
              published_at="2026-05-01T00:00:00+00:00"),
    ])
    latest = await db.recent_news(limit=10)
    assert [i["external_id"] for i in latest] == ["new", "hot", "old"]
    high = await db.recent_news(limit=10, tier="high")
    assert [i["external_id"] for i in high] == ["hot"]


async def test_record_news_prunes_to_keep(db):
    many = [
        _item(f"n{i:03d}", f"Item {i}", published_at=f"2026-06-{i % 28 + 1:02d}T00:00:00+00:00")
        for i in range(80)
    ]
    await db.record_news(many, keep=10)
    assert len(await db.recent_news(limit=100)) == 10


# --- the board --------------------------------------------------------------


def test_news_board_is_registered():
    assert isinstance(REGISTRY.get(NEWS_TYPE), NewsHighlightsBoard)


async def test_board_posts_nothing_when_archive_empty(db):
    assert await NewsHighlightsBoard().render(db, Config(), {}) is None


async def test_board_renders_both_zones(db):
    await db.record_news([
        _item("p1", "Patch 11.2 Notes", tier="high", event_type="wowhead.patch-notes",
              published_at="2026-06-05T00:00:00+00:00"),
        _item("b1", "Blue Post: Hotfixes", tier="high", event_type="wowhead.blue-post",
              published_at="2026-06-04T00:00:00+00:00"),
        _item("n1", "A small news item", published_at="2026-06-03T00:00:00+00:00"),
    ])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    assert payload is not None
    desc = payload.embed.description
    assert "Highlights" in desc and "Latest" in desc
    # masked links present
    assert "[Patch 11.2 Notes](https://wowhead.com/p1)" in desc
    # a relative time stamp is rendered
    assert "<t:" in desc


async def test_board_preserves_relayed_headline_verbatim(db):
    # A real Wowhead headline with an em dash is shown exactly as written — we
    # never rewrite someone else's words, even though Calcifer's own copy can't.
    headline = "Patch 11.2 — The Edge of Chaos"
    await db.record_news([_item("x", headline, tier="high",
                                event_type="wowhead.patch-notes")])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    assert headline in payload.embed.description


async def test_board_own_framing_has_no_em_dash(db):
    await db.record_news([_item("n", "Clean headline")])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    # The footer + zone labels are Calcifer's own copy and obey the house rule.
    assert not brand.has_em_dash(payload.embed.footer.text)
    assert not brand.has_em_dash(payload.embed.title)


async def test_board_fingerprint_stable_until_news_changes(db):
    await db.record_news([_item("a", "A"), _item("b", "B")])
    board = NewsHighlightsBoard()
    p1 = await board.render(db, Config(), {})
    p2 = await board.render(db, Config(), {})
    assert p1.fingerprint == p2.fingerprint
    await db.record_news([_item("c", "C", published_at="2026-12-01T00:00:00+00:00")])
    p3 = await board.render(db, Config(), {})
    assert p3.fingerprint != p1.fingerprint


# --- WowheadSource archives on fetch ----------------------------------------


class _FakeResp:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, text):
        self._text = text

    def get(self, url):
        return _FakeResp(self._text)


_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Patch 12.0 PTR Notes</title>
    <link>https://www.wowhead.com/news/ptr-1</link>
    <guid>ptr-1</guid>
    <pubDate>Wed, 03 Jun 2026 10:00:00 +0000</pubDate>
    <category>News</category>
  </item>
  <item>
    <title>General news filler</title>
    <link>https://www.wowhead.com/news/misc-1</link>
    <guid>misc-1</guid>
    <pubDate>Tue, 02 Jun 2026 09:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""


async def test_wowhead_source_archives_on_fetch(db):
    cfg = dataclasses.replace(Config(), database_path=db.path)
    source = WowheadSource(cfg)
    events = await source.fetch(_FakeSession(_RSS))
    assert len(events) == 2  # still returns the events for the feed pipeline

    archived = await db.recent_news(limit=10)
    assert {i["external_id"] for i in archived} == {"ptr-1", "misc-1"}
    # the PTR item is high-tier (it reaches the highlight zone)
    high = await db.recent_news(limit=10, tier="high")
    assert [i["external_id"] for i in high] == ["ptr-1"]


def test_wowhead_source_without_config_is_pure_url():
    # The bare-URL form still works and never touches a database.
    source = WowheadSource(url="https://example.com/rss")
    assert source.url == "https://example.com/rss"
    assert source.config is None
    # parse_rss remains the pure, independently tested core.
    assert parse_rss("not xml") == []
