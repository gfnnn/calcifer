"""Tests for the Raider.IO affix + realm-progression feed infrastructure.

Covers:
  - DB methods: upsert_affix_week / get_current_affixes
  - DB methods: upsert_realm_guild / get_realm_progress
  - New service methods: current_affixes / realm_raid_progression
  - Feed sources: RaiderIOAffixesSource / RaiderIORealmProgressSource (offline)
"""

from __future__ import annotations

import dataclasses

from ftabot.config import Config
from ftabot.feeds.raiderio_affixes import RaiderIOAffixesSource, _affix_summary
from ftabot.feeds.raiderio_realm_progress import RaiderIORealmProgressSource
from ftabot.services.raiderio import RaiderIOClient

# --- helpers ----------------------------------------------------------------


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


_AFFIXES_PAYLOAD = {
    "region": "eu",
    "title": "Tyrannical, Bursting, Storming",
    "leaderboard_url": "https://raider.io/...",
    "affix_details": [
        {"id": 9, "name": "Tyrannical", "description": "Boss hp up.", "wowhead_url": ""},
        {"id": 11, "name": "Bursting", "description": "Adds explode.", "wowhead_url": ""},
        {"id": 24, "name": "Storming", "description": "Storm tornados.", "wowhead_url": ""},
    ],
}

def _encounters(kills: int, total: int) -> list[dict]:
    """Build a minimal encountersDefeated list with `kills` defeated out of `total`."""
    ts = "2025-01-01T00:00:00.000Z"
    return [
        {"slug": f"boss-{i}", "firstDefeated": ts if i <= kills else None}
        for i in range(1, total + 1)
    ]


_REALM_PAYLOAD = {
    "raidRankings": [
        {
            "guild": {"name": "From the Ashes", "faction": "horde"},
            "rank": 1,
            "encountersDefeated": _encounters(5, 8),
        },
        {
            "guild": {"name": "Rival Guild", "faction": "alliance"},
            "rank": 2,
            "encountersDefeated": _encounters(3, 8),
        },
    ]
}


# --- service: current_affixes -----------------------------------------------


async def test_current_affixes_returns_list():
    session = _FakeSession([_FakeResp(200, _AFFIXES_PAYLOAD)])
    client = RaiderIOClient(session=session)
    affixes = await client.current_affixes("eu")
    assert len(affixes) == 3
    assert affixes[0]["name"] == "Tyrannical"
    assert affixes[0]["id"] == 9


async def test_current_affixes_returns_empty_on_error():
    session = _FakeSession([_FakeResp(500)])
    client = RaiderIOClient(session=session)
    affixes = await client.current_affixes("eu")
    assert affixes == []


# --- service: realm_raid_progression ----------------------------------------


async def test_realm_raid_progression_returns_guilds():
    session = _FakeSession([_FakeResp(200, _REALM_PAYLOAD)])
    client = RaiderIOClient(session=session)
    guilds = await client.realm_raid_progression(
        "liberation-of-undermine", "eu", "hellfire", "mythic"
    )
    assert len(guilds) == 2
    assert guilds[0]["guild"]["name"] == "From the Ashes"


async def test_realm_raid_progression_empty_on_404():
    session = _FakeSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    guilds = await client.realm_raid_progression(
        "liberation-of-undermine", "eu", "hellfire"
    )
    assert guilds == []


# --- DB: mythic_affixes -----------------------------------------------------


async def test_upsert_and_get_current_affixes(db):
    details = [{"id": 9, "name": "Tyrannical"}, {"id": 11, "name": "Bursting"}]
    await db.upsert_affix_week("eu-11-9", "eu", "Tyrannical, Bursting", details)
    row = await db.get_current_affixes("eu")
    assert row is not None
    assert row["week_key"] == "eu-11-9"
    assert row["affix_names"] == "Tyrannical, Bursting"
    assert row["details"] == details
    assert row["region"] == "eu"


async def test_get_current_affixes_returns_none_when_empty(db):
    assert await db.get_current_affixes("eu") is None


async def test_upsert_affix_week_replaces_same_key(db):
    await db.upsert_affix_week("eu-9", "eu", "Tyrannical", [])
    await db.upsert_affix_week("eu-9", "eu", "Tyrannical (updated)", [{"id": 9}])
    row = await db.get_current_affixes("eu")
    assert row["affix_names"] == "Tyrannical (updated)"


async def test_get_current_affixes_returns_most_recent(db):
    await db.upsert_affix_week("eu-old", "eu", "Old affixes", [])
    await db.upsert_affix_week("eu-new", "eu", "New affixes", [])
    row = await db.get_current_affixes("eu")
    assert row["affix_names"] == "New affixes"


# --- DB: realm_progress -----------------------------------------------------


