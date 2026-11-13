"""The sync's raid-progress step and its handling of an unreachable Raider.IO."""

from __future__ import annotations

import aiohttp

from ftabot import raids
from ftabot import sync as sync_mod
from ftabot.config import Config
from ftabot.models import Member
from ftabot.sync import sync_guild

from .test_raids import PROGRESSION, _static


class _FakeRio:
    """Stands in for RaiderIOClient: a fixed roster plus raid data."""

    def __init__(self, *, roster=None, overview=None, catalog=None, roster_error=None):
        self._roster = roster or [Member("Emberlyn")]
        self._overview = overview
        self._catalog = catalog
        self._roster_error = roster_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def guild_members(self, name, realm, region):
        if self._roster_error:
            raise self._roster_error
        return list(self._roster)

    async def raid_catalog(self, region):
        return self._catalog or []

    async def guild_overview(self, name, realm, region):
        if isinstance(self._overview, Exception):
            raise self._overview
        return self._overview

    async def enrich_all(self, members, *, concurrency=2):
        return members


def _patch(monkeypatch, **kw):
    monkeypatch.setattr(sync_mod, "RaiderIOClient", lambda *a, **k: _FakeRio(**kw))


async def test_sync_stores_the_guilds_raid_progress(db, monkeypatch):
    _patch(
        monkeypatch,
        catalog=raids.parse_catalog(_static(), "eu"),
        overview={"raid_progression": PROGRESSION, "raid_rankings": {}},
    )
    result = await sync_guild(db, Config())
    assert result.error is None

    stored = await db.guild_raids()
    # The open main raid leads under its catalog name; the ended tier trails.
    assert [r["raid_slug"] for r in stored] == ["main-raid", "side-raid", "old-tier"]
    assert stored[0]["name"] == "The Main Raid" and stored[0]["is_current"] is True
    assert stored[2]["is_current"] is False


async def test_a_failed_raid_fetch_keeps_the_roster_sync_and_old_progress(db, monkeypatch):
    await db.replace_guild_raids(
        [{"raid_slug": "kept", "name": "Kept", "total_bosses": 8, "heroic_killed": 3}]
    )
    _patch(monkeypatch, overview=RuntimeError("raider.io hiccup"))
    result = await sync_guild(db, Config())
    assert result.error is None and result.upserted == 1
    assert [r["raid_slug"] for r in await db.guild_raids()] == ["kept"]


async def test_an_unreachable_raiderio_is_a_reported_error_not_a_crash(db, monkeypatch):
    _patch(monkeypatch, roster_error=aiohttp.ClientConnectionError("no route"))
    result = await sync_guild(db, Config())
    assert result.error and "unreachable" in result.error
    assert "Sync failed" in result.summary()
