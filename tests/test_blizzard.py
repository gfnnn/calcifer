"""Tests for the Blizzard (Battle.net) client — parsing + auth + error paths.

Network is faked: a stub session answers the OAuth token POST and the data
GETs, mirroring the offline pattern in tests/test_raiderio.py.
"""

from __future__ import annotations

import pytest

from ftabot.models import Member
from ftabot.services.blizzard import BlizzardClient, BlizzardError, _api_host


class _FakeResp:
    def __init__(self, status=200, payload=None, text="error body"):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    """Answers the token POST then serves queued GET responses in order."""

    def __init__(self, get_responses, *, token_status=200,
                 token_payload=None):
        self._gets = list(get_responses)
        self.get_calls = 0
        self.post_calls = 0
        self._token_status = token_status
        self._token_payload = token_payload or {
            "access_token": "tok", "expires_in": 3600
        }

    def post(self, url, **kwargs):
        self.post_calls += 1
        return _FakeResp(self._token_status, self._token_payload)

    def get(self, url, params=None, headers=None):
        resp = self._gets[min(self.get_calls, len(self._gets) - 1)]
        self.get_calls += 1
        return resp

    async def close(self):
        pass


def test_api_host_region_routing():
    assert _api_host("eu") == "https://eu.api.blizzard.com"
    assert _api_host("US") == "https://us.api.blizzard.com"
    assert _api_host("cn") == "https://gateway.battlenet.com.cn"


async def test_guild_roster_parses_members():
    payload = {
        "members": [
            {"character": {"name": "Ashbringer",
                           "realm": {"slug": "hellfire"}, "level": 80},
             "rank": 0},
            {"character": {"name": "Emberlyn", "level": 78}, "rank": 3},
            {"character": {}, "rank": 5},  # nameless -> skipped
        ]
    }
    session = _FakeSession([_FakeResp(200, payload)])
    async with BlizzardClient("id", "secret", region="eu", session=session) as bz:
        roster = await bz.guild_roster("From the Ashes", "Hellfire")

    assert roster == [
        ("Ashbringer", "hellfire", 80, 0),
        ("Emberlyn", "hellfire", 78, 3),  # missing realm -> guild realm slug
    ]


async def test_enrich_character_fills_fields():
    summary = {
        "race": {"name": "Dwarf"},
        "faction": {"name": "Alliance"},
        "character_class": {"name": "Hunter"},
        "active_spec": {"name": "Marksmanship"},
        "level": 80,
        "equipped_item_level": 632,
    }
    session = _FakeSession([_FakeResp(200, summary)])
    member = Member("Boomer", realm_slug="hellfire", region="eu")
    async with BlizzardClient("id", "secret", session=session) as bz:
        await bz.enrich_character(member)

    assert member.race == "Dwarf"
    assert member.faction == "Alliance"
    assert member.class_name == "Hunter"
    assert member.active_spec == "Marksmanship"
    assert member.level == 80
    assert member.item_level == 632.0


async def test_enrich_character_returns_member_unchanged_on_404():
    session = _FakeSession([_FakeResp(404)])
    member = Member("Ghost", realm_slug="hellfire", region="eu", item_level=600.0)
    async with BlizzardClient("id", "secret", session=session) as bz:
        result = await bz.enrich_character(member)

    assert result is member
    assert result.item_level == 600.0  # nothing clobbered


async def test_token_is_cached_across_calls():
    session = _FakeSession(
        [_FakeResp(200, {"members": []}), _FakeResp(200, {"members": []})]
    )
    async with BlizzardClient("id", "secret", session=session) as bz:
        await bz.guild_roster("G", "Hellfire")
        await bz.guild_roster("G", "Hellfire")

    assert session.post_calls == 1  # token fetched once, then reused


async def test_token_failure_raises():
    session = _FakeSession([_FakeResp(200, {})], token_status=401)
    async with BlizzardClient("id", "secret", session=session) as bz:
        with pytest.raises(BlizzardError):
            await bz.guild_roster("G", "Hellfire")


async def test_non_200_data_response_raises():
    session = _FakeSession([_FakeResp(500)])
    async with BlizzardClient("id", "secret", session=session) as bz:
        with pytest.raises(BlizzardError):
            await bz.guild_roster("G", "Hellfire")


def test_session_property_requires_context_manager():
    bz = BlizzardClient("id", "secret")
    with pytest.raises(RuntimeError):
        _ = bz.session


async def test_get_retries_once_on_401_with_fresh_token():
    # An early-revoked token surfaces as 401; the client should re-mint and retry.
    payload = {"members": []}
    session = _FakeSession([_FakeResp(401), _FakeResp(200, payload)])
    async with BlizzardClient("id", "secret", session=session) as bz:
        out = await bz.guild_roster("From the Ashes", "hellfire")
    assert out == []
    assert session.get_calls == 2  # 401, then success
    assert session.post_calls == 2  # token re-minted on retry


async def test_get_does_not_retry_twice_on_persistent_401():
    session = _FakeSession([_FakeResp(401)])
    async with BlizzardClient("id", "secret", session=session) as bz:
        with pytest.raises(BlizzardError):
            await bz.guild_roster("From the Ashes", "hellfire")
    assert session.get_calls == 2  # one retry only
