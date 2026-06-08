"""Tests for the GRM identity SavedVariables parser."""

from ftabot.services.grm import alt_groups_from_savedvars, parse_identities
from ftabot.services.savedvars import parse_savedvariables

# A realistic GRM dump: GRM_GuildMemberHistory_Save[faction][guild][members],
# member records with named keys, alts both positional and named, isMain,
# joinDateUnix (2020-01-15) and a customNote.
GRM_LUA = """
GRM_GuildMemberHistory_Save = {
    [1] = {
        ["From the Ashes"] = {
            {
                ["name"] = "Calcifer-Hellfire",
                ["class"] = "MAGE",
                ["GUID"] = "Player-1234-ABCDEF01",
                ["rankName"] = "Guild Master",
                ["isMain"] = true,
                ["joinDateUnix"] = 1579046400,
                ["customNote"] = "the hearth",
                ["alts"] = {
                    { "Emberling-Hellfire", "WARLOCK" },
                    { ["name"] = "Cinder-Hellfire", ["class"] = "ROGUE" },
                },
            },
            {
                ["name"] = "Emberling-Hellfire",
                ["class"] = "WARLOCK",
                ["rankName"] = "Alt",
                ["isMain"] = false,
                ["mainName"] = "Calcifer-Hellfire",
                ["joinDate"] = "2021-03-02",
            },
        },
    },
}
"""


def test_parses_member_records_and_fields():
    recs = parse_identities(GRM_LUA)
    by_name = {r.character_name: r for r in recs}
    cal = by_name["Calcifer"]
    assert cal.realm_slug == "hellfire"
    assert cal.region == "eu"
    assert cal.class_name == "MAGE"
    assert cal.guid == "Player-1234-ABCDEF01"
    assert cal.rank_name == "Guild Master"
    assert cal.is_main is True
    assert cal.join_date == "2020-01-15"
    assert cal.note == "the hearth"
    assert cal.main_name is None  # a main has no main_name


def test_alts_positional_and_named():
    recs = parse_identities(GRM_LUA)
    cal = next(r for r in recs if r.character_name == "Calcifer")
    assert cal.alts == ["Emberling-Hellfire", "Cinder-Hellfire"]


def test_alt_declares_main_and_join_date_string():
    recs = parse_identities(GRM_LUA)
    ember = next(r for r in recs if r.character_name == "Emberling")
    assert ember.is_main is False
    assert ember.main_name == "Calcifer-Hellfire"
    assert ember.join_date == "2021-03-02"


def test_shape_agnostic_name_keyed_guild_bucket():
    # Some GRM forms key members by "Name-Realm" instead of a numeric array.
    text = """
    GRM_GuildMemberHistory_Save = {
        ["Horde"] = {
            ["From the Ashes"] = {
                ["Calcifer-Hellfire"] = {
                    ["name"] = "Calcifer-Hellfire", ["GUID"] = "x", ["isMain"] = true,
                },
            },
        },
    }
    """
    recs = parse_identities(text)
    assert len(recs) == 1 and recs[0].character_name == "Calcifer"


def test_realm_with_spaces_and_default_realm():
    text = """
    GRM_GuildMemberHistory_Save = {
        { ["name"] = "Emberling-Argent Dawn", ["GUID"] = "y" },
        { ["name"] = "Sparkless", ["rankName"] = "Member" },
    }
    """
    recs = parse_identities(text, default_realm="hellfire")
    by_name = {r.character_name: r for r in recs}
    assert by_name["Emberling"].realm_slug == "argent-dawn"
    assert by_name["Sparkless"].realm_slug == "hellfire"  # bare name -> default


def test_dedup_on_name_realm():
    text = """
    GRM_GuildMemberHistory_Save = {
        { ["name"] = "Calcifer-Hellfire", ["GUID"] = "a" },
        { ["name"] = "Calcifer-Hellfire", ["GUID"] = "b" },
    }
    """
    recs = parse_identities(text)
    assert len(recs) == 1


