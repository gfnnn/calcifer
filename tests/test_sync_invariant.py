"""End-to-end tests for ``sync_guild`` against a real temp-file database.

These cover the orchestration the old ``test_sync.py`` skipped (it only tested
the pure ``_build_snapshot`` helper). The headline guarantee verified here is
the codebase's central invariant: **a roster sync never overwrites
human-entered fields** (discord_id, is_main, notes, raid_team, officer_role).

Network is faked — ``ftabot.sync.RaiderIOClient`` is swapped for an in-memory
fake, and the optional WoW Audit / Blizzard / Warcraft Logs overlays stay off
because the default ``Config`` leaves their credentials unset.
"""

from __future__ import annotations

import copy

from ftabot import sync
from ftabot.config import Config
from ftabot.models import Member, WishlistItem
from ftabot.services.raiderio import RaiderIOError
from ftabot.sync import sync_guild


def _make_fake_rio(roster: list[Member], enrich_map: dict | None = None):
    """Build a drop-in ``RaiderIOClient`` replacement serving a fixed roster.

    ``sync_guild`` instantiates the client twice (roster fetch + enrichment),
    so the behaviour is encapsulated on the class rather than per-instance.
    """
    enrich_map = enrich_map or {}

    class _FakeRio:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def guild_members(self, name, realm, region):
            # Hand back deep copies so a sync run can't mutate the test's roster.
            return [copy.deepcopy(m) for m in roster]

        async def enrich_all(self, members, *, concurrency=3):
            for m in members:
                for key, val in (enrich_map.get(m.character_name.lower()) or {}).items():
                    setattr(m, key, val)
            return members

    return _FakeRio


def _raising_rio(exc: Exception):
    class _BrokenRio:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def guild_members(self, name, realm, region):
            raise exc

    return _BrokenRio


def _roster() -> list[Member]:
    return [
        Member("Alpha", realm_slug="hellfire", region="eu", role="TANK",
               class_name="Warrior", item_level=600.0, mythic_plus_score=2500.0),
        Member("Bravo", realm_slug="hellfire", region="eu", role="HEALER",
               class_name="Priest", item_level=610.0, mythic_plus_score=2600.0),
    ]


async def test_sync_inserts_roster_and_snapshots(db, monkeypatch):
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(_roster()))
    result = await sync_guild(db, Config())

    assert result.error is None
    assert result.fetched == 2
    assert result.upserted == 2
    assert result.pruned == 0
    assert "Raider.IO" in result.sources
    assert result.health_score is not None  # a snapshot was computed + stored

    stored = {m.character_name: m for m in await db.all_members()}
    assert set(stored) == {"Alpha", "Bravo"}
    assert stored["Alpha"].item_level == 600.0
    assert stored["Alpha"].role == "TANK"


async def test_sync_preserves_manual_fields(db, monkeypatch):
    """The core invariant: re-syncing updates API fields but not human ones."""
    roster = _roster()
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))
    await sync_guild(db, Config())

    alpha = await db.get_member("Alpha", "hellfire", "eu")
    bravo = await db.get_member("Bravo", "hellfire", "eu")
    # An officer hand-edits Alpha: links Discord, notes, raid team, makes it an
    # alt of Bravo, and assigns an officer responsibility.
    await db.set_discord_link(alpha.id, "discord-123")
    await db.set_note(alpha.id, "Reliable off-tank")
    await db.set_raid_team(alpha.id, "Team Cinder")
    await db.set_officer_role(alpha.id, "Loot")
    await db.set_main_alt(alpha.id, is_main=False, main_character_id=bravo.id)

    # A later roster sync brings fresh API stats for Alpha.
    roster[0].item_level = 655.0
    roster[0].mythic_plus_score = 3100.0
    await sync_guild(db, Config())

    refreshed = await db.get_member("Alpha", "hellfire", "eu")
    # API-owned fields updated...
    assert refreshed.item_level == 655.0
    assert refreshed.mythic_plus_score == 3100.0
    # ...human-owned fields untouched.
    assert refreshed.discord_id == "discord-123"
    assert refreshed.notes == "Reliable off-tank"
    assert refreshed.raid_team == "Team Cinder"
    assert refreshed.officer_role == "Loot"
    assert refreshed.is_main is False
    assert refreshed.main_character_id == bravo.id


