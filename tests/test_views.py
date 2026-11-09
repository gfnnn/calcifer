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
