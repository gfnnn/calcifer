from datetime import datetime, timedelta, timezone

from ftabot import metrics
from ftabot.models import Member

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def test_percentile_rank():
    vals = [600, 610, 620, 630, 640]
    assert metrics.percentile_rank(vals, 640) == 1.0
    assert metrics.percentile_rank(vals, 600) == 0.2
    assert metrics.percentile_rank(vals, None) == 0.0
    assert metrics.percentile_rank([], 600) == 0.0


def test_is_active_and_rate():
    fresh = Member("Fresh", last_login=_iso(2))
    stale = Member("Stale", last_login=_iso(40))
    unknown = Member("Unknown")
    assert metrics.is_active(fresh, window_days=14, now=NOW) is True
    assert metrics.is_active(stale, window_days=14, now=NOW) is False
    assert metrics.is_active(unknown, window_days=14, now=NOW) is False
    rate = metrics.activity_rate([fresh, stale, unknown], window_days=14, now=NOW)
    assert rate == 1 / 3


def test_prep():
    ready = Member("R", missing_enchants=0, missing_gems=0)
    notready = Member("N", missing_enchants=2, missing_gems=0)
    unknown = Member("U")
    assert metrics.prep_ready(ready) is True
    assert metrics.prep_ready(notready) is False
    assert metrics.prep_ready(unknown) is None
    assert metrics.prep_rate([ready, notready, unknown]) == 0.5  # unknown excluded


def test_role_balance():
    # Perfectly matches default targets (10/20/70).
    members = (
        [Member(f"T{i}", role="TANK") for i in range(1)]
        + [Member(f"H{i}", role="HEALER") for i in range(2)]
        + [Member(f"D{i}", role="DPS") for i in range(7)]
    )
    assert metrics.role_balance_score(members) == 1.0
    # All DPS, no tanks/healers -> poor balance.
    alldps = [Member(f"D{i}", role="DPS") for i in range(10)]
    assert metrics.role_balance_score(alldps) < 0.8
    assert metrics.role_balance_score([]) is None


def test_progression_parsing():
    summary = "Nerub-ar Palace: 8/8 N, 6/8 H, 2/8 M"
    parsed = metrics.parse_progression(summary)
    assert parsed == {"N": (8, 8), "H": (6, 8), "M": (2, 8)}
    # Hardest difficulty with kills is Mythic 2/8 -> 1.0 * 0.25.
    assert metrics.progression_fraction(summary) == 0.25
    assert metrics.progression_fraction("Raid: 8/8 N") == 0.3
    assert metrics.progression_fraction(None) is None


def test_compute_readiness_strong_vs_weak():
    team_ilvls = [620, 625, 630, 635, 640]
    strong = Member(
        "Strong",
        item_level=640,
        best_parse_avg=92,
        missing_enchants=0,
        missing_gems=0,
        vault_slots_filled=9,
        wishlist_pct=80,
        last_login=_iso(1),
    )
    weak = Member(
        "Weak",
        item_level=620,
        best_parse_avg=30,
        missing_enchants=3,
        missing_gems=1,
        last_login=_iso(60),
    )
    rs = metrics.compute_readiness(
        strong, team_item_levels=team_ilvls, attendance_pct=95, now=NOW
    )
    rw = metrics.compute_readiness(
        weak, team_item_levels=team_ilvls, attendance_pct=40, now=NOW
    )
    assert rs.score > rw.score
    assert rs.score > 80
    assert "🔴 missing enchants" in rw.flags
    assert "🟡 low parse" in rw.flags
    assert "⚪ no wishlist" in rw.flags
    assert "🔴 inactive" in rw.flags


def test_guild_health_renormalises_over_known_components():
    members = [
        Member("A", role="TANK", last_login="2026-06-01T00:00:00+00:00",
               raid_progression="Raid: 8/8 N, 2/8 M"),
        Member("B", role="HEALER", last_login="2026-05-30T00:00:00+00:00"),
        Member("C", role="DPS", last_login="2025-01-01T00:00:00+00:00"),
    ]
    health = metrics.guild_health(members, window_days=14, now=NOW)
    assert health.score is not None
    assert "activity" in health.components
    assert "progression" in health.components
    assert 0 <= health.score <= 100


def test_guild_health_no_data():
    assert metrics.guild_health([]).score is None


def test_loot_priority_ranking():
    bis_drought = metrics.LootCandidate(
        "Bisdrought", is_bis=True, upgrade_pct=8, attendance_pct=100,
        days_since_last_loot=30, recent_wins=0,
    )
    hoarder = metrics.LootCandidate(
        "Hoarder", is_bis=False, upgrade_pct=2, attendance_pct=80,
        days_since_last_loot=1, recent_wins=4,
    )
    ranked = metrics.rank_loot_priority([hoarder, bis_drought])
    assert ranked[0][0].character_name == "Bisdrought"
    assert ranked[0][1] > ranked[1][1]


def test_readiness_wishlist_credits_by_fill_not_mere_presence():
    team = [640]
    base = dict(item_level=640, best_parse_avg=90, missing_enchants=0,
                missing_gems=0, vault_slots_filled=9, last_login=_iso(1))
    full = Member("Full", wishlist_pct=100, **base)
    half = Member("Half", wishlist_pct=50, **base)
    empty = Member("Empty", wishlist_pct=0, **base)
    rf = metrics.compute_readiness(full, team_item_levels=team, attendance_pct=100, now=NOW)
    rh = metrics.compute_readiness(half, team_item_levels=team, attendance_pct=100, now=NOW)
    re_ = metrics.compute_readiness(empty, team_item_levels=team, attendance_pct=100, now=NOW)
    assert rf.components["wishlist"] == 1.0
    assert rh.components["wishlist"] == 0.5
    assert re_.components["wishlist"] == 0.0
    assert rf.score > rh.score > re_.score
    # A present-but-empty wishlist isn't flagged as missing.
    assert "⚪ no wishlist" not in re_.flags


def test_is_active_ignores_an_unparseable_timestamp():
    # A malformed last_login is treated as "no signal" (parse returns None),
    # so the member counts as inactive rather than crashing the rollup.
    broken = Member("Broken", last_login="not-a-date")
    assert metrics.is_active(broken, window_days=14, now=NOW) is False


def test_is_active_accepts_a_naive_timestamp_as_utc():
    # A timestamp with no timezone is read as UTC, so a recent naive login
    # still counts as active (exercises the tz-fill branch in _parse_dt).
    naive = Member("Naive", last_login="2026-05-31T00:00:00")  # 1 day before NOW
    assert metrics.is_active(naive, window_days=14, now=NOW) is True


def test_progression_fraction_is_zero_when_parsed_but_no_kills():
    # The summary parses into difficulties but every kill count is 0, so the
    # function falls through the hardest-first loop and returns 0.0 (not None).
    assert metrics.progression_fraction("Raid: 0/8 N, 0/8 H, 0/8 M") == 0.0
