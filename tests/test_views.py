from datetime import datetime, timezone

from ftabot import views
from ftabot.config import Config
from ftabot.models import LootEntry, Member, WishlistItem

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
CFG = Config()


async def _seed(db):
    await db.upsert_synced(
        Member("Gm", guild_rank=0, class_name="Warrior", role="TANK",
               item_level=640, best_parse_avg=90, missing_enchants=0,
               missing_gems=0, vault_slots_filled=9, wishlist_pct=80,
               last_login="2026-05-31T00:00:00+00:00", mythic_plus_score=3000)
    )
    await db.upsert_synced(
        Member("Healer", guild_rank=4, class_name="Priest", role="HEALER",
               item_level=632, best_parse_avg=70,
               last_login="2026-05-30T00:00:00+00:00")
    )
    await db.upsert_synced(
        Member("Slacker", guild_rank=5, class_name="Mage", role="DPS",
               item_level=610, best_parse_avg=20, missing_enchants=3,
               last_login="2025-01-01T00:00:00+00:00")
    )


def test_days_since_handles_none_naive_and_bad_input():
    # None -> None (no timestamp at all).
    assert views._days_since(None, NOW) is None
    # An unparseable timestamp degrades to None rather than raising.
    assert views._days_since("not-a-date", NOW) is None
    # A naive timestamp (no tz) is read as UTC, so the day delta is computed.
    days = views._days_since("2026-05-30T00:00:00", NOW)  # 2 days before NOW
    assert days == 2.0


async def test_gm_report(db):
    await _seed(db)
    r = await views.gm_report(db, CFG, now=NOW)
    assert r["counts"]["total"] == 3
    assert r["counts"]["active"] == 2
    assert r["counts"]["inactive"] == 1
    assert r["health"]["score"] is not None
    # Gm is rank 0 -> officer.
    assert any(o["name"] == "Gm" for o in r["officers"])
    assert all(o["name"] != "Slacker" for o in r["officers"])
    # Mark of the Wild (Druid) should be flagged missing.
    assert any("Versatility" in b for b in r["missing_buffs"])


async def test_raid_board_sorted_and_flags(db):
    await _seed(db)
    r = await views.raid_board(db, CFG, now=NOW)
    names = [row["name"] for row in r["rows"]]
    assert names[0] == "Gm"  # strongest readiness on top
    slacker = next(row for row in r["rows"] if row["name"] == "Slacker")
    assert "🔴 inactive" in slacker["flags"]
    assert "🔴 missing enchants" in slacker["flags"]


async def test_member_detail(db):
    await _seed(db)
    await db.add_loot([LootEntry("Gm", item_id=5, awarded_at="2026-05-01T20:00:00+00:00")])
    await db.replace_wishlist(
        "Gm", [WishlistItem("Gm", item_id=7, item_name="Axe", is_bis=True, obtained=False)]
    )
    d = await views.member_detail(db, CFG, "Gm", now=NOW)
    assert d is not None
    assert d["member"]["character_name"] == "Gm"
    assert d["readiness"]["score"] is not None
    assert d["wishlist"]["total"] == 1
    assert len(d["loot"]) == 1
    assert d["active"] is True

    assert await views.member_detail(db, CFG, "Ghost") is None


async def test_loot_overview_and_priority(db):
    await _seed(db)
    await db.add_loot(
        [
            LootEntry("Gm", item_id=1, awarded_at="2026-05-01T20:00:00+00:00"),
            LootEntry("Gm", item_id=2, awarded_at="2026-05-08T20:00:00+00:00"),
            LootEntry("Healer", item_id=3, awarded_at="2026-05-08T20:00:00+00:00"),
        ]
    )
    overview = await views.loot_overview(db, CFG, now=NOW)
    assert overview["total_awards"] == 3
    top = overview["fairness"][0]
    assert top["name"] == "Gm" and top["items_won"] == 2

    await db.replace_wishlist(
        "Healer",
        [WishlistItem("Healer", item_id=99, item_name="Shiny Trinket",
                      is_bis=True, upgrade_pct=6)],
    )
    await db.replace_wishlist(
        "Slacker",
        [WishlistItem("Slacker", item_id=99, item_name="Shiny Trinket",
                      is_bis=False, upgrade_pct=2)],
    )
    pr = await views.loot_priority(db, CFG, item_name="Shiny", now=NOW)
    chars = [c["character"] for c in pr["ranked"]]
    assert set(chars) == {"Healer", "Slacker"}
    assert chars[0] == "Healer"  # BiS + bigger upgrade ranks first


# --- Home: pulse + triage (Phase 1) -----------------------------------------

def test_sparkline_points_geometry():
    assert views._sparkline_points([]) == ""
    assert views._sparkline_points([5]) == ""  # need >= 2 known points
    pts = views._sparkline_points([0, 10], width=100, height=40).split(" ")
    assert len(pts) == 2
    assert pts[0].startswith("0.0,") and pts[1].startswith("100.0,")
    # None values are skipped but still consume their x-slot.
    assert len(views._sparkline_points([0, None, 10], width=100).split(" ")) == 2


