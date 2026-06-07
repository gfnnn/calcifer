from datetime import datetime, timedelta, timezone

from ftabot.models import Member
from ftabot.sync import _select_for_enrichment

NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _existing(name, *, hours_ago, ilvl=630):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return Member(name, item_level=ilvl, last_synced=ts)


def test_fresh_members_are_skipped():
    members = [Member("Fresh"), Member("Stale")]
    existing = {
        "fresh": _existing("Fresh", hours_ago=2),
        "stale": _existing("Stale", hours_ago=48),
    }
    picked = _select_for_enrichment(
        members, existing, set(), max_age_hours=12, limit=0, now=NOW
    )
    assert [m.character_name for m in picked] == ["Stale"]


def test_never_synced_and_no_ilvl_are_enriched():
    members = [Member("New"), Member("NoIlvl")]
    existing = {
        # exists but never got an item level -> still needs enrichment
        "noilvl": Member("NoIlvl", item_level=None,
                         last_synced=NOW.isoformat()),
    }
    picked = _select_for_enrichment(
        members, existing, set(), max_age_hours=12, limit=0, now=NOW
    )
    assert {m.character_name for m in picked} == {"New", "NoIlvl"}


def test_raiders_prioritised_when_stale():
    members = [Member("Alt"), Member("Raider")]
    existing = {
        "alt": _existing("Alt", hours_ago=48),
        "raider": _existing("Raider", hours_ago=48),
    }
    picked = _select_for_enrichment(
        members, existing, {"raider"}, max_age_hours=12, limit=0, now=NOW
    )
    # Both stale -> both enriched, raider first.
    assert [m.character_name for m in picked] == ["Raider", "Alt"]


def test_fresh_raider_is_still_skipped():
    members = [Member("Raider")]
    existing = {"raider": _existing("Raider", hours_ago=1)}  # fresh
    picked = _select_for_enrichment(
        members, existing, {"raider"}, max_age_hours=12, limit=0, now=NOW
    )
    assert picked == []  # freshness applies to everyone, even raiders


def test_limit_caps_non_raiders_oldest_first():
    members = [Member("R"), Member("A"), Member("B"), Member("C")]
    existing = {
        "a": _existing("A", hours_ago=100),
        "b": _existing("B", hours_ago=50),
        "c": _existing("C", hours_ago=200),
    }
    picked = _select_for_enrichment(
        members, existing, {"r"}, max_age_hours=12, limit=2, now=NOW
    )
    names = [m.character_name for m in picked]
    assert names[0] == "R"  # raider first, never counted against the cap
    # Oldest-first among others, capped at 2: C (200h) then A (100h); B dropped.
    assert names[1:] == ["C", "A"]


def test_max_age_zero_enriches_everyone():
    members = [Member("X"), Member("Y")]
    existing = {"x": _existing("X", hours_ago=1), "y": _existing("Y", hours_ago=1)}
    picked = _select_for_enrichment(
        members, existing, set(), max_age_hours=0, limit=0, now=NOW
    )
    assert {m.character_name for m in picked} == {"X", "Y"}
