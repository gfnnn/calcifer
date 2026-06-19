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
    # The guild's priority split: blue tracker, live patch notes and general WoW
    # news lead the page; PTR coverage and other Blizzard games sit lower; the
    # classic-games sections aren't on the page at all.
    assert news_tier("wowhead.blue-post") == "high"
    assert news_tier("wowhead.patch-notes") == "high"
    assert news_tier("wowhead.news") == "high"
    assert news_tier("wowhead.ptr") == "low"
    assert news_tier("wowhead.blizzard") == "low"
    assert news_tier("wowhead.classic") is None
    assert news_tier(None) == "low"
    assert news_tier("somebot.unknown") == "low"  # unknown provenance never leads


def test_classify_uses_the_rss_section_category():
    # Real <category> values from wowhead.com/news/rss/all: the section is the
    # authoritative signal, since titles often omit it.
    from ftabot.feeds.wowhead import _classify

    assert _classify("Mythic L'ura Macros and Positioning Assignments", ["Live"]) == "wowhead.news"
    assert _classify("Blizzard Releases New Youtooz and Funko Pop! Figures", ["WoW"]) == "wowhead.news"
    assert _classify(
        "PvP World Quest Appears Again - Patch 12.0.5 Hotfixes for June 9th", ["Live"]
    ) == "wowhead.patch-notes"
    # PTR items frequently don't say "PTR" in the title; the category catches them.
    assert _classify(
        "New Models for Darkspear Dash Microholiday on Patch 12.0.7", ["PTR"]
    ) == "wowhead.ptr"
    assert _classify(
        "Brian Holinka Joins Riot Games' MMORPG Team", ["Blizzard"]
    ) == "wowhead.blizzard"
    # A recognised retail section outranks stray keywords in the title.
    assert _classify(
        "WoW Weekly: Midnight, Mists of Pandaria Classic, Turbulent Timeways, and More!",
        ["WoW"],
    ) == "wowhead.news"
    # The classic-games sections are their own bucket (kept off the news board).
    assert _classify("Blizzard Has a Plan to Take Care of Casino Bots", ["Classic"]) == "wowhead.classic"
    assert _classify("Numen Finishes Siege of Orgrimmar in 2:17", ["Mists"]) == "wowhead.classic"
    assert _classify("Blizzard Adds Visible Arena MMR to UI", ["TBC"]) == "wowhead.classic"


def test_classify_follows_the_priority_mapping():
    from ftabot.feeds.wowhead import _classify

    # High priority: blue tracker, live patch notes/hotfixes, general WoW news.
    assert _classify("Blue Post: Class Tuning Incoming", []) == "wowhead.blue-post"
    assert _classify("Tuning Incoming", ["Blue Tracker"]) == "wowhead.blue-post"
    assert _classify("Patch 12.0.7 Hotfixes for June 10th", []) == "wowhead.patch-notes"
    assert _classify("Midnight Raid Finder Schedule", []) == "wowhead.news"

    # Low priority: test-realm coverage and other-Blizzard-game news.
    assert _classify("Patch 12.0.7 PTR Development Notes", []) == "wowhead.ptr"
    assert _classify("New Beta Build Deployed", []) == "wowhead.ptr"
    assert _classify("Diablo 4 Season Announced", []) == "wowhead.blizzard"
    assert _classify("Big Reveal", ["Hearthstone"]) == "wowhead.blizzard"

    # A blue post about the PTR is still a blue post (order matters), and
    # "ptr" only matches as a word, never inside another one.
    assert _classify("Blue Post: PTR Plans", []) == "wowhead.blue-post"
    assert _classify("The Sceptre of Storms Returns", []) == "wowhead.news"
    # Ordinary WoW news mentioning Blizzard the company stays high priority.
    assert _classify("Blizzard Confirms Raid Release Date", []) == "wowhead.news"


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
        _item("x1", "PTR build notes", tier="low", event_type="wowhead.ptr",
              published_at="2026-06-03T00:00:00+00:00"),
    ])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    assert payload is not None
    desc = payload.embed.description
    assert "Top stories" in desc and "PTR & Blizzard" in desc
    # the high-priority zone sits above the low-priority one
    assert desc.index("Top stories") < desc.index("PTR & Blizzard")
    # masked links present
    assert "[Patch 11.2 Notes](https://wowhead.com/p1)" in desc
    # a relative time stamp is rendered
    assert "<t:" in desc


