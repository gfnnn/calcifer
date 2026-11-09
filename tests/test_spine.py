"""Tests for data-spine ingestion + the SavedVariables upload endpoint."""

from ftabot.config import Config
from ftabot.models import Member
from ftabot.spine import ingest_savedvars

SWORD = "|cffa335ee|Hitem:212396::::::::80:::::|h[Ember Greatsword]|h|r"

RCLC_LUA = f"""
RCLootCouncilLootDB = {{
    ["Calcifer-Hellfire"] = {{
        {{ ["lootWon"] = "{SWORD}", ["date"] = "2026/06/01", ["time"] = "20:15:30",
           ["boss"] = "Nexus-King", ["difficultyID"] = 16, ["response"] = "BiS" }},
    }},
}}
"""

GRM_LUA = """
GRM_GuildMemberHistory_Save = {
    [1] = {
        ["From the Ashes"] = {
            { ["name"] = "Calcifer-Hellfire", ["GUID"] = "g1", ["isMain"] = true,
              ["joinDateUnix"] = 1579046400, ["alts"] = { { "Emberling-Hellfire" } } },
            { ["name"] = "Emberling-Hellfire", ["rankName"] = "Alt",
              ["mainName"] = "Calcifer-Hellfire" },
        },
    },
}
"""


# --- ingestion (offline, no HTTP) -------------------------------------------


async def test_ingest_rclootcouncil_into_loot_history(db):
    from ftabot.services.savedvars import parse_savedvariables

    summary = await ingest_savedvars(db, parse_savedvariables(RCLC_LUA))
    assert summary["detected"] == ["rclootcouncil"]
    assert summary["loot_parsed"] == 1
    assert summary["loot_imported"] == 1
    loot = await db.get_loot(character="Calcifer")
    assert len(loot) == 1 and loot[0].item_id == 212396


async def test_ingest_is_idempotent_on_reupload(db):
    from ftabot.services.savedvars import parse_savedvariables

    data = parse_savedvariables(RCLC_LUA)
    first = await ingest_savedvars(db, data)
    second = await ingest_savedvars(db, data)
    assert first["loot_imported"] == 1
    assert second["loot_imported"] == 0  # de-duped on the second pass
    assert len(await db.get_loot()) == 1


async def test_ingest_ignores_unknown_globals(db):
    summary = await ingest_savedvars(db, {"SomeOtherAddonDB": {"x": 1}})
    assert summary["detected"] == []


async def test_ingest_returns_empty_summary_for_non_dict_input(db):
    # A parse that yields something other than a dict (e.g. a list) is handled
    # gracefully: nothing detected, no crash.
    summary = await ingest_savedvars(db, ["not", "a", "dict"])  # type: ignore[arg-type]
    assert summary == {"detected": []}


async def test_ingest_grm_links_alts_in_roster(db):
    from ftabot.services.savedvars import parse_savedvariables

    # Two roster characters the GRM dump groups as one person.
    await db.upsert_synced(Member("Calcifer", class_name="Mage"))
    await db.upsert_synced(Member("Emberling", class_name="Warlock"))

    summary = await ingest_savedvars(db, parse_savedvariables(GRM_LUA))
    assert "grm" in summary["detected"]
    assert summary["identities_parsed"] == 2
    assert summary["identities_stored"] == 2
    assert summary["accounts"] == 1
    assert summary["accounts_linked"] == 1
    assert summary["alts_linked"] == 1

    main = await db.get_member("Calcifer", "hellfire", "eu")
    alt = await db.get_member("Emberling", "hellfire", "eu")
    assert main.is_main is True and main.main_character_id is None
    assert alt.is_main is False and alt.main_character_id == main.id


async def test_ingest_grm_alt_list_links_when_records_do_not(db):
    from ftabot.services.savedvars import parse_savedvariables

    # The member records carry no alts/mainName cross-links; only GRM's
    # GRM_PlayerListOfAlts_Save auxiliary table groups the two characters.
    lua = """
    GRM_GuildMemberHistory_Save = {
        [1] = { ["From the Ashes"] = {
            { ["name"] = "Calcifer-Hellfire", ["GUID"] = "g1", ["isMain"] = true },
            { ["name"] = "Emberling-Hellfire", ["GUID"] = "g2" },
        } },
    }
    GRM_PlayerListOfAlts_Save = {
        ["From the Ashes"] = {
            ["Calcifer-Hellfire"] = { { ["name"] = "Emberling-Hellfire" } },
        },
    }
    """
    await db.upsert_synced(Member("Calcifer", class_name="Mage"))
    await db.upsert_synced(Member("Emberling", class_name="Warlock"))

    summary = await ingest_savedvars(db, parse_savedvariables(lua))
    assert summary["alt_groups"] == 1
    assert summary["accounts"] == 1  # collapsed into one despite no record links
    assert summary["alts_linked"] == 1

    main = await db.get_member("Calcifer", "hellfire", "eu")
    alt = await db.get_member("Emberling", "hellfire", "eu")
    assert main.is_main is True
    assert alt.is_main is False and alt.main_character_id == main.id


async def test_ingest_grm_skips_account_whose_main_is_untracked(db):
    from ftabot.services.savedvars import parse_savedvariables

    # Only the alt is in the roster; its main isn't tracked, so nothing links
    # (we never anchor to a fabricated row), but the identity is still stored.
    await db.upsert_synced(Member("Emberling", class_name="Warlock"))
    summary = await ingest_savedvars(db, parse_savedvariables(GRM_LUA))
    assert summary["accounts_linked"] == 0
    assert summary["alts_linked"] == 0
    alt = await db.get_member("Emberling", "hellfire", "eu")
    assert alt.is_main is True  # untouched default


