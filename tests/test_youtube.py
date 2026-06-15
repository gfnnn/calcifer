"""YouTube channel-uploads feed source (offline). The Atom parser is pure, so
it's exercised directly against a synthetic feed; the network fetch is checked
only on its no-channels short-circuit."""

from __future__ import annotations

import dataclasses

from ftabot.config import Config
from ftabot.feeds.runner import build_sources
from ftabot.feeds.youtube import YouTubeSource, channel_url, parse_atom

_YT_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>From the Ashes</title>
  <entry>
    <id>yt:video:ABC123</id>
    <yt:videoId>ABC123</yt:videoId>
    <title>Mythic Boss Kill!</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=ABC123"/>
    <author><name>From the Ashes</name></author>
    <published>2026-06-10T19:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/ABC123/hq.jpg"/>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>DEF456</yt:videoId>
    <title>Raid Recap</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=DEF456"/>
    <author><name>From the Ashes</name></author>
    <published>2026-06-09T19:00:00+00:00</published>
  </entry>
</feed>"""


def test_youtube_parses_entries():
    events = parse_atom(_YT_ATOM)
    assert len(events) == 2
    first = events[0]
    assert first.title == "Mythic Boss Kill!"
    assert first.event_type == "youtube.video"
    assert first.external_id == "yt:ABC123"
    assert first.url == "https://www.youtube.com/watch?v=ABC123"
    assert first.image_url.endswith("hq.jpg")
    assert first.summary == "New video from From the Ashes."
    assert first.authored is False  # video title relayed verbatim
    assert first.extra["channel"] == "From the Ashes"


def test_youtube_parse_handles_garbage():
    assert parse_atom("<nope>") == []


def test_youtube_channel_url():
    assert channel_url("UC123") == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
    )


async def test_youtube_source_no_channels_returns_empty():
    src = YouTubeSource(Config())  # no channel ids configured
    assert await src.fetch(session=None) == []


def test_build_sources_includes_youtube_when_configured():
    cfg = dataclasses.replace(
        Config(),
        wowhead_enabled=False,
        blizzard_news_enabled=False,
        youtube_feed_enabled=True,
        youtube_channel_ids=("UC123",),
    )
    assert [s.name for s in build_sources(cfg)] == ["youtube"]