async def test_upsert_realm_guild_first_insert_returns_false(db):
    new_kill = await db.upsert_realm_guild(
        "From the Ashes", "hellfire", "eu", "liberation-of-undermine", "mythic", 3,
        summary="3/8 M", realm_rank=1,
    )
    assert new_kill is False


async def test_upsert_realm_guild_unchanged_kills_returns_false(db):
    await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 3)
    assert await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 3) is False


async def test_upsert_realm_guild_kill_increase_returns_true(db):
    await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 3)
    new_kill = await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 4)
    assert new_kill is True


async def test_upsert_realm_guild_kill_decrease_returns_false(db):
    await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 5)
    new_kill = await db.upsert_realm_guild("FtA", "hellfire", "eu", "raid", "mythic", 3)
    assert new_kill is False


async def test_get_realm_progress_sorted_by_kills(db):
    await db.upsert_realm_guild("Guild A", "hellfire", "eu", "raid", "mythic", 2)
    await db.upsert_realm_guild("Guild B", "hellfire", "eu", "raid", "mythic", 5)
    await db.upsert_realm_guild("Guild C", "hellfire", "eu", "raid", "mythic", 3)
    rows = await db.get_realm_progress("hellfire", "eu", "raid")
    assert [r["guild_name"] for r in rows] == ["Guild B", "Guild C", "Guild A"]


async def test_get_realm_progress_empty_before_any_data(db):
    assert await db.get_realm_progress("hellfire", "eu", "raid") == []


# --- affix feed source (offline) -------------------------------------------


