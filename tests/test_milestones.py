"""Guild milestones: pure detection, the DB-backed collect, the feed source and
the end-to-end path through the runner (dedup, priming, guardrail hold-back)."""

from __future__ import annotations

import dataclasses

import aiosqlite

from ftabot import brand, metrics, milestones, persona
from ftabot.config import Config
from ftabot.db import Database
from ftabot.feeds.milestones import SOURCE_NAME, MilestonesSource, to_event
from ftabot.feeds.runner import build_sources, poll_once
from ftabot.migrations import MIGRATIONS
from ftabot.models import MAX_LEVEL, Member
from ftabot.posting import PostBlocked

from .test_feeds import _Recorder


def _raid(heroic=0, mythic=0, *, slug="main-raid", current=True, total=8):
    return {
        "raid_slug": slug, "name": "The Main Raid", "total_bosses": total,
        "normal_killed": total, "heroic_killed": heroic, "mythic_killed": mythic,
        "summary": None, "ranks": {}, "is_current": current,
    }


def _kinds(found):
    return [(m["kind"], m["subject"], m["value"]) for m in found]


# --- pure detection ---------------------------------------------------------


def test_mplus_tier_is_the_highest_threshold_reached():
    assert milestones.mplus_tier(None) == 0
    assert milestones.mplus_tier(1499.9) == 0
    assert milestones.mplus_tier(1500) == 1500
    assert milestones.mplus_tier(2999) == 2500
    assert milestones.mplus_tier(3020.4) == 3000


def test_the_first_run_records_everything_and_announces_nothing():
    members = [Member("Emberlyn", mythic_plus_score=3100.0, level=MAX_LEVEL)]
    found, marks = milestones.detect({}, members, [_raid(heroic=8, mythic=2)])
    assert found == []
    assert marks[("emberlyn|hellfire|eu", "mplus")] == 3100.0
    assert marks[("emberlyn|hellfire|eu", "level")] == MAX_LEVEL
    assert marks[("guild", "raid:main-raid:mythic")] == 2


def test_a_score_crossing_a_threshold_is_a_milestone():
    _, marks = milestones.detect({}, [Member("Emberlyn", mythic_plus_score=2480.0)], [])
    found, _ = milestones.detect(marks, [Member("Emberlyn", mythic_plus_score=2512.0)], [])
    assert _kinds(found) == [("mplus", "Emberlyn", 2500)]
    assert found[0]["detail"]["score"] == 2512.0


def test_a_score_moving_within_a_tier_or_dropping_is_not_a_milestone():
    _, marks = milestones.detect({}, [Member("Emberlyn", mythic_plus_score=2510.0)], [])
    found, marks = milestones.detect(marks, [Member("Emberlyn", mythic_plus_score=2890.0)], [])
    assert found == []
    # A new season resets scores; the mark follows it down so the re-climb counts.
    found, marks = milestones.detect(marks, [Member("Emberlyn", mythic_plus_score=0.0)], [])
    assert found == []
    found, _ = milestones.detect(marks, [Member("Emberlyn", mythic_plus_score=1600.0)], [])
    assert _kinds(found) == [("mplus", "Emberlyn", 1500)]


def test_a_new_member_already_past_a_threshold_is_not_announced():
    # The roster has marks, then somebody joins at 3,000: no mark, no alert.
    _, marks = milestones.detect({}, [Member("Veteran", mythic_plus_score=2000.0)], [])
    found, _ = milestones.detect(
        marks,
        [Member("Veteran", mythic_plus_score=2000.0),
         Member("Newcomer", mythic_plus_score=3000.0, level=MAX_LEVEL)],
        [],
    )
    assert found == []


def test_reaching_the_level_cap_is_a_milestone_once():
    _, marks = milestones.detect({}, [Member("Sprout", level=MAX_LEVEL - 1)], [])
    found, marks = milestones.detect(marks, [Member("Sprout", level=MAX_LEVEL)], [])
    assert _kinds(found) == [("level_cap", "Sprout", MAX_LEVEL)]
    found, _ = milestones.detect(marks, [Member("Sprout", level=MAX_LEVEL)], [])
    assert found == []


def test_a_level_up_below_the_cap_is_not_a_milestone():
    _, marks = milestones.detect({}, [Member("Sprout", level=40)], [])
    found, _ = milestones.detect(marks, [Member("Sprout", level=41)], [])
    assert found == []


def test_a_new_boss_kill_is_a_milestone_per_difficulty():
    _, marks = milestones.detect({}, [], [_raid(heroic=8, mythic=2)])
    found, _ = milestones.detect(marks, [], [_raid(heroic=8, mythic=3)])
    assert _kinds(found) == [("raid_kill", "The Main Raid", 3)]
    assert found[0]["detail"] == {
        "raid_slug": "main-raid", "difficulty": "mythic", "total_bosses": 8,
    }


