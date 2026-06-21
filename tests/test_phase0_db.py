from ftabot.models import LootEntry, Member, WishlistItem


def _loot(name, item_id, when, **kw):
    return LootEntry(character_name=name, item_id=item_id, awarded_at=when, **kw)


def _wish(name, item_id, **kw):
    return WishlistItem(character_name=name, item_id=item_id, **kw)


async def test_loot_add_dedup_and_counts(db):
    entries = [
        _loot("Ashbringer", 111, "2026-05-01T20:00:00+00:00", response="BiS"),
        _loot("Ashbringer", 222, "2026-05-08T20:00:00+00:00", response="Upgrade"),
        _loot("Emberlyn", 111, "2026-05-08T20:30:00+00:00", response="Mainspec"),
    ]
    added = await db.add_loot(entries)
    assert added == 3
    # Re-adding the same awards is a no-op (dedup on char+item+time).
    again = await db.add_loot(entries)
    assert again == 0

    counts = await db.loot_counts()
    assert counts["ashbringer"] == 2
    assert counts["emberlyn"] == 1

    ash_loot = await db.get_loot(character="Ashbringer")
    assert [e.item_id for e in ash_loot] == [222, 111]  # newest first


async def test_wishlist_replace_and_progress(db):
    items = [
        _wish("Ashbringer", 10, is_bis=True, obtained=True, upgrade_pct=5),
        _wish("Ashbringer", 11, is_bis=True, obtained=False, upgrade_pct=8),
        _wish("Ashbringer", 12, is_bis=False, obtained=False, upgrade_pct=2),
    ]
    written = await db.replace_wishlist("Ashbringer", items)
    assert written == 3

    got, total, pct = await db.wishlist_progress("Ashbringer")
    assert (got, total) == (1, 3)
    assert round(pct, 1) == 33.3

    # Replacing wholesale removes the old rows.
    await db.replace_wishlist("Ashbringer", [_wish("Ashbringer", 99, obtained=True)])
    got, total, _ = await db.wishlist_progress("Ashbringer")
    assert (got, total) == (1, 1)

    wl = await db.get_wishlist("Ashbringer")
    assert [w.item_id for w in wl] == [99]


async def test_attendance_stats(db):
    e1 = await db.add_raid_event(
        started_at="2026-05-01T20:00:00+00:00", instance="Palace", difficulty="Mythic"
    )
    e2 = await db.add_raid_event(
        started_at="2026-05-08T20:00:00+00:00", instance="Palace", difficulty="Mythic"
    )
    # add_raid_event is idempotent on the natural key.
    e2b = await db.add_raid_event(
        started_at="2026-05-08T20:00:00+00:00", instance="Palace", difficulty="Mythic"
    )
    assert e2 == e2b

    await db.add_attendance(e1, "Ashbringer", status="present")
    await db.add_attendance(e2, "Ashbringer", status="absent")
    present, scheduled, pct = await db.attendance_stats("Ashbringer")
    assert (present, scheduled) == (1, 2)
    assert pct == 50.0

    # last_n=1 counts only the most recent event (the absence).
    assert await db.attendance_stats("Ashbringer", last_n=1) == (0, 1, 0.0)
    # last_n=0 means zero events, not "no limit".
    assert await db.attendance_stats("Ashbringer", last_n=0) == (0, 0, None)


async def test_officers_by_rank_and_role(db):
    await db.upsert_synced(Member("Gm", guild_rank=0))
    await db.upsert_synced(Member("Officer", guild_rank=2))
    await db.upsert_synced(Member("Raider", guild_rank=5))
    tagged_id = await db.upsert_synced(Member("Helper", guild_rank=6))
    await db.set_officer_role(tagged_id, "Recruitment")

    officers = await db.officers(rank_max=2)
    names = {o.character_name for o in officers}
    assert names == {"Gm", "Officer", "Helper"}  # Raider excluded


async def test_wowaudit_team_drives_effective_team(db):
    # A sync-tagged wowaudit_team makes a character "on the team" without any
    # manual assignment, and a manual raid_team override wins for grouping.
    await db.upsert_synced(Member("Autotagged", wowaudit_team="Progression"))
    manual = await db.upsert_synced(Member("Overridden", wowaudit_team="Progression"))
    await db.set_raid_team(manual, "Bench")

    # team_roster (effective team) includes the auto-tagged character.
    assert {m.character_name for m in await db.team_roster()} == {
        "Autotagged", "Overridden"
    }
    # Grouping uses the effective team: auto under its WoW Audit team, the
    # overridden one under the manual team instead.
    assert [m.character_name for m in await db.team_members("Progression")] == [
        "Autotagged"
    ]
    assert [m.character_name for m in await db.team_members("Bench")] == ["Overridden"]
    m = await db.get_member("Overridden", "hellfire", "eu")
    assert m.effective_team == "Bench" and m.wowaudit_team == "Progression"


async def test_set_raid_team_and_snapshot(db):
    mid = await db.upsert_synced(Member("Ashbringer"))
    await db.set_raid_team(mid, "Progression")
    team = await db.team_members("progression")
    assert [m.character_name for m in team] == ["Ashbringer"]

    await db.add_snapshot(
        {
            "member_count": 10,
            "active_count": 8,
            "avg_item_level": 632.0,
            "health_score": 74.5,
            "role_counts": {"TANK": 2, "DPS": 8},
        }
    )
    snaps = await db.snapshots()
    assert len(snaps) == 1
    assert snaps[0]["health_score"] == 74.5
    assert snaps[0]["role_counts"] == {"TANK": 2, "DPS": 8}
    assert snaps[0]["taken_at"] is not None
