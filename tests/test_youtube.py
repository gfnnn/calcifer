"""YouTube channel-uploads feed source (offline). The Atom parser is pure, so
it's exercised directly against a synthetic feed; the network fetch is checked
only on its no-channels short-circuit."""

from __future__ import annotations

import dataclasses

import aiohttp

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


class _FakeResp:
    """A one-shot aiohttp-style response context manager."""

    def __init__(self, *, xml=None, error=None):
        self._xml = xml
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    async def text(self):
        return self._xml


class _FakeSession:
    def __init__(self, by_channel):
        self._by_channel = by_channel

    def get(self, url):
        return self._by_channel[url.split("channel_id=")[-1]]


async def test_youtube_fetch_isolates_a_bad_channel():
    # A 404 on one channel must not block a healthy channel's uploads. The bad
    # id is listed first to prove the loop continues past the failure.
    session = _FakeSession(
        {
            "UCbad": _FakeResp(error=aiohttp.ClientError("404 Not Found")),
            "UCgood": _FakeResp(xml=_YT_ATOM),
        }
    )
    src = YouTubeSource(channel_ids=("UCbad", "UCgood"))
    events = await src.fetch(session)
    assert [e.external_id for e in events] == ["yt:ABC123", "yt:DEF456"]


def test_build_sources_includes_youtube_when_configured():
    cfg = dataclasses.replace(
        Config(),
        wowhead_enabled=False,
        blizzard_news_enabled=False,
        youtube_feed_enabled=True,
        youtube_channel_ids=("UC123",),
    )
    assert [s.name for s in build_sources(cfg)] == ["youtube"]
