from ftabot.config import Config
from ftabot.models import Member
from ftabot.sync import _build_snapshot


def test_build_snapshot_computes_guild_metrics():
    members = [
        Member("A", role="TANK", item_level=630, mythic_plus_score=3000,
               raid_progression="Palace: 8/8 N, 2/8 M",
               last_login="2026-06-01T00:00:00+00:00"),
        Member("B", role="HEALER", item_level=620, mythic_plus_score=2800,
               last_login="2026-06-01T00:00:00+00:00"),
        Member("C", role="DPS", item_level=640, mythic_plus_score=3200),
    ]
    snap = _build_snapshot(members, Config())
    assert snap["member_count"] == 3
    assert snap["avg_item_level"] == (630 + 620 + 640) / 3
    assert snap["progression_summary"] == "Palace: 8/8 N, 2/8 M"
    assert snap["role_counts"] == {"TANK": 1, "HEALER": 1, "DPS": 1}
    assert snap["health_score"] is not None
    assert snap["taken_at"] is None  # filled in by the DB layer


def test_build_snapshot_health_includes_retention_with_prev_count():
    # The stored snapshot must factor the retention component (member-count
    # change vs the previous snapshot) so it is computed on the same basis as the
    # live GM view; otherwise the GM page's health delta compares unlike scores.
    members = [
        Member("A", role="TANK", item_level=630, mythic_plus_score=3000),
        Member("B", role="HEALER", item_level=620, mythic_plus_score=2800),
        Member("C", role="DPS", item_level=640, mythic_plus_score=3200),
    ]
    without = _build_snapshot(members, Config())["health_score"]
    grown = _build_snapshot(members, Config(), prev_member_count=1)["health_score"]
    # Adding the retention component (strong growth here) shifts the weighted score.
    assert grown != without
