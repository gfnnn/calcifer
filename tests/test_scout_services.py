"""Parsing tests for the recruitment scout's service-layer additions.

Network is faked (no real HTTP), mirroring tests/test_raiderio.py.
"""

import pytest

from ftabot.services.raiderio import RaiderIOClient
from ftabot.services.warcraftlogs import (
    WarcraftLogsClient,
    _parse_ranking,
    metric_for,
)

# --- fakes ------------------------------------------------------------------


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


class _FakeGetSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp

    async def close(self):
        pass


class _FakePostSession:
    """Serves queued JSON payloads to POST (token first, then GraphQL)."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0

    def post(self, url, **kwargs):
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return _FakeResp(200, payload)

    async def close(self):
        pass


# --- Raider.IO candidate card ----------------------------------------------


async def test_character_card_aligns_seasons_oldest_first():
    payload = {
        "name": "Climber",
        "realm": "Ravencrest",
        "region": "eu",
        "class": "Paladin",
        "active_spec_name": "Holy",
        "active_spec_role": "healer",
        "gear": {"item_level_equipped": 289.5},
        "guild": {"name": "Casual Embers"},
        "mythic_plus_scores_by_season": [
            {"season": "season-tww-1", "scores": {"all": 2100}},
            {"season": "season-tww-2", "scores": {"all": 2600}},
            # current season intentionally missing -> None in that slot
        ],
    }
    session = _FakeGetSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    card = await client.character_card(
        "Climber", "ravencrest", "eu",
        ["season-tww-1", "season-tww-2", "season-tww-3"],
    )
    assert card["class_name"] == "Paladin"
    assert card["role"] == "HEALER"
    assert card["item_level"] == 289.5
    assert card["guild_name"] == "Casual Embers"
    assert card["mplus_seasons"] == [2100, 2600, None]
    assert card["lfg_active"] is False  # no recruitmentProfiles in payload


async def test_character_card_lfg_active_when_recruitment_profile_present():
    payload = {
        "name": "Seeker",
        "realm": "Ravencrest",
        "region": "eu",
        "class": "Mage",
        "gear": {"item_level_equipped": 285.0},
        "guild": {"name": "Some Guild"},
        "mythic_plus_scores_by_season": [],
        # Non-empty recruitmentProfiles array → player is actively LFG.
        "recruitmentProfiles": [{"status": "active", "activities": ["raiding"]}],
    }
    session = _FakeGetSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    card = await client.character_card("Seeker", "ravencrest", "eu", ["season-tww-3"])
    assert card["lfg_active"] is True


async def test_character_card_returns_none_on_404():
    session = _FakeGetSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    card = await client.character_card("Ghost", "ravencrest", "eu", ["season-tww-1"])
    assert card is None


async def test_guild_strength_converts_mythic_progress_to_fraction():
    payload = {
        "raid_progression": {
            "liberation-of-undermine": {
                "total_bosses": 8,
                "normal_bosses_killed": 8,
                "heroic_bosses_killed": 8,
                "mythic_bosses_killed": 3,
            }
        }
    }
    session = _FakeGetSession([_FakeResp(200, payload)])
    client = RaiderIOClient(session=session)
    strength = await client.guild_strength("From the Ashes", "hellfire", "eu")
    assert strength == pytest.approx(3 / 8, abs=0.01)


async def test_guild_strength_returns_none_on_404():
    session = _FakeGetSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    strength = await client.guild_strength("Unknown Guild", "hellfire", "eu")
    assert strength is None


# --- Warcraft Logs discovery ------------------------------------------------


def test_metric_for_healer_vs_dps():
    assert metric_for("Holy", "HEALER") == "hps"
    assert metric_for("Restoration", None) == "hps"
    assert metric_for("Frost", "DPS") == "dps"
    assert metric_for(None, None) == "dps"


def test_parse_ranking_normalises_server_and_guild():
    entry = {
        "name": "Toppal",
        "class": "Paladin",
        "spec": "Holy",
        "amount": 123456.0,
        "rankPercent": 99.1,
        "server": {"name": "Twisting Nether", "region": "EU"},
        "guild": {"name": "Hardcore"},
    }
    parsed = _parse_ranking(entry)
    assert parsed["name"] == "Toppal"
    assert parsed["realm_slug"] == "twisting-nether"
    assert parsed["region"] == "eu"
    assert parsed["guild_name"] == "Hardcore"
    assert parsed["rank_percent"] == 99.1


def test_parse_ranking_skips_nameless():
    assert _parse_ranking({"class": "Mage"}) is None


async def test_character_rankings_parses_pages():
    token = {"access_token": "t", "expires_in": 3600}
    page1 = {
        "data": {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "hasMorePages": False,
                        "rankings": [
                            {
                                "name": "Alpha",
                                "class": "Paladin",
                                "spec": "Holy",
                                "amount": 5000,
                                "server": {"name": "Ravencrest", "region": "EU"},
                                "guild": {"name": "Embers"},
                            },
                            {"class": "Paladin"},  # malformed -> skipped
                        ],
                    }
                }
            }
        }
    }
    session = _FakePostSession([token, page1])
    async with WarcraftLogsClient("id", "secret", zone_id=1, session=session) as wcl:
        results = await wcl.character_rankings(
            42, class_name="Paladin", spec_name="Holy", metric="hps", region="eu"
        )
    assert [r["name"] for r in results] == ["Alpha"]
    assert results[0]["realm_slug"] == "ravencrest"


async def test_parse_curve_orders_zones_with_gaps():
    token = {"access_token": "t", "expires_in": 3600}

    def zone_payload(best):
        char = {"zoneRankings": {"bestPerformanceAverage": best}} if best is not None else {"zoneRankings": {}}
        return {"data": {"characterData": {"character": char}}}

    session = _FakePostSession([token, zone_payload(72.0), zone_payload(None), zone_payload(88.0)])
    async with WarcraftLogsClient("id", "secret", zone_id=1, session=session) as wcl:
        curve = await wcl.parse_curve("Alpha", "ravencrest", "eu", [10, 20, 30])
    assert curve == [72.0, None, 88.0]