def test_non_member_tables_are_ignored():
    # Settings/metadata blocks without strong member markers don't become rows.
    text = """
    GRM_GuildMemberHistory_Save = {
        ["settings"] = { ["theme"] = "dark", ["scale"] = 1 },
        { ["name"] = "Calcifer-Hellfire", ["GUID"] = "a" },
    }
    """
    recs = parse_identities(text)
    assert [r.character_name for r in recs] == ["Calcifer"]


def test_join_date_accepts_quoted_numeric_unix():
    # SavedVariables frequently store numbers as quoted strings.
    lua = """
    GRM_GuildMemberHistory_Save = { [1] = { ["Guild"] = { {
        ["name"] = "Sparky-Hellfire", ["class"] = "MAGE",
        ["GUID"] = "Player-1-AAAA", ["rankName"] = "Member",
        ["joinDateUnix"] = "1579046400",
    } } } }
    """
    recs = parse_identities(lua)
    assert recs[0].join_date == "2020-01-15"


def test_join_date_history_picks_earliest_event():
    # joinDateHist is an unordered history; the earliest epoch is the real join.
    lua = """
    GRM_GuildMemberHistory_Save = { [1] = { ["Guild"] = { {
        ["name"] = "Ember-Hellfire", ["class"] = "WARLOCK",
        ["GUID"] = "Player-1-BBBB", ["rankName"] = "Member",
        ["joinDateHist"] = { 1700000000, 1579046400, 1650000000 },
    } } } }
    """
    recs = parse_identities(lua)
    assert recs[0].join_date == "2020-01-15"  # 1579046400, the smallest


# --- GRM_PlayerListOfAlts_Save (alt<->main grouping auxiliary table) ---

ALTS_LUA = """
GRM_PlayerListOfAlts_Save = {
    ["From the Ashes"] = {
        ["Calcifer-Hellfire"] = {
            { ["name"] = "Emberling-Hellfire", ["class"] = "WARLOCK" },
            { "Cinder-Hellfire", "ROGUE" },
        },
        ["Sparky-Hellfire"] = {
            { ["name"] = "Sparkless-Hellfire" },
        },
    },
}
"""


def test_alt_groups_named_and_positional_entries():
    parsed = parse_savedvariables(ALTS_LUA)
    groups = alt_groups_from_savedvars(parsed)
    by_main = {g[0]: g for g in groups}
    # The dict key is the main; entries become its alts (name only, never class).
    assert by_main["Calcifer-Hellfire"] == [
        "Calcifer-Hellfire",
        "Emberling-Hellfire",
        "Cinder-Hellfire",
    ]
    assert by_main["Sparky-Hellfire"] == ["Sparky-Hellfire", "Sparkless-Hellfire"]


def test_alt_groups_accepts_flat_table_without_guild_wrapper():
    lua = """
    GRM_PlayerListOfAlts_Save = {
        ["Calcifer-Hellfire"] = { { ["name"] = "Emberling-Hellfire" } },
    }
    """
    groups = alt_groups_from_savedvars(parse_savedvariables(lua))
    assert groups == [["Calcifer-Hellfire", "Emberling-Hellfire"]]


def test_alt_groups_drop_singletons_and_dedup():
    # A main with no alts (or only itself) carries no linking information.
    lua = """
    GRM_PlayerListOfAlts_Save = {
        ["Lonely-Hellfire"] = { },
        ["Calcifer-Hellfire"] = {
            { ["name"] = "Calcifer-Hellfire" },
            { ["name"] = "Emberling-Hellfire" },
        },
    }
    """
    groups = alt_groups_from_savedvars(parse_savedvariables(lua))
    assert groups == [["Calcifer-Hellfire", "Emberling-Hellfire"]]


def test_alt_groups_empty_when_table_absent():
    assert alt_groups_from_savedvars({"GRM_GuildMemberHistory_Save": {}}) == []
