"""The three data boards added for the MVP: Raid Progress (raid), The Armoury
(gear) and The Climb (levelling). Each renders offline from the stored data."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

from ftabot import brand
from ftabot.boards import BOARD_NAMES, REGISTRY
from ftabot.boards._weekly import entity_key, weekly_baseline
from ftabot.boards.armoury import ARMOURY_TYPE, ArmouryBoard
from ftabot.boards.climb import CLIMB_TYPE, ClimbBoard
from ftabot.boards.raidprog import RAIDPROG_TYPE, RaidProgressBoard, _bar, _standing
from ftabot.config import Config
from ftabot.models import MAX_LEVEL, Member


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _raid(slug, name, total, n, h, m, *, current=True, summary=None, ranks=None):
    return {
        "raid_slug": slug, "name": name, "total_bosses": total,
        "normal_killed": n, "heroic_killed": h, "mythic_killed": m,
        "summary": summary, "ranks": ranks or {}, "is_current": current,
    }


def test_the_new_boards_are_registered_with_friendly_names():
    assert isinstance(REGISTRY.get(RAIDPROG_TYPE), RaidProgressBoard)
    assert isinstance(REGISTRY.get(ARMOURY_TYPE), ArmouryBoard)
    assert isinstance(REGISTRY.get(CLIMB_TYPE), ClimbBoard)
    assert BOARD_NAMES[RAIDPROG_TYPE] == "Raid Progress"
    assert BOARD_NAMES[ARMOURY_TYPE] == "The Armoury"
    assert BOARD_NAMES[CLIMB_TYPE] == "The Climb"


# --- Raid Progress ----------------------------------------------------------


def test_bar_scales_to_the_boss_count():
    assert _bar(3, 8) == "▰▰▰▱▱▱▱▱"
    assert _bar(8, 8) == "▰" * 8
    assert _bar(0, 0) == ""
    assert len(_bar(10, 20)) == 12  # long raids are capped to fit a phone


async def test_raid_progress_posts_nothing_before_a_sync(db):
    assert await RaidProgressBoard().render(db, Config(), {}) is None


async def test_raid_progress_renders_current_raids_and_standings(db):
    await db.replace_guild_raids(
        [
            _raid("main-raid", "The Main Raid", 8, 8, 8, 2, summary="2/8 M",
                  ranks={"mythic": {"world": 5372, "region": 2347, "realm": 17}}),
            _raid("side-raid", "The Side Raid", 1, 0, 1, 0, summary="1/1 H"),
            _raid("old-tier", "The Old Tier", 9, 9, 8, 4, current=False, summary="4/9 M"),
        ]
    )
    payload = await RaidProgressBoard().render(db, Config(), {})
    desc = payload.embed.description

    assert "From the Ashes are **2/8 Mythic** in **The Main Raid**" in desc
    assert "`M` ▰▰▱▱▱▱▱▱ `2/8`" in desc
    assert "`H` ▰▰▰▰▰▰▰▰ `8/8`" in desc
    assert "Mythic standing: realm #17 · EU #2,347 · world #5,372" in desc
    assert "**The Side Raid**" in desc
    # The ended tier collapses into the one-line recap, not its own block.
    assert "Earlier this expansion: The Old Tier 4/9 M" in desc
    assert "**The Old Tier**" not in desc
    assert not brand.has_em_dash(desc)


async def test_raid_progress_headline_for_a_clear_and_for_no_kills(db):
    await db.replace_guild_raids([_raid("main-raid", "The Main Raid", 8, 8, 8, 0)])
    desc = (await RaidProgressBoard().render(db, Config(), {})).embed.description
    assert "**The Main Raid** is cleared on Heroic (8/8)" in desc

    await db.replace_guild_raids([_raid("main-raid", "The Main Raid", 8, 0, 0, 0)])
    desc = (await RaidProgressBoard().render(db, Config(), {})).embed.description
    assert "No bosses down yet" in desc and "standing" not in desc


def test_standing_skips_unranked_and_malformed():
    assert _standing(_raid("r", "R", 8, 8, 0, 0, ranks={"normal": {"world": 0}}), "eu") is None
    assert _standing(_raid("r", "R", 8, 8, 0, 0, ranks={"normal": "x"}), "eu") is None
    only_realm = _raid("r", "R", 8, 8, 0, 0, ranks={"normal": {"realm": 4, "region": 0}})
    assert _standing(only_realm, "eu") == "Normal standing: realm #4"


async def test_raid_progress_fingerprint_changes_only_on_a_real_change(db):
    board = RaidProgressBoard()
    await db.replace_guild_raids([_raid("main-raid", "The Main Raid", 8, 8, 3, 0)])
    first = await board.render(db, Config(), {})
    again = await board.render(db, Config(), {})
    assert first.fingerprint == again.fingerprint

    await db.replace_guild_raids([_raid("main-raid", "The Main Raid", 8, 8, 4, 0)])
    assert (await board.render(db, Config(), {})).fingerprint != first.fingerprint


async def test_an_empty_fetch_never_wipes_stored_raid_progress(db):
    await db.replace_guild_raids([_raid("main-raid", "The Main Raid", 8, 8, 3, 0)])
    await db.replace_guild_raids([])
    assert [r["raid_slug"] for r in await db.guild_raids()] == ["main-raid"]


# --- The Armoury ------------------------------------------------------------


async def _gear(db, name, ilvl, *, seen_days=1.0, **kw):
    await db.upsert_synced(
        Member(name, class_name="Mage", role="DPS", item_level=ilvl,
               last_crawled_at=_ago(seen_days), **kw)
    )


async def test_armoury_posts_nothing_without_recent_gear(db):
    assert await ArmouryBoard().render(db, Config(), {}) is None
    await _gear(db, "Longgone", 700.0, seen_days=400)
    assert await ArmouryBoard().render(db, Config(), {}) is None


async def test_armoury_ranks_by_item_level_and_skips_stale_characters(db):
    await _gear(db, "Sharp", 330.0)
    await _gear(db, "Sturdy", 325.0)
    # A character last seen a year ago still carries an item level from the old
    # scale; it must not top the board (or skew the average).
    await _gear(db, "Relic", 740.0, seen_days=400)

    payload = await ArmouryBoard().render(db, Config(), {})
    desc = payload.embed.description
    rows = [ln for ln in desc.splitlines() if ln.startswith("`")]

    assert "Relic" not in desc
    assert "Sharp" in rows[0] and "`330`" in rows[0]
    assert "Sturdy" in rows[1]
    assert "Best dressed: **Sharp** at `330`" in desc
    assert "average across 2 active characters: `328`" in desc
    assert not brand.has_em_dash(desc)


async def test_armoury_shows_at_most_twenty(db):
    for i in range(25):
        await _gear(db, f"Char{i:02d}", 300.0 + i)
    desc = (await ArmouryBoard().render(db, Config(), {})).embed.description
    assert len([ln for ln in desc.splitlines() if ln.startswith("`")]) == 20
    assert "Char24" in desc and "Char00" not in desc


async def test_armoury_shows_movement_against_the_weekly_baseline(db):
    await _gear(db, "Alpha", 330.0)
    await _gear(db, "Beta", 320.0)
    board = ArmouryBoard()
    first = await board.render(db, Config(), {})
    assert "▲" not in first.embed.description  # the opening post is settled

    await _gear(db, "Beta", 340.0)  # Beta overtakes Alpha
    await _gear(db, "Gamma", 300.0)  # a newcomer
    second = await board.render(db, Config(), {})
    rows = [ln for ln in second.embed.description.splitlines() if ln.startswith("`")]
    assert "Beta" in rows[0] and "▲1" in rows[0]
    assert "Alpha" in rows[1] and "▼1" in rows[1]
    assert "Gamma" in rows[2] and "NEW" in rows[2]
    assert second.fingerprint != first.fingerprint


# --- The Climb --------------------------------------------------------------


async def _level(db, name, level):
    await db.upsert_synced(Member(name, class_name="Druid", level=level))


async def test_climb_posts_nothing_without_level_data(db):
    await db.upsert_synced(Member("Unknown", class_name="Mage"))
    assert await ClimbBoard().render(db, Config(), {}) is None


async def test_climb_lists_levellers_highest_first_with_weekly_gains(db):
    await _level(db, "Sprout", 72)
    await _level(db, "Sapling", 85)
    await _level(db, "Veteran", MAX_LEVEL)
    board = ClimbBoard()
    first = await board.render(db, Config(), {})
    rows = [ln for ln in first.embed.description.splitlines() if ln.startswith("`")]
    assert "**2** adventurers are on the road" in first.embed.description
    assert "Sapling" in rows[0] and "`Lv 85`" in rows[0]
    assert "Sprout" in rows[1]
    assert "Veteran" not in first.embed.description  # already at the cap
    assert "▲" not in first.embed.description and "Fresh at" not in first.embed.description

    await _level(db, "Sprout", 75)  # gained three levels
    await _level(db, "Sapling", MAX_LEVEL)  # reached the cap this week
    await _level(db, "Seed", 12)  # a brand new alt
    second = await board.render(db, Config(), {})
    desc = second.embed.description
    rows = [ln for ln in desc.splitlines() if ln.startswith("`")]
    assert "Sprout" in rows[0] and "▲3" in rows[0]
    assert "Seed" in rows[1] and "NEW" in rows[1]
    assert f"Fresh at {MAX_LEVEL} this week: **Sapling**." in desc
    assert second.fingerprint != first.fingerprint
    assert not brand.has_em_dash(desc)


async def test_climb_has_a_quiet_state_when_everyone_is_capped(db):
    await _level(db, "Veteran", MAX_LEVEL)
    desc = (await ClimbBoard().render(db, Config(), {})).embed.description
    assert f"Everyone is at level {MAX_LEVEL}" in desc


async def test_climb_truncates_a_long_road_and_a_long_roll_of_honour(db):
    for i in range(18):
        await _level(db, f"Climber{i:02d}", 10 + i)
    for i in range(12):
        await _level(db, f"Fresh{i:02d}", MAX_LEVEL - 1)
    board = ClimbBoard()
    await board.render(db, Config(), {})  # seeds the baseline
    for i in range(12):
        await _level(db, f"Fresh{i:02d}", MAX_LEVEL)
    desc = (await board.render(db, Config(), {})).embed.description
    assert "…and 3 more on the way." in desc
    assert "and 2 more." in desc


# --- the shared weekly baseline ---------------------------------------------


async def test_weekly_baseline_seeds_then_rolls_after_a_reset(db):
    cfg = dataclasses.replace(Config(), raid_tz="UTC")
    monday = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

    # First use seeds the baseline with the current values.
    assert await weekly_baseline(db, cfg, "demo", {"a": 1}, now=monday) == {"a": 1}

    # Same week: compares against the seed and leaves it alone.
    assert await weekly_baseline(db, cfg, "demo", {"a": 5}, now=monday) == {"a": 1}
    assert (await db.board_baseline("demo"))[0] == {"a": 1}

    # First render after a later reset still compares against last week's
    # values, then rolls so the next render starts the new week.
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert await weekly_baseline(db, cfg, "demo", {"a": 9}, now=later) == {"a": 1}
    assert (await db.board_baseline("demo"))[0] == {"a": 9}


def test_entity_key_is_case_insensitive():
    assert entity_key(Member("Emberlyn", realm_slug="Hellfire")) == "emberlyn|hellfire|eu"
