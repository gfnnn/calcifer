"""The data-spine validation tooling: the Lua serialiser, the redactor that
turns a real dump into a committable fixture, and the shape inspector.
"""

from __future__ import annotations

from ftabot.identity import build_accounts
from ftabot.services.grm import identities_from_savedvars
from ftabot.services.rclootcouncil import loot_from_savedvars
from ftabot.services.savedvars import dump, parse_savedvariables

_SWORD = "|cffa335ee|Hitem:212396::::::::80:::::|h[Ember Greatsword]|h|r"

# A small but representative dump (modelled on the real addon shapes the parsers
# accept): a GRM main with two alts (one positional, one named), a GUID, a
# private note, and an RCLootCouncil loot table keyed by name.
DUMP = f"""
GRM_GuildMemberHistory_Save = {{
    [1] = {{
        ["From the Ashes"] = {{
            {{
                ["name"] = "Calcifer-Hellfire",
                ["class"] = "MAGE",
                ["GUID"] = "Player-1234-ABCDEF01",
                ["rankName"] = "Guild Master",
                ["isMain"] = true,
                ["joinDateUnix"] = 1579046400,
                ["customNote"] = "real person's real name here",
                ["alts"] = {{
                    {{ "Emberling-Hellfire", "WARLOCK" }},
                    {{ ["name"] = "Cinder-Hellfire", ["class"] = "ROGUE" }},
                }},
            }},
            {{
                ["name"] = "Emberling-Hellfire",
                ["class"] = "WARLOCK",
                ["rankName"] = "Alt",
                ["isMain"] = false,
                ["mainName"] = "Calcifer-Hellfire",
                ["joinDate"] = "2021-03-02",
            }},
        }},
    }},
}}
RCLootCouncilLootDB = {{
    ["Calcifer-Hellfire"] = {{
        {{
            ["lootWon"] = "{_SWORD}",
            ["date"] = "2026/06/01",
            ["boss"] = "Nexus-King Salhadaar",
            ["difficultyID"] = 16,
        }},
    }},
}}
"""

_ORIGINAL_NAMES = {"Calcifer-Hellfire", "Emberling-Hellfire", "Cinder-Hellfire"}


# --- the Lua serialiser -----------------------------------------------------


def test_dump_round_trips_through_the_parser():
    parsed = parse_savedvariables(DUMP)
    assert parse_savedvariables(dump(parsed)) == parsed


def test_dump_escapes_strings_and_serialises_scalars():
    parsed = {"X": {1: 'a "quote"\nline', 2: 80, 3: 12.5, 4: True, 5: None}}
    assert parse_savedvariables(dump(parsed)) == parsed


# --- the redactor -----------------------------------------------------------


def _redact_module():
    import importlib

    return importlib.import_module("scripts.redact_savedvars")


def test_collect_names_finds_every_character():
    redact = _redact_module()
    names = redact.collect_names(parse_savedvariables(DUMP))
    assert _ORIGINAL_NAMES.issubset(names)


def test_redacted_output_leaks_no_original_name():
    redact = _redact_module()
    out = redact.redact_text(DUMP)
    for name in _ORIGINAL_NAMES:
        assert name not in out
    assert redact.residual_names(out, _ORIGINAL_NAMES) == set()


def test_redaction_preserves_shape_and_references():
    redact = _redact_module()
    out = redact.redact_text(DUMP)
    parsed = parse_savedvariables(out)  # still valid Lua

    # Same structural counts after redaction.
    records = identities_from_savedvars(parsed)
    assert len(records) == 2
    loot = loot_from_savedvars(parsed)
    assert len(loot) == 1
    assert loot[0].character_name.startswith("Player")  # owner renamed

    # The references survive renaming: the main + both alts still collapse into
    # a single account (so an alt's main pointer renamed to the main's pseudonym).
    accounts = build_accounts(records)
    assert len(accounts) == 1
    assert len(accounts[0].alts) == 2  # Emberling + Cinder still linked


def test_redaction_hashes_guids_and_drops_notes():
    redact = _redact_module()
    out = redact.redact_text(DUMP)
    assert "Player-1234-ABCDEF01" not in out  # original GUID gone
    assert "Player-0-" in out  # replaced with a hashed GUID
    assert "real person's real name here" not in out  # note dropped


# --- the inspector ----------------------------------------------------------


def test_inspect_summarize_reports_addons_and_counts():
    import importlib

    inspect = importlib.import_module("scripts.inspect_savedvars")
    report = inspect.summarize(parse_savedvariables(DUMP))
    assert "GRM" in report
    assert "RCLootCouncil" in report
    assert "GRM identities parsed: 2" in report
    assert "RCLootCouncil loot rows parsed: 1" in report