def test_normal_kills_and_retired_raids_are_ignored():
    _, marks = milestones.detect({}, [], [_raid(heroic=1)])
    old = _raid(heroic=8, mythic=8, slug="old-tier", current=False)
    found, marks = milestones.detect(marks, [], [_raid(heroic=1), old])
    assert found == []
    assert ("guild", "raid:old-tier:mythic") not in marks


def test_the_first_kill_in_a_newly_opened_raid_is_announced():
    # Primed on one raid; a new raid then opens and the guild kills a boss
    # before the next poll. Its kills count from zero, so it is announced.
    _, marks = milestones.detect({}, [], [_raid(heroic=8)])
    new_raid = {**_raid(heroic=2, slug="new-raid"), "name": "The New Raid"}
    found, _ = milestones.detect(marks, [], [_raid(heroic=8), new_raid])
    assert _kinds(found) == [("raid_kill", "The New Raid", 2)]


# --- collect (DB-backed) ----------------------------------------------------


async def test_collect_detects_once_and_persists(db):
    await db.upsert_synced(Member("Emberlyn", mythic_plus_score=2480.0))
    await db.replace_guild_raids([_raid(heroic=3)])
    assert await milestones.collect(db) == 0  # first run: marks only

    await db.upsert_synced(Member("Emberlyn", mythic_plus_score=2550.0))
    await db.replace_guild_raids([_raid(heroic=4)])
    assert await milestones.collect(db) == 2
    assert await milestones.collect(db) == 0  # marks advanced: detected exactly once

    stored = await db.recent_milestones(days=7)
    assert {m["kind"] for m in stored} == {"mplus", "raid_kill"}
    assert all(m["achieved_at"] for m in stored)


async def test_old_milestones_are_pruned(db):
    await db.add_milestones([{"kind": "mplus", "entity_key": "e", "subject": "Old", "value": 1500}])
    await db.conn.execute("UPDATE milestones SET achieved_at = '2020-01-01T00:00:00+00:00'")
    await db.conn.commit()
    assert await db.recent_milestones(days=7) == []
    await db.add_milestones([{"kind": "mplus", "entity_key": "e", "subject": "New", "value": 2000}])
    cur = await db.conn.execute("SELECT subject FROM milestones")
    assert [r["subject"] for r in await cur.fetchall()] == ["New"]


async def test_setting_no_marks_is_a_no_op(db):
    await db.set_milestone_marks({})
    assert await db.milestone_marks() == {}


# --- event rendering --------------------------------------------------------


def _row(kind, subject, value, **detail):
    return {
        "id": 7, "kind": kind, "entity_key": "e", "subject": subject, "value": value,
        "detail": detail, "achieved_at": "2026-11-13T20:00:00+00:00",
    }


def test_a_boss_kill_event():
    event = to_event(
        _row("raid_kill", "The Main Raid", 5, difficulty="mythic", total_bosses=8),
        "From the Ashes",
    )
    assert event.source == SOURCE_NAME and event.external_id == "milestone-7"
    assert event.event_type == "milestone.raid-kill"
    assert event.title == "🔥 Boss down: 5/8 Mythic in The Main Raid"
    assert "From the Ashes just pushed further" in event.summary
    assert event.severity == "warning"


def test_a_raid_clear_event():
    event = to_event(
        _row("raid_kill", "The Main Raid", 8, difficulty="heroic", total_bosses=8),
        "From the Ashes",
    )
    assert event.title == "🏆 The Main Raid cleared on Heroic (8/8)"


def test_a_mythic_plus_event_links_the_profile():
    event = to_event(
        _row("mplus", "Emberlyn", 2500, score=2512.4, profile_url="https://raider.io/x"),
        "From the Ashes",
    )
    assert event.event_type == "milestone.mplus"
    assert event.title == "⚡ Emberlyn broke 2,500 Mythic+ score"
    assert event.summary.startswith("Now sitting at 2,512.")
    assert event.url == "https://raider.io/x"


def test_a_level_cap_event_and_an_unknown_kind():
    event = to_event(_row("level_cap", "Sprout", MAX_LEVEL), "From the Ashes")
    assert event.event_type == "milestone.level-cap"
    assert event.title == f"🎉 Sprout reached level {MAX_LEVEL}"
    assert to_event(_row("mystery", "X", 1), "From the Ashes") is None