def _attn_kwargs(**over):
    base = dict(
        inactive_raiders=[], unprepped_raiders=[], low_readiness=[],
        gm={"officers": [{"name": "Ok", "active": True}]},
        roster={"groups": {}}, boards={"boards": []},
        feeds={"pending_outbox": 0, "pending_digests": 0, "muted_sources": []},
        pages={"pages": [{"title": "Rules", "placed": True}]},
        recruiting={"config": {}}, leads=[], snapshot_age_days=0.5,
        sync=None, now=NOW,
    )
    base.update(over)
    return base


def test_attention_all_clear_is_empty():
    assert views.attention(**_attn_kwargs()) == []


def test_attention_fires_and_ranks_signals():
    items = views.attention(**_attn_kwargs(
        inactive_raiders=["A", "B"],
        unprepped_raiders=["A"],
        gm={"officers": [{"name": "Sleepy", "active": False},
                         {"name": "Awake", "active": True}]},
        roster={"groups": {"Tentative": [{"name": "Maybe"}]}},
        boards={"boards": [
            {"name": "Hot 20", "enabled": True, "promoted": True,
             "posted": False, "effective_destination": "123"},
            {"name": "News", "enabled": True, "promoted": False,
             "posted": True, "effective_destination": None},
        ]},
        feeds={"pending_outbox": 2, "pending_digests": 1, "muted_sources": ["wowhead"]},
        pages={"pages": [{"title": "Rules", "placed": False},
                         {"title": "Guides", "placed": True}]},
        recruiting={"config": {"slots": {"TANK": {"open": 1}, "HEALER": {"open": 0}}}},
        leads=[
            {"name": "Cold", "state": "new", "updated_at": "2026-05-01T00:00:00+00:00"},
            {"name": "Fresh", "state": "new", "updated_at": "2026-05-30T00:00:00+00:00"},
            {"name": "Done", "state": "contacted", "updated_at": "2026-01-01T00:00:00+00:00"},
        ],
        snapshot_age_days=5.0,
    ))
    by = {it["kind"]: it for it in items}
    assert by["inactive_raiders"]["count"] == 2
    assert by["unprepped_raiders"]["count"] == 1
    assert by["pending_signups"]["severity"] == "low"
    assert by["cold_leads"]["count"] == 1 and "Cold" in by["cold_leads"]["detail"]
    assert by["open_slots"]["count"] == 1  # only TANK has open > 0
    assert by["board_issues"]["count"] == 2 and by["board_issues"]["severity"] == "high"
    assert by["feed_backlog"]["count"] == 3 and by["feed_backlog"]["severity"] == "high"
    assert "feed_muted" in by and by["feed_muted"]["severity"] == "low"
    assert by["pages_unplaced"]["count"] == 1
    assert by["sync_stale"]["severity"] == "high"  # 5d > 3d
    assert by["officer_inactive"]["count"] == 1
    assert "low_readiness" not in by  # empty list -> no item
    # Ordered high -> low severity.
    ranks = [views._SEVERITY_RANK[it["severity"]] for it in items]
    assert ranks == sorted(ranks, reverse=True)


def test_attention_sync_stale_severity_and_fallback():
    med = views.attention(**_attn_kwargs(snapshot_age_days=2.0))
    assert any(it["kind"] == "sync_stale" and it["severity"] == "med" for it in med)
    # No snapshot yet -> fall back to the live sync's last_finished age.
    fb = views.attention(**_attn_kwargs(
        snapshot_age_days=None,
        sync={"last_finished": "2026-05-20T00:00:00+00:00"},
    ))
    assert any(it["kind"] == "sync_stale" for it in fb)
    # Fresh sync -> silent.
    assert not any(it["kind"] == "sync_stale"
                   for it in views.attention(**_attn_kwargs(snapshot_age_days=0.2)))


async def test_home_shape(db):
    await _seed(db)
    r = await views.home(db, CFG, now=NOW)
    assert r["guild"] == CFG.guild_name
    pulse = r["pulse"]
    assert "health" in pulse and "counts" in pulse
    assert "sparklines" in pulse["trends"]
    assert isinstance(r["attention"], list)
    assert r["attention_total"] == sum(it["count"] for it in r["attention"])
    assert r["generated_at"]


async def test_home_surfaces_raider_signals(db):
    mid = await db.upsert_synced(
        Member("Ghost", class_name="Mage", role="DPS", item_level=600,
               missing_enchants=2, missing_gems=0, best_parse_avg=10,
               last_login="2025-01-01T00:00:00+00:00")
    )
    await db.set_raid_team(mid, "Cinder")
    r = await views.home(db, CFG, now=NOW)
    kinds = {it["kind"] for it in r["attention"]}
    assert "inactive_raiders" in kinds   # last_login is ancient
    assert "unprepped_raiders" in kinds  # missing_enchants=2
    assert "low_readiness" in kinds      # weak gear/parse/inactive


