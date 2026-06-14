"""Generic RSS news source + the shared pure RSS helpers (offline)."""

from __future__ import annotations

from ftabot.config import Config
from ftabot.feeds._rss import iter_rss_items, parse_pubdate, strip_html
from ftabot.feeds.rss_news import RssNewsSource, parse_news_rss

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Patch 12.1 Now Live</title>
    <link>https://news.blizzard.com/a</link>
    <guid>blz-1</guid>
    <category>World of Warcraft</category>
    <description>&lt;p&gt;The update is &lt;b&gt;live&lt;/b&gt; now.&lt;/p&gt;</description>
    <pubDate>Wed, 10 Jun 2026 18:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Hotfixes Incoming</title>
    <link>https://news.blizzard.com/b</link>
  </item>
  <item><description>no title, skipped</description></item>
</channel></rss>"""


# --- shared pure helpers ----------------------------------------------------


def test_strip_html_flattens_and_truncates():
    assert strip_html("<p>Hello <b>there</b></p>") == "Hello there"
    assert strip_html(None) is None
    assert strip_html("   ") is None
    long = "<p>" + "x " * 300 + "</p>"
    out = strip_html(long, limit=40)
    assert len(out) == 40 and out.endswith("…")


def test_parse_pubdate_handles_good_and_bad():
    assert parse_pubdate("Wed, 10 Jun 2026 18:00:00 GMT").startswith("2026-06-10T18:00:00")
    assert parse_pubdate(None) is None
    assert parse_pubdate("not a date") is None


def test_iter_rss_items_tolerates_garbage():
    assert iter_rss_items("not xml at all") == []
    items = iter_rss_items(_RSS)
    assert [i["title"] for i in items] == ["Patch 12.1 Now Live", "Hotfixes Incoming"]
    # guid falls back to the link when absent.
    assert items[1]["guid"] == "https://news.blizzard.com/b"


# --- generic source parsing -------------------------------------------------


def test_parse_news_rss_relays_verbatim():
    events = parse_news_rss(_RSS, source="blizzard", event_type="blizzard.news")
    assert [e.title for e in events] == ["Patch 12.1 Now Live", "Hotfixes Incoming"]
    first = events[0]
    assert first.source == "blizzard"
    assert first.event_type == "blizzard.news"
    assert first.url == "https://news.blizzard.com/a"
    assert first.summary == "The update is live now."
    assert first.published_at.startswith("2026-06-10T18:00:00")
    assert first.authored is False  # relayed, never rewritten


# --- fetch + archive into the board's news_items ----------------------------


class _Resp:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def raise_for_status(self):
        pass

    async def text(self):
        return self._text


class _Session:
    def __init__(self, text):
        self._text = text

    def get(self, _url):
        return _Resp(self._text)


async def test_fetch_archives_with_the_source_tier(db):
    cfg = Config(database_path=db.path)
    src = RssNewsSource(
        cfg, name="blizzard", url="https://x", event_type="blizzard.news", tier="high"
    )
    events = await src.fetch(_Session(_RSS))
    assert len(events) == 2

    archived = await db.recent_news(limit=10)
    assert {a["source"] for a in archived} == {"blizzard"}
    assert all(a["tier"] == "high" for a in archived)
    # They land in the board's top zone.
    assert {a["title"] for a in await db.recent_news(limit=10, tier="high")} == {
        "Patch 12.1 Now Live",
        "Hotfixes Incoming",
    }


async def test_archive_is_a_noop_without_config_or_events():
    # No config -> nothing to persist, but fetch still returns the parsed events.
    src = RssNewsSource(None, name="x", url="u", event_type="x.news")
    await src._archive([])  # empty -> no-op, no crash
    assert await src.fetch(_Session(_RSS))
