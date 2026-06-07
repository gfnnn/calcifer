"""Targeted M+ score refresh: bounded selection, persistence, board nudge."""

from __future__ import annotations

from ftabot.config import Config
from ftabot.models import Member
from ftabot.scores import refresh_boards, refresh_scores, select_active


def test_select_active_is_raiders_first_then_top_scorers_capped():
    cfg = Config(mplus_feed_member_cap=3)
    members = [
        Member("Raider1", raid_team="Team A", mythic_plus_score=100.0),
        Member("Pusher", mythic_plus_score=3000.0),
        Member("Mid", mythic_plus_score=2000.0),
        Member("Low", mythic_plus_score=500.0),
    ]
    picked = [m.character_name for m in select_active(members, cfg)]
    assert len(picked) == 3
    assert picked[0] == "Raider1"          # raiders always first
    assert "Pusher" in picked              # then the highest scorers
    assert "Low" not in picked             # capped out


def test_select_active_cap_of_zero_picks_nobody():
    # A cap of 0 must mean "refresh nobody", not "one member" (the off-by-one the
    # post-append cap check used to produce).
    cfg = Config(mplus_feed_member_cap=0)
    members = [Member("Raider1", raid_team="Team A"), Member("Pusher", mythic_plus_score=9.0)]
    assert select_active(members, cfg) == []


def test_select_active_keeps_same_name_different_realm():
    # Two characters named "Asha" on different realms are distinct people.
    cfg = Config(mplus_feed_member_cap=5)
    members = [
        Member("Asha", realm_slug="hellfire", region="eu", mythic_plus_score=2000.0),
        Member("Asha", realm_slug="ravencrest", region="eu", mythic_plus_score=1500.0),
    ]
    picked = select_active(members, cfg)
    assert len(picked) == 2


class _FakeRio:
    """Stands in for RaiderIOClient: bumps scores from a class-level table."""

    new_scores: dict[str, float] = {}

    def __init__(self, session=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def enrich_character(self, member):
        if member.character_name in self.new_scores:
            member.mythic_plus_score = self.new_scores[member.character_name]
        return member


async def test_refresh_scores_persists_and_reports_only_changes(db, monkeypatch):
    await db.upsert_synced(Member("Emberlyn", class_name="Mage", role="DPS", mythic_plus_score=3000.0))
    await db.upsert_synced(Member("Ashbringer", class_name="Paladin", role="TANK", mythic_plus_score=2500.0))
    _FakeRio.new_scores = {"Emberlyn": 3300.0}  # only Emberlyn moved
    monkeypatch.setattr("ftabot.scores.RaiderIOClient", _FakeRio)

    members = await db.all_members()
    changed = await refresh_scores(db, Config(), members, session=object())

    assert [m.character_name for m in changed] == ["Emberlyn"]
    # the new score is persisted
    fresh = await db.get_member("Emberlyn", "hellfire", "eu")
    assert fresh.mythic_plus_score == 3300.0
    # the unchanged one is untouched
    same = await db.get_member("Ashbringer", "hellfire", "eu")
    assert same.mythic_plus_score == 2500.0


async def test_refresh_scores_empty_is_noop(db):
    assert await refresh_scores(db, Config(), [], session=object()) == []


async def test_refresh_boards_iterates_enabled_boards(db):
    await db.upsert_synced(Member("Emberlyn", class_name="Mage", role="DPS", mythic_plus_score=3000.0))
    await db.add_board("hot20", "999")
    # posting off -> the board is blocked by the guardrail, but the pass still
    # runs cleanly over it (no bot calls needed when blocked).
    rendered = await refresh_boards(db, Config(), bot=None)
    assert rendered == 1