async def test_sync_prunes_members_who_left(db, monkeypatch):
    roster = _roster()
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))
    await sync_guild(db, Config())
    assert await db.count() == 2

    # Bravo leaves the guild — next roster only has Alpha.
    roster.pop()  # drop Bravo
    result = await sync_guild(db, Config())

    assert result.pruned == 1
    remaining = {m.character_name for m in await db.all_members()}
    assert remaining == {"Alpha"}


async def test_sync_no_prune_keeps_departed_members(db, monkeypatch):
    roster = _roster()
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))
    await sync_guild(db, Config())

    roster.pop()  # Bravo leaves
    result = await sync_guild(db, Config(), prune=False)

    assert result.pruned == 0
    assert {m.character_name for m in await db.all_members()} == {"Alpha", "Bravo"}


async def test_empty_roster_fetch_does_not_wipe_table(db, monkeypatch):
    """A successful-but-empty roster fetch must never prune the whole table.

    Regression: if an upstream hiccup returns 0 members (a 200 with no usable
    ``members`` array, schema drift, a renamed guild), ``prune_missing([])``
    would delete every row, manual fields and all. The sync must protect the
    existing roster instead and recover on the next good fetch.
    """
    roster = _roster()
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))
    await sync_guild(db, Config())
    alpha = await db.get_member("Alpha", "hellfire", "eu")
    await db.set_note(alpha.id, "do not lose me")
    assert await db.count() == 2

    # The upstream now returns an empty roster (no exception raised).
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio([]))
    result = await sync_guild(db, Config())  # prune defaults to True

    assert result.fetched == 0
    assert result.pruned == 0
    # The roster (and the human-entered note) survives the bad fetch.
    assert await db.count() == 2
    preserved = await db.get_member("Alpha", "hellfire", "eu")
    assert preserved.notes == "do not lose me"


async def test_sync_roster_failure_returns_error_without_writing(db, monkeypatch):
    monkeypatch.setattr(
        sync, "RaiderIOClient", _raising_rio(RaiderIOError("roster down"))
    )
    result = await sync_guild(db, Config())

    assert result.error is not None
    assert "roster down" in result.error
    assert result.summary().startswith("Sync failed")
    # A failed roster fetch is fatal before any persistence happens.
    assert await db.count() == 0


async def test_sync_enriches_when_roster_lacks_stats(db, monkeypatch):
    """A bare roster (name/class only) is filled in by the enrichment step."""
    bare = [Member("Cinder", realm_slug="hellfire", region="eu", class_name="Mage")]
    enrich_map = {"cinder": {"item_level": 640.0, "mythic_plus_score": 2900.0,
                             "role": "DPS"}}
    monkeypatch.setattr(
        sync, "RaiderIOClient", _make_fake_rio(bare, enrich_map=enrich_map)
    )
    # enrich_max_age_hours=0 => always enrich (no freshness skip).
    result = await sync_guild(db, Config(enrich_max_age_hours=0))

    assert result.enriched == 1
    stored = await db.get_member("Cinder", "hellfire", "eu")
    assert stored.item_level == 640.0
    assert stored.role == "DPS"


# --- full overlay path (WoW Audit + Blizzard + Warcraft Logs) ---------------


