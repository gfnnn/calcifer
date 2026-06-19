"""WoWProgress ranking integration (offline).

WoWProgress has no RSS; this covers the JSON-rank client's pure helpers
(parse_rank, path/url building) and the feed source's pure event builder +
config-driven wiring. The network ``guild_rank`` call is exercised only on its
non-200 / bad-JSON short-circuits via a fake session.
"""

from __future__ import annotations

import dataclasses

from ftabot.config import Config
from ftabot.feeds.runner import build_sources
from ftabot.feeds.wowprogress import WowProgressSource, _rank_event
from ftabot.services.wowprogress import (
    ATTRIBUTION,
    WowProgressClient,
    guild_path,
    guild_url,
    parse_rank,
)

# --- service: parse_rank ----------------------------------------------------


def test_parse_rank_coerces_ints():
    out = parse_rank(
        {"score": "40817", "world_rank": "1", "area_rank": "1", "realm_rank": "2"}
    )
    assert out == {"score": 40817, "world_rank": 1, "area_rank": 1, "realm_rank": 2}


def test_parse_rank_marks_missing_as_negative():
    out = parse_rank({"world_rank": "5"})  # others absent
    assert out["world_rank"] == 5
    assert out["realm_rank"] == -1


def test_parse_rank_none_when_empty_or_garbage():
    assert parse_rank({}) is None  # unranked guild returns {} -> nothing to report
    assert parse_rank(None) is None
    assert parse_rank("not a dict") is None
    assert parse_rank({"score": "abc"}) is None  # all fields uncoercible


# --- service: url building --------------------------------------------------


def test_guild_path_slugs_realm_and_encodes_name():
    path = guild_path("EU", "Hellfire", "From the Ashes")
    assert path == "/guild/eu/hellfire/From%20the%20Ashes"


def test_guild_path_with_rating():
    path = guild_path("us", "sen-jin", "Premonition", rating="tier8.ach")
    assert path == "/guild/us/sen-jin/Premonition/rating.tier8.ach"


def test_guild_url_is_a_wowprogress_link():
    # The attribution requirement: every surface links back to wowprogress.com.
    assert guild_url("eu", "hellfire", "From the Ashes").startswith(
        "https://www.wowprogress.com/guild/eu/hellfire/"
    )


# --- service: client short-circuits -----------------------------------------


class _Resp:
    def __init__(self, status=200, payload=None, boom=False):
        self.status = status
        self._payload = payload
        self._boom = boom

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if self._boom:
            raise ValueError("not json")
        return self._payload


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def get(self, url):
        return self._resp


async def test_client_returns_none_on_non_200():
    client = WowProgressClient(session=_Session(_Resp(status=403)))
    assert await client.guild_rank("eu", "hellfire", "From the Ashes") is None


async def test_client_returns_none_on_bad_json():
    client = WowProgressClient(session=_Session(_Resp(status=200, boom=True)))
    assert await client.guild_rank("eu", "hellfire", "From the Ashes") is None


async def test_client_parses_a_rank():
    payload = {"score": "100", "world_rank": "3", "area_rank": "2", "realm_rank": "1"}
    client = WowProgressClient(session=_Session(_Resp(status=200, payload=payload)))
    rank = await client.guild_rank("eu", "hellfire", "From the Ashes")
    assert rank["world_rank"] == 3 and rank["realm_rank"] == 1


def test_session_headers_send_a_real_user_agent():
    # Cloudflare 403s default fetchers, so a client-owned session sends a real
    # User-Agent (and accepts JSON).
    headers = WowProgressClient()._session_headers()
    assert headers["Accept"] == "application/json"
    assert "ftabot" in headers["User-Agent"]


# --- feed: event builder ----------------------------------------------------


def test_rank_event_carries_attribution_and_link():
    event = _rank_event(
        "eu", "hellfire", "From the Ashes",
        {"world_rank": 120, "realm_rank": 1, "area_rank": 12, "score": 4000},
    )
    assert event.source == "wowprogress"
    assert event.event_type == "wowprogress.rank"
    assert "world #120" in event.title and "realm #1" in event.title
    assert ATTRIBUTION in event.summary  # required attribution in the message
    assert event.url.startswith("https://www.wowprogress.com/")
    # The dedup key encodes the standing, so each new rank fires exactly once.
    assert event.external_id.endswith("-w120-r1")


def test_rank_event_handles_unranked_fields():
    event = _rank_event(
        "eu", "hellfire", "From the Ashes",
        {"world_rank": -1, "realm_rank": 4, "area_rank": -1, "score": -1},
    )
    assert "world unranked" in event.title
    assert "Score" not in event.summary  # no score line when score is missing


# --- feed: source wiring ----------------------------------------------------


async def test_source_returns_empty_when_unranked(monkeypatch):
    from ftabot.feeds import wowprogress as wp_mod

    class _NoRankClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def guild_rank(self, *a, **k):
            return None

    monkeypatch.setattr(wp_mod, "WowProgressClient", _NoRankClient)
    assert await WowProgressSource(Config()).fetch(session=None) == []


def test_build_sources_includes_wowprogress_when_enabled():
    cfg = dataclasses.replace(
        Config(), wowhead_enabled=False, blizzard_news_enabled=False,
        wowprogress_feed_enabled=True,
    )
    assert [s.name for s in build_sources(cfg)] == ["wowprogress"]
