"""Tests for the command-cleanup pass: member card, team roster, next-raid pull.

Covers the pure rendering/selection logic added when /member, /roster and the
team-gated commands were reworked, plus the DB round-trips behind them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ftabot import brand, views
from ftabot.config import Config
from ftabot.models import MAX_LEVEL, Member
from ftabot.services.wowaudit import WowAuditClient, select_next_raid
from ftabot.ui import _raid_progress_lines, member_embed

# --- /member card -----------------------------------------------------------


def test_member_card_uses_role_icon_and_drops_race():
    m = Member("Thrall", class_name="Shaman", active_spec="Enhancement",
               role="DPS", race="Orc", item_level=639, mythic_plus_score=2814,
               level=MAX_LEVEL)
    e = member_embed(m)
    assert e.description == "Enhancement Shaman"  # spec + class, not race
    role = next(f for f in e.fields if f.name == "Role")
    assert role.value == brand.icon("dps")
    assert not any(f.name == "Race" for f in e.fields)


def test_member_card_max_level_shows_mplus_not_level():
    m = Member("Capped", class_name="Mage", role="DPS", level=MAX_LEVEL,
               mythic_plus_score=2900)
    e = member_embed(m)
    assert any(f.name == "M+ Score" for f in e.fields)
    assert not any(f.name == "Level" for f in e.fields)


def test_member_card_sub_cap_shows_level_not_mplus():
    m = Member("Lowbie", class_name="Mage", role="DPS", level=72,
               mythic_plus_score=0)
    e = member_embed(m)
    level = next(f for f in e.fields if f.name == "Level")
    assert level.value == "72"
    assert not any(f.name == "M+ Score" for f in e.fields)


def test_member_card_rank_name_overrides_number():
    m = Member("Officer", class_name="Priest", role="HEALER", guild_rank=2,
               level=MAX_LEVEL)
    e = member_embed(m, rank_name="Raider")
    rank = next(f for f in e.fields if f.name == "Guild Rank")
    assert rank.value == "Raider"
    # Without a name it falls back to the number rather than vanishing.
    e2 = member_embed(m)
    assert next(f for f in e2.fields if f.name == "Guild Rank").value == "2"


def test_member_card_shows_all_season_raids():
    prog = {
        "the-voidspire": {"summary": "8/8 M"},
        "dreamrift": {"summary": "6/8 M"},
        "march-of-quel-dorei": {
            "normal_bosses_killed": 8, "heroic_bosses_killed": 4,
            "mythic_bosses_killed": 0, "total_bosses": 8,
        },
    }
    m = Member("Raider", class_name="Warrior", role="TANK", level=MAX_LEVEL,
               details={"raid_progression": prog})
    e = member_embed(m)
    field = next(f for f in e.fields if f.name == "Raid Progress")
    assert "The Voidspire" in field.value
    assert "Dreamrift" in field.value
    assert "March of Quel'Dorei" in field.value
    assert "8/8 N, 4/8 H, 0/8 M" in field.value


def test_raid_progress_lines_skips_non_dict():
    assert _raid_progress_lines({"weird": "not-a-dict"}) == []


# --- /roster grouping -------------------------------------------------------


def test_bucket_for_status_normalises():
    assert views.bucket_for_status("Present") == "Confirmed"
    assert views.bucket_for_status("tentative") == "Tentative"
    assert views.bucket_for_status("Bench") == "Benched"
    assert views.bucket_for_status("Absent") == "Absent"
    assert views.bucket_for_status("something-odd") == "Other"
    assert views.bucket_for_status(None) == "Other"


def test_group_signups_orders_and_enriches():
    members = {"thrall": Member("Thrall", class_name="Shaman", role="DPS")}
    signups = [
        {"name": "Jaina", "status": "absent"},
        {"name": "Thrall", "status": "present"},
        {"name": "", "status": "present"},  # no name -> skipped
    ]
    groups = views.group_signups(signups, members)
    assert list(groups.keys()) == ["Confirmed", "Absent"]  # bucket order
    assert groups["Confirmed"][0]["class"] == "Shaman"
    assert groups["Absent"][0]["name"] == "Jaina"


async def test_roster_board_view(db):
    aid = await db.upsert_synced(Member("Thrall", class_name="Shaman", role="DPS"))
    await db.set_raid_team(aid, "Cinder")
    await db.set_roster_signups(
        {"raid": {"instance": "The Voidspire"},
         "signups": [{"name": "Thrall", "status": "present"}]}
    )
    data = await views.roster_board(db, Config())
    assert data["count"] == 1
    assert "Confirmed" in data["groups"]
    assert data["raid"]["instance"] == "The Voidspire"


# --- DB round-trips ---------------------------------------------------------


async def test_team_roster_only_returns_assigned(db):
    a = await db.upsert_synced(Member("OnTeam"))
    await db.upsert_synced(Member("Bench"))
    await db.set_raid_team(a, "Cinder")
    roster = await db.team_roster()
    assert [m.character_name for m in roster] == ["OnTeam"]


async def test_roster_signups_roundtrip(db):
    assert await db.roster_signups() == {}
    await db.set_roster_signups({"signups": [{"name": "A", "status": "present"}]})
    assert (await db.roster_signups())["signups"][0]["name"] == "A"


# --- WoW Audit next-raid sign-up pull ---------------------------------------


def test_select_next_raid_picks_soonest_future():
    raids = [
        {"id": 1, "date": "2026-06-01"},
        {"id": 2, "date": "2026-06-20"},
        {"id": 3, "date": "2026-06-14"},
    ]
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    chosen = select_next_raid(raids, now, timezone.utc)
    assert chosen is not None and chosen[0]["id"] == 3
    # No future raids -> None.
    assert select_next_raid([{"id": 1, "date": "2026-06-01"}], now, timezone.utc) is None


class _FakeWA(WowAuditClient):
    def __init__(self, responses):
        super().__init__("dummy-key", default_region="eu", default_realm="hellfire")
        self._responses = responses

    async def _get(self, path, params=None):
        return self._responses[path]


async def test_upcoming_raid_signups_returns_next_raid_with_statuses():
    responses = {
        "raids": {"raids": [
            {"id": 1, "date": "2026-06-01", "start_time": "20:00", "instance": "Old"},
            {"id": 2, "date": "2026-06-20", "start_time": "20:00",
             "instance": "The Voidspire", "difficulty": "Mythic"},
        ]},
        "raids/2": {"signups": [
            {"character": {"name": "Thrall"}, "status": "Present"},
            {"character": {"name": "Jaina"}, "status": "Bench"},
        ]},
    }
    wa = _FakeWA(responses)
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    out = await wa.upcoming_raid_signups(now=now)
    assert out["instance"] == "The Voidspire"
    assert out["difficulty"] == "Mythic"
    assert out["signups"] == [("Thrall", "present"), ("Jaina", "bench")]


async def test_upcoming_raid_signups_none_when_nothing_scheduled():
    wa = _FakeWA({"raids": {"raids": [{"id": 1, "date": "2026-06-01"}]}})
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert await wa.upcoming_raid_signups(now=now) is None
