"""Tests for the RCLootCouncil loot-history mapper."""

from ftabot.services.rclootcouncil import (
    _normalize_when,
    _parse_item_link,
    loot_from_savedvars,
    parse_loot,
)

SWORD = "|cffa335ee|Hitem:212396::::::::80:::::|h[Ember Greatsword]|h|r"
RING = "|cffa335ee|Hitem:215135::::::::80:::::|h[Band of Ash]|h|r"

SAMPLE = f"""
RCLootCouncilLootDB = {{
    ["Calcifer-Hellfire"] = {{
        {{
            ["lootWon"] = "{SWORD}",
            ["date"] = "2026/06/01",
            ["time"] = "20:15:30",
            ["instance"] = "Manaforge Omega-Mythic",
            ["boss"] = "Nexus-King Salhadaar",
            ["response"] = "BiS",
            ["responseID"] = 1,
            ["votes"] = 7,
            ["difficultyID"] = 16,
        }},
        {{
            ["lootWon"] = "{RING}",
            ["date"] = "31/05/26",
            ["time"] = "21:02",
            ["instance"] = "Manaforge Omega-Heroic",
            ["boss"] = "Plexus Sentinel",
            ["response"] = "Upgrade",
            ["votes"] = 2,
            ["difficultyID"] = 15,
        }},
    }},
    ["Emberling-Argent Dawn"] = {{
        {{
            ["lootWon"] = "{RING}",
            ["date"] = "2026/06/02",
            ["time"] = "19:00:00",
            ["boss"] = "Loom'ithar",
            ["difficultyID"] = 16,
        }},
    }},
}}
"""


def test_parse_loot_end_to_end():
    entries = parse_loot(SAMPLE)
    assert len(entries) == 3
    first = entries[0]
    assert first.character_name == "Calcifer"
    assert first.realm_slug == "hellfire"
    assert first.region == "eu"
    assert first.item_id == 212396
    assert first.item_name == "Ember Greatsword"
    assert first.boss == "Nexus-King Salhadaar"
    assert first.difficulty == "Mythic"
    assert first.response == "BiS"
    assert first.votes == 7
    assert first.awarded_at == "2026-06-01T20:15:30"
    assert first.source == "rclc"


def test_realm_slug_with_spaces():
    entries = parse_loot(SAMPLE)
    ember = next(e for e in entries if e.character_name == "Emberling")
    assert ember.realm_slug == "argent-dawn"


def test_old_date_format_and_short_time():
    entries = parse_loot(SAMPLE)
    heroic = next(e for e in entries if e.difficulty == "Heroic")
    # "31/05/26" + "21:02" -> ISO with zero seconds.
    assert heroic.awarded_at == "2026-05-31T21:02:00"


def test_accepts_loot_table_directly_not_just_whole_file():
    table = {"Calcifer-Hellfire": {1: {"lootWon": SWORD, "date": "2026/06/01"}}}
    entries = loot_from_savedvars(table)
    assert len(entries) == 1 and entries[0].item_id == 212396


def test_dedup_within_one_import():
    table = {
        "Calcifer-Hellfire": {
            1: {"lootWon": SWORD, "date": "2026/06/01", "time": "20:15:30"},
            2: {"lootWon": SWORD, "date": "2026/06/01", "time": "20:15:30"},
        }
    }
    assert len(loot_from_savedvars(table)) == 1


def test_malformed_awards_are_skipped():
    table = {
        "Calcifer-Hellfire": {
            1: "not-a-dict",
            2: {"response": "BiS"},  # no item id and no date -> undedupable, skip
            3: {"lootWon": SWORD, "date": "2026/06/01"},
        }
    }
    entries = loot_from_savedvars(table)
    assert len(entries) == 1 and entries[0].item_id == 212396


def test_item_link_helper():
    assert _parse_item_link(SWORD) == (212396, "Ember Greatsword")
    assert _parse_item_link("garbage") == (None, None)
    assert _parse_item_link(None) == (None, None)


def test_normalize_when_variants():
    assert _normalize_when("2026/06/01", "20:15:30") == "2026-06-01T20:15:30"
    assert _normalize_when("01/06/26", "9:05") == "2026-06-01T09:05:00"
    assert _normalize_when("2026-06-01", None) == "2026-06-01"
    assert _normalize_when("not a date", "x") is None
    assert _normalize_when("", "") is None
