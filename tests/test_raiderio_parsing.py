"""Parsing tests for RaiderIOClient roster/character endpoints (offline).

tests/test_raiderio.py covers the retry/backoff plumbing of ``_get``; this file
covers the mappers that turn Raider.IO JSON into Member objects and the
progression summariser.
"""

from __future__ import annotations

from ftabot.models import Member
from ftabot.services.raiderio import RaiderIOClient, _summarize_progression


class _FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self._payload = payload or {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return "error body"


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp

    async def close(self):
        pass


async def test_guild_members_maps_roster():
    payload = {
        "members": [
            {
                "rank": 0,
                "character": {
                    "name": "Ashbringer",
                    "realm": "Hellfire",
                    "region": "eu",
                    "class": "Paladin",
                    "active_spec_name": "Protection",
                    "active_spec_role": "tank",
                    "faction": "alliance",
                    "profile_url": "https://raider.io/ashbringer",
                },
            },
            {"rank": 5, "character": {}},  # nameless -> skipped
        ]
    }
    session = _FakeSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    members = await client.guild_members("From the Ashes", "hellfire", "eu")

    assert len(members) == 1
    m = members[0]
    assert m.character_name == "Ashbringer"
    assert m.realm_slug == "hellfire"
    assert m.class_name == "Paladin"
    assert m.role == "TANK"  # uppercased
    assert m.guild_rank == 0


async def test_enrich_character_fills_stats_and_progression():
    payload = {
        "class": "Mage",
        "active_spec_name": "Frost",
        "active_spec_role": "dps",
        "race": "Gnome",
        "gear": {"item_level_equipped": 638.4},
        "mythic_plus_scores_by_season": [{"scores": {"all": 2975.0}}],
        "raid_progression": {
            "nerubar-palace": {"summary": "8/8 N, 6/8 H, 2/8 M"},
        },
    }
    session = _FakeSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    member = Member("Frostine", realm_slug="hellfire", region="eu")
    await client.enrich_character(member)

    assert member.class_name == "Mage"
    assert member.role == "DPS"
    assert member.item_level == 638.4
    assert member.mythic_plus_score == 2975.0
    assert member.raid_progression == "Nerubar Palace: 8/8 N, 6/8 H, 2/8 M"


async def test_enrich_character_tolerates_404():
    session = _FakeSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    member = Member("Lowbie", realm_slug="hellfire", region="eu", item_level=120.0)
    result = await client.enrich_character(member)
    # Uncrawlable character: left exactly as-is, no exception.
    assert result is member
    assert result.item_level == 120.0


async def test_recent_runs_parses_keys():
    payload = {
        "mythic_plus_recent_runs": [
            {
                "dungeon": "Ara-Kara, City of Echoes",
                "short_name": "ARAK",
                "mythic_level": 18,
                "num_keystone_upgrades": 2,
                "completed_at": "2026-06-01T12:00:00Z",
                "url": "https://raider.io/run/1",
                "score": 312.4,
            }
        ]
    }
    session = _FakeSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    runs = await client.recent_runs("Frostine", "hellfire", "eu")

    assert len(runs) == 1
    assert runs[0]["dungeon"] == "Ara-Kara, City of Echoes"
    assert runs[0]["level"] == 18
    assert runs[0]["upgrades"] == 2


async def test_recent_runs_empty_on_404():
    session = _FakeSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    assert await client.recent_runs("Ghost", "hellfire", "eu") == []


def test_summarize_progression_fallback_without_summary():
    progression = {
        "nerubar-palace": {
            "normal_bosses_killed": 8,
            "heroic_bosses_killed": 5,
            "mythic_bosses_killed": 1,
            "total_bosses": 8,
        }
    }
    assert _summarize_progression(progression) == (
        "Nerubar Palace: 8/8 N, 5/8 H, 1/8 M"
    )


def test_summarize_progression_none_when_empty():
    assert _summarize_progression({}) is None