class _FakeRioSession:
    """Serves a single payload for any GET."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, url, params=None):
        return _FakeResp(200, self._payload)

    async def close(self):
        pass


async def test_affixes_source_emits_event_and_persists(db, tmp_path):
    cfg = dataclasses.replace(
        Config(), database_path=str(tmp_path / "test.db"), guild_region="eu"
    )
    # Re-use the already-connected db fixture path — source opens its own conn.
    cfg = dataclasses.replace(cfg, database_path=db.path)

    source = RaiderIOAffixesSource(cfg)
    events = await source.fetch(_FakeRioSession(_AFFIXES_PAYLOAD))

    assert len(events) == 1
    ev = events[0]
    assert ev.source == "raiderio_affixes"
    assert ev.event_type == "raiderio.affixes-changed"
    assert "Tyrannical" in ev.title
    assert "Bursting" in ev.title

    # Persisted to DB.
    row = await db.get_current_affixes("eu")
    assert row is not None
    assert "Tyrannical" in row["affix_names"]


async def test_affixes_source_stable_external_id(db):
    cfg = dataclasses.replace(Config(), database_path=db.path, guild_region="eu")
    source = RaiderIOAffixesSource(cfg)
    events = await source.fetch(_FakeRioSession(_AFFIXES_PAYLOAD))
    # Sorted IDs: 9, 11, 24 → key "eu-11-24-9"
    assert events[0].external_id == "eu-11-24-9"


async def test_affixes_source_empty_response_yields_no_events(db):
    cfg = dataclasses.replace(Config(), database_path=db.path, guild_region="eu")
    source = RaiderIOAffixesSource(cfg)
    events = await source.fetch(_FakeRioSession({"affix_details": []}))
    assert events == []


# --- realm progress feed source (offline) ----------------------------------


async def test_realm_progress_source_first_poll_no_events(db):
    """First poll: guilds are inserted but no kill increases → no events."""
    cfg = dataclasses.replace(
        Config(),
        database_path=db.path,
        guild_region="eu",
        guild_realm="hellfire",
        guild_name="From the Ashes",
        realm_progress_raids=("liberation-of-undermine",),
        realm_progress_difficulty="mythic",
    )
    source = RaiderIORealmProgressSource(cfg)
    events = await source.fetch(_FakeRioSession(_REALM_PAYLOAD))
    assert events == []

    # But data was persisted.
    rows = await db.get_realm_progress("hellfire", "eu", "liberation-of-undermine")
    assert len(rows) == 2


async def test_realm_progress_source_kill_increase_emits_event(db):
    """Second poll with more kills → one event per guild that progressed."""
    cfg = dataclasses.replace(
        Config(),
        database_path=db.path,
        guild_region="eu",
        guild_realm="hellfire",
        guild_name="From the Ashes",
        realm_progress_raids=("liberation-of-undermine",),
        realm_progress_difficulty="mythic",
    )
    source = RaiderIORealmProgressSource(cfg)

    # Seed baseline via first poll (no events expected).
    await source.fetch(_FakeRioSession(_REALM_PAYLOAD))

    # Second poll: FtA progresses to 6/8, rival stays at 3/8.
    updated_payload = {
        "raidRankings": [
            {
                "guild": {"name": "From the Ashes"},
                "rank": 1,
                "encountersDefeated": _encounters(6, 8),
            },
            {
                "guild": {"name": "Rival Guild"},
                "rank": 2,
                "encountersDefeated": _encounters(3, 8),
            },
        ]
    }
    events = await source.fetch(_FakeRioSession(updated_payload))

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "raiderio.our-progression"
    assert "From the Ashes" in ev.title
    assert "6/8" in ev.title
    assert ev.severity == "warning"


async def test_realm_progress_rival_kill_uses_different_event_type(db):
    cfg = dataclasses.replace(
        Config(),
        database_path=db.path,
        guild_region="eu",
        guild_realm="hellfire",
        guild_name="From the Ashes",
        realm_progress_raids=("liberation-of-undermine",),
        realm_progress_difficulty="mythic",
    )
    source = RaiderIORealmProgressSource(cfg)
    # First poll seeds 3 kills for Rival Guild.
    await source.fetch(_FakeRioSession(_REALM_PAYLOAD))
    # Second poll: rival advances.
    rival_up = {
        "raidRankings": [
            {
                "guild": {"name": "From the Ashes"},
                "rank": 1,
                "encountersDefeated": _encounters(5, 8),
            },
            {
                "guild": {"name": "Rival Guild"},
                "rank": 2,
                "encountersDefeated": _encounters(5, 8),
            },
        ]
    }
    events = await source.fetch(_FakeRioSession(rival_up))
    rival_events = [e for e in events if "Rival" in e.title]
    assert len(rival_events) == 1
    assert rival_events[0].event_type == "raiderio.realm-progression"
    assert rival_events[0].severity == "info"


async def test_realm_progress_no_raid_slug_yields_no_events(db):
    cfg = dataclasses.replace(
        Config(), database_path=db.path, realm_progress_raids=()
    )
    source = RaiderIORealmProgressSource(cfg)
    assert await source.fetch(_FakeRioSession(_REALM_PAYLOAD)) == []


async def test_realm_progress_multi_raid_tracks_each_independently(db):
    """Two raids configured → kill increase in either raid emits its own event."""

    class _MultiRaidSession:
        """Returns raid-specific payloads keyed by the 'raid' query param."""
        def __init__(self, payloads: dict):
            self._payloads = payloads
            self.queried: list[str] = []

        def get(self, url, params=None):
            raid = (params or {}).get("raid", "")
            self.queried.append(raid)
            payload = self._payloads.get(raid, {"guilds": []})
            return _FakeResp(200, payload)

        async def close(self):
            pass

    raid_a = {
        "raidRankings": [{"guild": {"name": "From the Ashes"}, "rank": 1,
                          "encountersDefeated": _encounters(3, 8)}]
    }
    raid_b = {
        "raidRankings": [{"guild": {"name": "From the Ashes"}, "rank": 1,
                          "encountersDefeated": _encounters(2, 6)}]
    }
    cfg = dataclasses.replace(
        Config(),
        database_path=db.path,
        guild_region="eu",
        guild_realm="hellfire",
        guild_name="From the Ashes",
        realm_progress_raids=("voidspire", "dreamrift"),
        realm_progress_difficulty="mythic",
    )
    source = RaiderIORealmProgressSource(cfg)
    session = _MultiRaidSession({"voidspire": raid_a, "dreamrift": raid_b})

    # First poll — seeds both raids, no events.
    await source.fetch(session)
    assert set(session.queried) == {"voidspire", "dreamrift"}

    # Second poll — FtA progresses in voidspire only.
    raid_a_updated = {
        "raidRankings": [{"guild": {"name": "From the Ashes"}, "rank": 1,
                          "encountersDefeated": _encounters(4, 8)}]
    }
    session2 = _MultiRaidSession({"voidspire": raid_a_updated, "dreamrift": raid_b})
    events = await source.fetch(session2)
    assert len(events) == 1
    assert "voidspire" in events[0].extra["raid_slug"]


# --- config parsing ---------------------------------------------------------


def test_config_parses_realm_progress_raids(monkeypatch):
    monkeypatch.setenv("REALM_PROGRESS_RAIDS", "voidspire, dreamrift , march")
    monkeypatch.setenv("REALM_PROGRESS_DIFFICULTY", "heroic")
    cfg = Config.from_env()
    assert cfg.realm_progress_raids == ("voidspire", "dreamrift", "march")
    assert cfg.realm_progress_difficulty == "heroic"


def test_config_realm_progress_raids_default():
    cfg = Config()
    assert cfg.realm_progress_raids == ("tier-mn-1",)


# --- _affix_summary helper --------------------------------------------------


def test_affix_summary_includes_descriptions():
    affixes = [
        {"name": "Tyrannical", "description": "Boss hp up."},
        {"name": "Bursting", "description": "Adds explode."},
    ]
    summary = _affix_summary(affixes)
    assert "**Tyrannical**: Boss hp up." in summary
    assert "**Bursting**: Adds explode." in summary


def test_affix_summary_works_without_description():
    affixes = [{"name": "Tyrannical"}]
    assert _affix_summary(affixes) == "**Tyrannical**"