def _fake_wowaudit():
    class _FakeWA:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def team_name(self):
            return "Team Cinder"

        async def characters(self):
            return [Member("Alpha", realm_slug="hellfire", region="eu")]

        async def vault_slots(self):
            return {"alpha": 5}

        async def wishlists(self):
            return [WishlistItem("Alpha", realm_slug="hellfire", region="eu",
                                 item_id=1, item_name="Ashen Blade", is_bis=True)]

        async def recent_attendance(self):
            return [{"date": "2026-06-01", "instance": "Palace",
                     "difficulty": "Mythic",
                     "signups": [("Alpha", "present"), ("Bravo", "absent")]}]

        async def upcoming_raid_signups(self, *, tz=None, now=None):
            return {"date": "2026-06-10", "time": "20:00", "start": None,
                    "instance": "The Voidspire", "difficulty": "Mythic",
                    "signups": [("Alpha", "present"), ("Bravo", "tentative")]}

    return _FakeWA


def _fake_blizzard():
    class _FakeBZ:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def guild_roster(self, name, realm):
            return [("Alpha", "hellfire", 80, 1)]

        async def enrich_character(self, member):
            member.race = "Human"
            return member

    return _FakeBZ


def _fake_wcl():
    class _FakeWCL:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def enrich_character(self, member):
            member.best_parse_avg = 95.0
            return member

    return _FakeWCL


async def test_sync_full_overlay_persists_everything(db, monkeypatch):
    """Enable every optional source and verify the overlay data all lands."""
    roster = [Member("Alpha", realm_slug="hellfire", region="eu", role="TANK",
                      class_name="Warrior", item_level=600.0),
              Member("Bravo", realm_slug="hellfire", region="eu", role="DPS",
                     class_name="Rogue", item_level=610.0)]
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))
    monkeypatch.setattr(sync, "WowAuditClient", _fake_wowaudit())
    monkeypatch.setattr(sync, "BlizzardClient", _fake_blizzard())
    monkeypatch.setattr(sync, "WarcraftLogsClient", _fake_wcl())

    cfg = Config(
        wowaudit_api_key="k",
        blizzard_client_id="i", blizzard_client_secret="s",
        warcraftlogs_client_id="i", warcraftlogs_client_secret="s",
        enrich_max_age_hours=0,
    )
    result = await sync_guild(db, cfg)

    assert set(result.sources) == {
        "Raider.IO", "WoW Audit", "Blizzard", "Warcraft Logs"
    }
    assert result.raiders == 1
    assert result.wishlist_items == 1
    assert result.attendance_events == 1

    alpha = await db.get_member("Alpha", "hellfire", "eu")
    # raid_team is a *manual* column, so the WoW Audit team tag is applied to
    # the in-memory member but intentionally NOT written by upsert_synced — only
    # the sync-owned fields below get persisted.
    assert alpha.raid_team is None
    assert alpha.vault_slots_filled == 5       # WoW Audit vault overlay
    assert alpha.race == "Human"               # Blizzard enrichment
    assert alpha.best_parse_avg == 95.0        # Warcraft Logs enrichment
    assert alpha.guild_rank == 1               # Blizzard roster rank

    wishlist = await db.get_wishlist("Alpha")
    assert [w.item_name for w in wishlist] == ["Ashen Blade"]

    # The next raid's sign-ups are stored for the team-chemistry roster view.
    state = await db.roster_signups()
    assert state["raid"]["instance"] == "The Voidspire"
    assert {s["name"] for s in state["signups"]} == {"Alpha", "Bravo"}


async def test_sync_wowaudit_overlay_failure_is_non_fatal(db, monkeypatch):
    """A broken WoW Audit overlay is logged and skipped, not fatal."""
    roster = _roster()
    monkeypatch.setattr(sync, "RaiderIOClient", _make_fake_rio(roster))

    class _BrokenWA:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def team_name(self):
            raise RuntimeError("wowaudit down")

    monkeypatch.setattr(sync, "WowAuditClient", _BrokenWA)
    result = await sync_guild(db, Config(wowaudit_api_key="k"))

    # The roster still syncs from Raider.IO; WoW Audit just drops out.
    assert result.error is None
    assert result.sources == ("Raider.IO",)
    assert await db.count() == 2
