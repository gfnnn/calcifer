"""The Raider.IO client's raid catalog, guild overview and crawl timestamp,
plus the proxy-aware session helper every outbound call now shares."""

from __future__ import annotations

import pytest

from ftabot.models import Member
from ftabot.services import raiderio
from ftabot.services._http import new_session
from ftabot.services.raiderio import RaiderIOClient, RaiderIOError

from .test_raids import PROGRESSION, _static


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """The catalog is cached process-wide; keep each test independent."""
    raiderio._catalog_cache.clear()
    yield
    raiderio._catalog_cache.clear()


class _Rio(RaiderIOClient):
    """A client whose ``_get`` answers from a path -> payload map."""

    def __init__(self, routes):
        super().__init__(session=object())
        self.routes = routes
        self.calls: list[str] = []

    async def _get(self, path, params, *, retries=4):
        self.calls.append(path)
        answer = self.routes.get(path)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise RaiderIOError("not found")
        return answer


async def test_raid_catalog_parses_and_is_cached_across_clients():
    first = _Rio({"/raiding/static-data": _static()})
    catalog = await first.raid_catalog("eu")
    assert {r.slug for r in catalog} >= {"main-raid", "side-raid"}

    # A second client in the same process reuses the cached catalog: no call.
    second = _Rio({})
    assert await second.raid_catalog("eu") == catalog
    assert second.calls == []
    assert second._catalog == catalog


async def test_raid_catalog_failure_is_empty_and_not_cached():
    failing = _Rio({"/raiding/static-data": RuntimeError("boom")})
    assert await failing.raid_catalog("eu") == []
    assert failing._catalog == []
    # The miss was not cached, so the next client tries again and succeeds.
    working = _Rio({"/raiding/static-data": _static()})
    assert await working.raid_catalog("eu")
    assert working.calls == ["/raiding/static-data"]


async def test_guild_overview_returns_progress_and_rankings():
    rio = _Rio({"/guilds/profile": {"raid_progression": PROGRESSION, "raid_rankings": None}})
    overview = await rio.guild_overview("Guild", "realm", "eu")
    assert overview["raid_progression"] == PROGRESSION
    assert overview["raid_rankings"] == {}


async def test_guild_members_carry_the_crawl_timestamp():
    rio = _Rio(
        {
            "/guilds/profile": {
                "members": [
                    {"rank": 3, "character": {
                        "name": "Emberlyn", "realm": "Hellfire", "region": "eu",
                        "class": "Mage", "last_crawled_at": "2026-11-10T08:00:00.000Z",
                    }},
                    {"rank": 9, "character": {}},  # nameless entries are skipped
                ]
            }
        }
    )
    members = await rio.guild_members("Guild", "hellfire", "eu")
    assert [m.character_name for m in members] == ["Emberlyn"]
    assert members[0].last_crawled_at == "2026-11-10T08:00:00.000Z"


async def test_enrich_character_labels_progress_against_the_current_raid():
    rio = _Rio(
        {
            "/raiding/static-data": _static(),
            "/characters/profile": {
                "class": "Mage",
                "last_crawled_at": "2026-11-12T00:00:00.000Z",
                "gear": {"item_level_equipped": 320},
                "raid_progression": PROGRESSION,
            },
        }
    )
    member = await rio.enrich_character(Member("Emberlyn"))

    # The catalog was loaded on first use, and the summary describes the live
    # main raid by its real name (not the ended tier that comes first).
    assert rio.calls == ["/raiding/static-data", "/characters/profile"]
    assert member.raid_progression == "The Main Raid: 8/8 H"
    assert member.last_crawled_at == "2026-11-12T00:00:00.000Z"
    assert list(member.details["raid_progression"]) == ["main-raid", "side-raid", "old-tier"]
    assert member.details["raid_names"]["main-raid"] == "The Main Raid"


async def test_enrich_character_without_a_catalog_keeps_the_old_labelling():
    rio = _Rio({"/characters/profile": {"raid_progression": PROGRESSION}})
    member = await rio.enrich_character(Member("Emberlyn"))
    assert member.raid_progression == "Old Tier: 4/9 M"
    assert member.details["raid_names"]["old-tier"] == "Old Tier"


async def test_enrich_all_loads_the_catalog_once_up_front():
    rio = _Rio(
        {
            "/raiding/static-data": _static(),
            "/characters/profile": {"raid_progression": PROGRESSION},
        }
    )
    await rio.enrich_all([Member("A"), Member("B"), Member("C")], concurrency=2)
    assert rio.calls.count("/raiding/static-data") == 1
    assert rio.calls.count("/characters/profile") == 3


async def test_new_session_honours_the_host_proxy_settings():
    # aiohttp ignores HTTPS_PROXY unless trust_env is set; the helper sets it so
    # the bot works from a host that routes egress through a proxy.
    session = new_session({"Accept": "application/json"})
    try:
        assert session.trust_env is True
        assert session.headers["Accept"] == "application/json"
    finally:
        await session.close()