async def test_board_zone_sizes_favour_high_priority(db):
    # 16 high + 9 low archived; the defaults cap the board at 12 up top and 6
    # below, so the zones fill but stay bounded.
    items = [
        _item(f"h{i:02d}", f"Big story {i:02d}", tier="high", event_type="wowhead.news",
              published_at=f"2026-06-{1 + i:02d}T00:00:00+00:00")
        for i in range(16)
    ] + [
        _item(f"l{i:02d}", f"PTR scrap {i:02d}", tier="low", event_type="wowhead.ptr",
              published_at=f"2026-05-{1 + i:02d}T00:00:00+00:00")
        for i in range(9)
    ]
    await db.record_news(items, keep=60)
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    desc = payload.embed.description
    assert sum(1 for i in range(16) if f"Big story {i:02d}" in desc) == 12
    assert sum(1 for i in range(9) if f"PTR scrap {i:02d}" in desc) == 6
    # ...and a board config can resize either zone.
    payload = await NewsHighlightsBoard().render(
        db, Config(), {"highlights": 2, "latest": 1}
    )
    desc = payload.embed.description
    assert sum(1 for i in range(16) if f"Big story {i:02d}" in desc) == 2
    assert sum(1 for i in range(9) if f"PTR scrap {i:02d}" in desc) == 1


async def test_board_renders_with_only_one_zone_populated(db):
    await db.record_news([_item("solo", "PTR only", tier="low",
                                event_type="wowhead.ptr")])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    desc = payload.embed.description
    assert "PTR & Blizzard" in desc and "Top stories" not in desc


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


async def test_board_renders_naive_and_unparseable_timestamps(db):
    # A naive (no-tz) published_at is assumed UTC and still gets a relative
    # stamp; an unparseable one yields no stamp but the headline still renders.
    await db.record_news([
        _item("naive", "Naive ts", tier="high", event_type="wowhead.news",
              published_at="2026-06-05T00:00:00"),  # no tzinfo -> _epoch line 59
        _item("garbage", "Garbage ts", tier="high", event_type="wowhead.news",
              published_at="not-a-timestamp"),  # ValueError -> _epoch lines 56-57
    ])
    payload = await NewsHighlightsBoard().render(db, Config(), {})
    desc = payload.embed.description
    assert "Naive ts" in desc and "<t:" in desc  # naive ts produced an epoch
    assert "Garbage ts" in desc  # bad ts: headline still shown, just no stamp


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
  <item>
    <title>Siege of Orgrimmar Guide Compendium</title>
    <link>https://www.wowhead.com/news/mists-1</link>
    <guid>mists-1</guid>
    <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
    <category>Mists</category>
  </item>
</channel></rss>
"""


async def test_wowhead_source_archives_on_fetch(db):
    cfg = dataclasses.replace(Config(), database_path=db.path)
    source = WowheadSource(cfg)
    events = await source.fetch(_FakeSession(_RSS))
    assert len(events) == 3  # still returns every event for the feed pipeline

    # The classic-section item is never archived, so it can't crowd the board.
    archived = await db.recent_news(limit=10)
    assert {i["external_id"] for i in archived} == {"ptr-1", "misc-1"}
    # general WoW news leads the page; the PTR item sits in the lower zone
    high = await db.recent_news(limit=10, tier="high")
    assert [i["external_id"] for i in high] == ["misc-1"]
    low = await db.recent_news(limit=10, tier="low")
    assert [i["external_id"] for i in low] == ["ptr-1"]


def test_wowhead_source_without_config_is_pure_url():
    # The bare-URL form still works and never touches a database.
    source = WowheadSource(url="https://example.com/rss")
    assert source.url == "https://example.com/rss"
    assert source.config is None
    # parse_rss remains the pure, independently tested core.
    assert parse_rss("not xml") == []


async def test_archive_is_a_noop_without_config_or_events(db):
    # No config => nowhere to persist; no events => nothing to persist. Either
    # way _archive returns early without touching a DB (and never crashes).
    from ftabot.feeds.base import FeedEvent

    no_config = WowheadSource(url="https://x/rss")
    assert no_config.config is None
    # A real event, but no config: returns early, nothing to assert beyond "no crash".
    assert await no_config._archive(
        [FeedEvent(source="wowhead", event_type="wowhead.news", external_id="z", title="T")]
    ) is None

    # A config but an empty event list: the DB is left untouched.
    cfg = dataclasses.replace(Config(), database_path=db.path)
    await WowheadSource(cfg)._archive([])
    assert await db.recent_news(limit=10) == []  # nothing archived


_CLASSIC_ONLY_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Siege of Orgrimmar Speedrun</title>
    <link>https://www.wowhead.com/news/classic-1</link>
    <guid>classic-1</guid>
    <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
    <category>Mists</category>
  </item>
</channel></rss>
"""


async def test_archive_skips_when_every_item_is_off_board(db):
    # A feed of only classic-section items (tier None) archives nothing: the
    # `if not rows` guard short-circuits before opening the database. The events
    # are still returned for the alert pipeline.
    cfg = dataclasses.replace(Config(), database_path=db.path)
    events = await WowheadSource(cfg).fetch(_FakeSession(_CLASSIC_ONLY_RSS))
    assert len(events) == 1  # still flows to the router
    assert await db.recent_news(limit=10) == []  # but nothing archived
