
from ftabot.models import Member


def _member(name, **kw):
    return Member(character_name=name, realm_slug="hellfire", region="eu", **kw)


async def test_upsert_insert_and_get(db):
    mid = await db.upsert_synced(
        _member("Ashbringer", class_name="Paladin", role="TANK", mythic_plus_score=3000)
    )
    assert mid > 0
    got = await db.get_member("ashbringer", "hellfire", "eu")  # case-insensitive
    assert got is not None
    assert got.id == mid
    assert got.class_name == "Paladin"
    assert got.mythic_plus_score == 3000
    assert got.created_at is not None and got.last_synced is not None


async def test_upsert_update_preserves_manual_fields(db):
    mid = await db.upsert_synced(_member("Emberlyn", class_name="Mage"))
    await db.set_discord_link(mid, "123456789")
    await db.set_note(mid, "Raid lead")

    # Re-sync with new score; manual fields must survive.
    await db.upsert_synced(_member("Emberlyn", class_name="Mage", mythic_plus_score=3200))
    got = await db.get_member("Emberlyn", "hellfire", "eu")
    assert got.discord_id == "123456789"
    assert got.notes == "Raid lead"
    assert got.mythic_plus_score == 3200


async def test_upsert_partial_does_not_null_existing(db):
    await db.upsert_synced(_member("Cinder", class_name="Druid", item_level=620))
    # A later partial sync with no item level must not wipe the stored one.
    await db.upsert_synced(_member("Cinder", mythic_plus_score=2500))
    got = await db.get_member("Cinder", "hellfire", "eu")
    assert got.item_level == 620
    assert got.mythic_plus_score == 2500


async def test_search_by_class_and_role(db):
    await db.upsert_synced(_member("A", class_name="Mage", role="DPS"))
    await db.upsert_synced(_member("B", class_name="Priest", role="HEALER"))
    await db.upsert_synced(_member("C", class_name="Mage", role="DPS"))

    mages = await db.search_members(class_name="Mage")
    assert {m.character_name for m in mages} == {"A", "C"}

    healers = await db.search_members(role="HEALER")
    assert [m.character_name for m in healers] == ["B"]

    by_name = await db.search_members(name="a")  # 'A' only matches substring 'a'
    assert {m.character_name for m in by_name} == {"A"}


async def test_main_alt_relationship(db):
    main_id = await db.upsert_synced(_member("Mainchar"))
    alt_id = await db.upsert_synced(_member("Altchar"))
    await db.set_main_alt(alt_id, is_main=False, main_character_id=main_id)

    alts = await db.alts_of(main_id)
    assert [a.character_name for a in alts] == ["Altchar"]
    alt = await db.get_member_by_id(alt_id)
    assert alt.is_main is False
    assert alt.main_character_id == main_id


async def test_discord_link_and_whois(db):
    mid = await db.upsert_synced(_member("Linkme"))
    await db.set_discord_link(mid, "999")
    found = await db.search_members(discord_id="999")
    assert [m.character_name for m in found] == ["Linkme"]
    # Unlink.
    await db.set_discord_link(mid, None)
    assert await db.search_members(discord_id="999") == []


async def test_prune_missing_removes_absent(db):
    a = await db.upsert_synced(_member("Stay"))
    await db.upsert_synced(_member("Leave"))
    removed = await db.prune_missing([a])
    assert removed == 1
    assert await db.get_member("Leave", "hellfire", "eu") is None
    assert await db.get_member("Stay", "hellfire", "eu") is not None


async def test_all_members_sort_nulls_last(db):
    await db.upsert_synced(_member("HighScore", mythic_plus_score=3000))
    await db.upsert_synced(_member("NoScore"))
    await db.upsert_synced(_member("MidScore", mythic_plus_score=1500))
    ordered = await db.all_members(order_by="mythic_plus_score")
    names = [m.character_name for m in ordered]
    assert names == ["HighScore", "MidScore", "NoScore"]


async def test_count(db):
    assert await db.count() == 0
    await db.upsert_synced(_member("One"))
    await db.upsert_synced(_member("Two"))
    assert await db.count() == 2