def test_milestone_copy_keeps_the_house_style():
    rows = [
        _row("raid_kill", "The Main Raid", 5, difficulty="mythic", total_bosses=8),
        _row("raid_kill", "The Main Raid", 8, difficulty="heroic", total_bosses=8),
        _row("mplus", "Emberlyn", 2500, score=2512.4),
        _row("level_cap", "Sprout", MAX_LEVEL),
    ]
    for row in rows:
        event = to_event(row, "From the Ashes")
        assert not brand.has_em_dash(event.title) and not brand.has_em_dash(event.summary)
    for event_type in ("milestone.raid-kill", "milestone.mplus", "milestone.level-cap"):
        assert persona._LINES[event_type]  # each has its own flavour lines
        assert not any(brand.has_em_dash(line) for line in persona._LINES[event_type])


# --- the feed, end to end through the runner --------------------------------


def _cfg(db, **kw):
    return dataclasses.replace(
        Config(), database_path=db.path, feed_default_channel_id="42",
        persona_tone="off", **kw,
    )


def test_the_feed_is_off_by_default_and_on_with_its_flag():
    assert not any(s.name == SOURCE_NAME for s in build_sources(Config()))
    on = dataclasses.replace(Config(), milestones_feed_enabled=True)
    assert any(isinstance(s, MilestonesSource) for s in build_sources(on))


async def test_milestones_post_once_through_the_runner(db):
    cfg = _cfg(db)
    source, send = MilestonesSource(cfg), _Recorder()
    await db.upsert_synced(Member("Emberlyn", mythic_plus_score=2480.0))

    # First poll primes silently (and records the marks).
    assert await poll_once(db, cfg, [source], None, send) == 0

    await db.upsert_synced(Member("Emberlyn", mythic_plus_score=2550.0))
    assert await poll_once(db, cfg, [source], None, send) == 1
    assert send.sent == [("42", "⚡ Emberlyn broke 2,500 Mythic+ score")]

    # Still on offer for a week, but the runner's dedup never reposts it.
    assert await poll_once(db, cfg, [source], None, send) == 0
    assert len(send.sent) == 1


async def test_a_milestone_held_by_the_guardrail_posts_once_it_opens(db):
    cfg = _cfg(db)
    source = MilestonesSource(cfg)
    await db.replace_guild_raids([_raid(heroic=3)])

    async def blocked(channel_id, embed, content=None):
        raise PostBlocked(channel_id)

    await poll_once(db, cfg, [source], None, blocked)  # prime
    await db.replace_guild_raids([_raid(heroic=4)])
    # Detected while posting is off: the marks advance, but the stored
    # milestone stays on offer instead of being lost.
    assert await poll_once(db, cfg, [source], None, blocked) == 0

    send = _Recorder()
    assert await poll_once(db, cfg, [source], None, send) == 1
    assert send.sent == [("42", "🔥 Boss down: 4/8 Heroic in The Main Raid")]


# --- the activity signal + its migration ------------------------------------


def test_is_active_prefers_the_crawl_time_over_the_sync_time():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    just_synced = (now - timedelta(minutes=5)).isoformat()
    long_ago = (now - timedelta(days=200)).isoformat()
    # Synced a moment ago, but not actually seen playing for months.
    dormant = Member("Dormant", last_crawled_at=long_ago, last_synced=just_synced)
    assert metrics.is_active(dormant, window_days=14, now=now) is False
    # A real last login outranks the crawl time.
    back = Member("Back", last_login=just_synced, last_crawled_at=long_ago)
    assert metrics.is_active(back, window_days=14, now=now) is True
    # With neither signal, the sync time is still the last resort.
    legacy = Member("Legacy", last_synced=just_synced)
    assert metrics.is_active(legacy, window_days=14, now=now) is True


async def test_an_established_db_gains_the_crawl_column(tmp_path):
    # Same shape as the wowaudit_team regression: migrations 1-6 already ran on
    # this database before last_crawled_at existed, so migration 7 must add it.
    from ftabot.db_schema import SCHEMA

    old_schema = "\n".join(
        ln for ln in SCHEMA.splitlines() if "last_crawled_at" not in ln
    )
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(old_schema)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)"
        )
        for migration in MIGRATIONS:
            if migration.version <= 6:
                await conn.execute(
                    "INSERT INTO schema_migrations VALUES (?, 'pre-existing', "
                    "'2026-01-01T00:00:00+00:00')",
                    (migration.version,),
                )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_wa_team ON members (wowaudit_team)")
        await conn.commit()

    async with Database(path) as db:
        cur = await db.conn.execute("PRAGMA table_info(members)")
        assert "last_crawled_at" in {r["name"] for r in await cur.fetchall()}
        await db.upsert_synced(Member("Seen", last_crawled_at="2026-11-12T00:00:00Z"))
        member = await db.get_member("Seen", "hellfire", "eu")
        assert member.last_crawled_at == "2026-11-12T00:00:00Z"
        assert member.to_dict()["last_crawled_at"] == "2026-11-12T00:00:00Z"