# --- Deeper analytics view-models -------------------------------------------

async def test_attendance_report_trend_and_order(db):
    e1 = await db.add_raid_event(started_at="2026-05-01", instance="X", difficulty="Mythic")
    e2 = await db.add_raid_event(started_at="2026-05-08", instance="X", difficulty="Mythic")
    await db.add_attendance(e1, "Gm", status="present")
    await db.add_attendance(e2, "Gm", status="present")
    await db.add_attendance(e1, "Slacker", status="present")
    await db.add_attendance(e2, "Slacker", status="absent")
    r = await views.attendance_report(db, CFG, recent_window=1)
    assert r["event_count"] == 2
    chars = {c["name"]: c for c in r["characters"]}
    assert chars["Gm"]["pct"] == 100.0
    assert chars["Slacker"]["pct"] == 50.0
    assert r["characters"][0]["name"] == "Slacker"          # worst first
    assert chars["Slacker"]["trend"] == "down"              # recent (e2 absent) < overall
    assert chars["Gm"]["trend"] == "steady"
    assert [t["present"] for t in r["turnout"]] == [2, 1]   # oldest -> newest


async def test_loot_analytics_breakdown_and_avg(db):
    await db.upsert_synced(Member("Gm", role="TANK", class_name="Warrior"))
    await db.upsert_synced(Member("Slacker", role="DPS", class_name="Mage"))
    await db.add_loot([
        LootEntry(character_name="Gm", item_id=1, item_name="A", slot="head",
                  difficulty="Mythic", response="BiS", awarded_at="2026-05-01T00:00:00"),
        LootEntry(character_name="Gm", item_id=2, item_name="B", slot="hands",
                  difficulty="Heroic", response="Upgrade", awarded_at="2026-05-02T00:00:00"),
    ])
    r = await views.loot_analytics(db, CFG, now=NOW)
    assert r["total_awards"] == 2
    assert r["avg_per_player"] == 1.0                       # (2 + 0) / 2
    by_slot = {x["key"]: x["count"] for x in r["breakdown"]["slot"]}
    assert by_slot["head"] == 1 and by_slot["hands"] == 1
    assert any(d["name"] == "Gm" for d in r["drought"])     # only one with loot


async def test_wishlist_analytics_contention_and_coverage(db):
    await db.replace_wishlist("Gm", [
        WishlistItem(character_name="Gm", item_id=10, item_name="Sword", slot="weapon",
                     difficulty="Mythic", upgrade_pct=12.0, is_bis=True, obtained=False),
    ])
    await db.replace_wishlist("Slacker", [
        WishlistItem(character_name="Slacker", item_id=10, item_name="Sword", slot="weapon",
                     difficulty="Mythic", upgrade_pct=8.0, is_bis=False, obtained=True),
    ])
    r = await views.wishlist_analytics(db, CFG)
    c = {x["item_id"]: x for x in r["contention"]}[10]
    assert c["wishlisters"] == 2 and c["bis"] == 1 and c["obtained"] == 1 and c["open"] == 1
    assert c["avg_upgrade"] == 10.0
    cov = {x["name"]: x for x in r["coverage"]}
    assert cov["Gm"]["pct"] == 0.0 and cov["Slacker"]["pct"] == 100.0
    assert r["coverage"][0]["name"] == "Gm"                 # worst coverage first


async def test_recruitment_funnel_counts_and_close_rate(db):
    for i, st in enumerate(["new", "contacted", "trialing", "joined", "passed", "passed"]):
        lid = await db.upsert_lead({"name": f"L{i}", "realm": "r", "region": "eu",
                                    "class_name": "Mage", "source": "wcl"})
        if st != "new":
            await db.set_lead_state(lid, st)
    r = await views.recruitment_funnel(db, CFG)
    counts = {s["state"]: s["count"] for s in r["stages"]}
    assert counts == {"new": 1, "contacted": 1, "trialing": 1, "joined": 1, "passed": 2}
    assert r["total"] == 6 and r["active"] == 3
    assert r["joined"] == 1 and r["passed"] == 2
    assert r["close_rate"] == 33.3                          # 1 / (1 + 2)
    assert r["by_class"]["Mage"] == 6 and r["by_source"]["wcl"] == 6


async def test_member_row_carries_class_and_score_colours(db):
    from ftabot import brand
    from ftabot.models import Member

    await db.upsert_synced(
        Member("Colourful", class_name="Druid", role="DPS", mythic_plus_score=4300.0)
    )
    m = await db.get_member("Colourful", "hellfire", "eu")
    row = views.member_row(m)
    assert row["class_color"] == "#ff7c0a"  # Druid orange
    assert row["mythic_plus_color"] == f"#{brand.score_color(4300):06x}"
    # a member with no score/class gets no colour (the JS then shows the default)
    await db.upsert_synced(Member("Blank"))
    blank = views.member_row(await db.get_member("Blank", "hellfire", "eu"))
    assert blank["class_color"] is None and blank["mythic_plus_color"] is None