async def test_uploaded_identity_is_stored(tmp_path, ingest_client):
    cfg = Config(database_path=str(tmp_path / "id.db"), savedvars_upload_secret="s3cret")
    client, ingest = await ingest_client(cfg)
    resp = await client.post("/api/upload/savedvars?token=s3cret", data=GRM_LUA)
    assert resp.status == 200, await resp.text()
    record = await ingest.db.get_identity("Calcifer", cfg.guild_realm, cfg.guild_region)
    assert record["is_main"] is True
    assert record["join_date"] == "2020-01-15"
    assert record["alts"] == ["Emberling-Hellfire"]
    assert await ingest.db.get_identity("Nobody", cfg.guild_realm, cfg.guild_region) is None


async def test_combined_upload_detects_both(tmp_path, ingest_client):
    cfg = Config(database_path=str(tmp_path / "both.db"), savedvars_upload_secret="s3cret")
    client, _ = await ingest_client(cfg)
    resp = await client.post(
        "/api/upload/savedvars?token=s3cret", data=RCLC_LUA + "\n" + GRM_LUA
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert set(body["detected"]) == {"rclootcouncil", "grm"}
    assert body["loot_imported"] == 1
    assert body["identities_stored"] == 2


# --- upload endpoint --------------------------------------------------------


def _cfg(tmp_path, *, secret: str | None):
    return Config(database_path=str(tmp_path / "spine.db"), savedvars_upload_secret=secret)


async def test_upload_disabled_without_secret(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret=None))
    resp = await client.post("/api/upload/savedvars", data=RCLC_LUA)
    assert resp.status == 503


async def test_upload_rejects_wrong_secret(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    resp = await client.post("/api/upload/savedvars?token=nope", data=RCLC_LUA)
    assert resp.status == 401


async def test_upload_happy_path_ingests_loot(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    headers = {"X-Upload-Token": "s3cret"}
    resp = await client.post("/api/upload/savedvars", data=RCLC_LUA, headers=headers)
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["detected"] == ["rclootcouncil"]
    assert body["loot_imported"] == 1
    # Re-uploading the same dump imports nothing, proving the first POST
    # persisted through the HTTP path (the dedup is over stored rows).
    again = await client.post("/api/upload/savedvars", data=RCLC_LUA, headers=headers)
    assert again.status == 200
    assert (await again.json())["loot_imported"] == 0


async def test_upload_accepts_a_multi_megabyte_dump(tmp_path, ingest_client):
    # Real dumps exceed aiohttp's 1 MB default body cap; the listener raises it.
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    padding = "-- " + "x" * (2 * 1024 * 1024) + "\n"
    resp = await client.post("/api/upload/savedvars?token=s3cret", data=padding + RCLC_LUA)
    assert resp.status == 200, await resp.text()


async def test_upload_empty_body_is_400(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    resp = await client.post("/api/upload/savedvars?token=s3cret", data="   ")
    assert resp.status == 400


async def test_upload_unparseable_is_400(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    resp = await client.post("/api/upload/savedvars?token=s3cret", data="DB = {{{ broken")
    assert resp.status == 400


async def test_upload_unrecognised_addon_is_422(tmp_path, ingest_client):
    client, _ = await ingest_client(_cfg(tmp_path, secret="s3cret"))
    resp = await client.post(
        "/api/upload/savedvars?token=s3cret", data='SomethingElseDB = { ["x"] = 1 }'
    )
    assert resp.status == 422


async def test_apply_accounts_clears_a_removed_alt_link(db):
    from ftabot.identity import Account

    await db.upsert_synced(Member("Calcifer", class_name="Mage"))
    await db.upsert_synced(Member("Emberling", class_name="Warlock"))

    # First upload: Emberling is Calcifer's alt.
    await db.apply_accounts(
        [Account(main=("calcifer", "hellfire"), alts=[("emberling", "hellfire")])]
    )
    main = await db.get_member("Calcifer", "hellfire", "eu")
    alt = await db.get_member("Emberling", "hellfire", "eu")
    assert alt.is_main is False and alt.main_character_id == main.id

    # Second upload after the officer un-linked Emberling in GRM: it stands
    # alone now, so its stale main pointer must clear (the bug being fixed).
    await db.apply_accounts(
        [Account(main=("calcifer", "hellfire")), Account(main=("emberling", "hellfire"))]
    )
    alt = await db.get_member("Emberling", "hellfire", "eu")
    assert alt.is_main is True and alt.main_character_id is None


async def test_apply_accounts_leaves_non_grm_rows_untouched(db):
    from ftabot.identity import Account

    # An officer manually marked Shadow as an alt (via /alt, not GRM).
    await db.upsert_synced(Member("Calcifer", class_name="Mage"))
    await db.upsert_synced(Member("Shadow", class_name="Priest"))
    main = await db.get_member("Calcifer", "hellfire", "eu")
    shadow = await db.get_member("Shadow", "hellfire", "eu")
    await db.set_main_alt(shadow.id, is_main=False, main_character_id=main.id)

    # A GRM upload that never mentions Shadow must not reset its manual link.
    await db.apply_accounts([Account(main=("calcifer", "hellfire"))])
    shadow = await db.get_member("Shadow", "hellfire", "eu")
    assert shadow.is_main is False and shadow.main_character_id == main.id
