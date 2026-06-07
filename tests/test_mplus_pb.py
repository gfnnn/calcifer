"""M+ personal-best gating and the quiet log lane."""

from __future__ import annotations

from ftabot.feeds.raiderio_mplus import _log_event, _run_event, _select_members
from ftabot.models import Member


def _member(name="Stabby", *, realm="hellfire", region="eu", score=None, team=None):
    return Member(
        name,
        class_name="Rogue",
        role="DPS",
        realm_slug=realm,
        region=region,
        mythic_plus_score=score,
        raid_team=team,
    )


def _run(level, upgrades=1, url=None, dungeon="Temple"):
    return {
        "level": level,
        "upgrades": upgrades,
        "dungeon": dungeon,
        "url": url or f"https://raider.io/run/{level}",
        "completed_at": "2026-06-01T20:00:00.000Z",
    }


# ---------------------------------------------------------------------------
# _run_event helpers
# ---------------------------------------------------------------------------

def test_run_event_normal_title():
    m = _member()
    e = _run_event(m, _run(20))
    assert "timed" in e.title
    assert "new PB" not in e.title
    assert e.event_type == "raiderio.mplus-run"


def test_run_event_pb_title():
    m = _member()
    e = _run_event(m, _run(22), is_pb=True)
    assert "new PB" in e.title
    assert "+22" in e.title
    assert e.extra["is_pb"] is True


def test_log_event_lane():
    m = _member()
    e = _log_event(m, _run(10))
    assert "📊" in e.title
    assert e.event_type == "raiderio.mplus-log"
    assert "+10" in e.title


# ---------------------------------------------------------------------------
# _select_members: raiders-first ordering
# ---------------------------------------------------------------------------

def test_select_members_raiders_first():
    raider = _member("R", score=3000, team="Red")
    pug = _member("P", score=5000, team=None)
    picked = _select_members([raider, pug], cap=2)
    assert picked[0].character_name == "R"


def test_select_members_deduplicates():
    m = _member("X", score=2000)
    picked = _select_members([m, m], cap=10)
    assert len(picked) == 1


def test_select_members_respects_cap():
    members = [_member(f"M{i}", score=i) for i in range(20)]
    assert len(_select_members(members, cap=5)) == 5


# ---------------------------------------------------------------------------
# DB: mplus_bests
# ---------------------------------------------------------------------------

async def test_get_mplus_best_returns_zero_when_unseen(db):
    assert await db.get_mplus_best("Nobody", "hellfire", "eu") == 0


async def test_set_and_get_mplus_best(db):
    await db.set_mplus_best("Stabby", "hellfire", "eu", 22)
    assert await db.get_mplus_best("Stabby", "hellfire", "eu") == 22


async def test_mplus_best_only_advances(db):
    await db.set_mplus_best("Stabby", "hellfire", "eu", 22)
    await db.set_mplus_best("Stabby", "hellfire", "eu", 18)  # lower → ignored
    assert await db.get_mplus_best("Stabby", "hellfire", "eu") == 22


async def test_mplus_best_advances_on_new_high(db):
    await db.set_mplus_best("Stabby", "hellfire", "eu", 22)
    await db.set_mplus_best("Stabby", "hellfire", "eu", 25)
    assert await db.get_mplus_best("Stabby", "hellfire", "eu") == 25


async def test_mplus_best_case_insensitive_lookup(db):
    await db.set_mplus_best("Stabby", "hellfire", "eu", 20)
    assert await db.get_mplus_best("stabby", "Hellfire", "EU") == 20
