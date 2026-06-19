"""Tests for the dependency-free WoW SavedVariables Lua parser."""

import pytest

from ftabot.services.savedvars import (
    LuaParseError,
    dump,
    parse_lua_value,
    parse_savedvariables,
)

# --- scalars ----------------------------------------------------------------


def test_scalars_and_numbers():
    assert parse_lua_value("nil") is None
    assert parse_lua_value("true") is True
    assert parse_lua_value("false") is False
    assert parse_lua_value("42") == 42
    assert parse_lua_value("-7") == -7
    assert parse_lua_value("3.5") == 3.5
    assert parse_lua_value("-0.25") == -0.25
    assert parse_lua_value(".5") == 0.5
    assert parse_lua_value("1e3") == 1000.0
    assert parse_lua_value("0x1F") == 31


def test_strings_quotes_and_escapes():
    assert parse_lua_value('"hello"') == "hello"
    assert parse_lua_value("'single'") == "single"
    assert parse_lua_value(r'"a\tb\nc"') == "a\tb\nc"
    assert parse_lua_value(r'"quote: \" and slash: \\"') == 'quote: " and slash: \\'
    # \ddd decimal byte escape (Lua 5.1).
    assert parse_lua_value(r'"\65\66"') == "AB"


def test_long_bracket_strings():
    assert parse_lua_value("[[plain]]") == "plain"
    # A single leading newline after the opener is dropped by Lua.
    assert parse_lua_value("[[\nline]]") == "line"
    assert parse_lua_value("[==[ has ]] inside ]==]") == " has ]] inside "


def test_item_link_string_survives_pipes_and_colons():
    link = '"|cffa335ee|Hitem:212396::::::::80:::::|h[Greatsword]|h|r"'
    assert parse_lua_value(link) == "|cffa335ee|Hitem:212396::::::::80:::::|h[Greatsword]|h|r"


# --- tables -----------------------------------------------------------------


def test_array_table_is_one_indexed():
    assert parse_lua_value('{ "a", "b", "c" }') == {1: "a", 2: "b", 3: "c"}


def test_empty_and_trailing_separators():
    assert parse_lua_value("{}") == {}
    assert parse_lua_value("{ 1, 2, }") == {1: 1, 2: 2}
    assert parse_lua_value("{ 1; 2; }") == {1: 1, 2: 2}


def test_keyed_table_all_key_forms():
    parsed = parse_lua_value('{ ["str"] = 1, bare = 2, [3] = "three", [true] = 4 }')
    assert parsed == {"str": 1, "bare": 2, 3: "three", True: 4}


def test_mixed_array_and_keyed_keeps_both():
    parsed = parse_lua_value('{ "first", name = "x", "second" }')
    # Positional entries auto-index 1,2 regardless of the interleaved named key.
    assert parsed == {1: "first", 2: "second", "name": "x"}


def test_nested_tables():
    parsed = parse_lua_value('{ a = { b = { c = 1 } }, list = { 1, 2 } }')
    assert parsed == {"a": {"b": {"c": 1}}, "list": {1: 1, 2: 2}}


def test_comments_are_ignored():
    text = """{
        -- a line comment
        x = 1,  -- trailing
        --[[ a block
             comment ]]
        y = 2,
    }"""
    assert parse_lua_value(text) == {"x": 1, "y": 2}


# --- whole-file parsing -----------------------------------------------------


def test_parse_savedvariables_multiple_globals():
    text = """
    MyAddonDB = {
        ["enabled"] = true,
        count = 3,
    }
    MyAddon_Other = "hello"
    MyAddon_Flag = false
    """
    out = parse_savedvariables(text)
    assert out["MyAddonDB"] == {"enabled": True, "count": 3}
    assert out["MyAddon_Other"] == "hello"
    assert out["MyAddon_Flag"] is False


def test_rclootcouncil_lootdb_shape():
    text = """
    RCLootCouncilLootDB = {
        ["Calcifer-Hellfire"] = {
            {
                ["lootWon"] = "|cffa335ee|Hitem:212396::::::::80:::::|h[Sword]|h|r",
                ["date"] = "2026/06/01",
                ["time"] = "20:15:30",
                ["instance"] = "Manaforge Omega-Mythic",
                ["boss"] = "Nexus-King",
                ["response"] = "BiS",
                ["responseID"] = 1,
                ["votes"] = 5,
                ["difficultyID"] = 16,
            },
        },
    }
    """
    out = parse_savedvariables(text)
    awards = out["RCLootCouncilLootDB"]["Calcifer-Hellfire"]
    assert awards[1]["boss"] == "Nexus-King"
    assert awards[1]["response"] == "BiS"
    assert awards[1]["difficultyID"] == 16


def test_grm_nested_faction_guild_shape():
    text = """
    GRM_GuildMemberHistory_Save = {
        [1] = {
            ["From the Ashes"] = {
                {
                    ["name"] = "Calcifer-Hellfire",
                    ["class"] = "MAGE",
                    ["isMain"] = true,
                    ["alts"] = { { "Emberling-Hellfire", "WARLOCK" } },
                },
            },
        },
    }
    """
    out = parse_savedvariables(text)
    guild = out["GRM_GuildMemberHistory_Save"][1]["From the Ashes"]
    assert guild[1]["name"] == "Calcifer-Hellfire"
    assert guild[1]["alts"][1] == {1: "Emberling-Hellfire", 2: "WARLOCK"}


# --- errors -----------------------------------------------------------------


def test_unterminated_string_raises():
    with pytest.raises(LuaParseError):
        parse_lua_value('"no end')


def test_unterminated_table_raises():
    with pytest.raises(LuaParseError):
        parse_lua_value("{ 1, 2")


def test_bare_global_value_is_rejected():
    # A value position may only hold a literal/table, never a bare identifier.
    with pytest.raises(LuaParseError):
        parse_lua_value("SomeGlobal")


def test_trailing_data_after_value_raises():
    with pytest.raises(LuaParseError):
        parse_lua_value("{} garbage")


def test_empty_input_expects_a_value():
    # An empty value position (e.g. inside a stray sep) hits "expected a value".
    with pytest.raises(LuaParseError, match="expected a value"):
        parse_lua_value("   ")


def test_malformed_number_after_minus_raises():
    # A '-' followed by a non-number is a malformed number, not unary minus.
    with pytest.raises(LuaParseError, match="malformed number"):
        parse_lua_value("-x")


def test_dangling_escape_at_end_of_string_raises():
    # A trailing backslash with nothing after it inside a string.
    with pytest.raises(LuaParseError, match="dangling escape"):
        parse_lua_value('"abc\\')


def test_hex_byte_escape_and_fallthrough():
    # \xHH (Lua 5.2+) is tolerated; an incomplete \x falls through to literal 'x'.
    assert parse_lua_value(r'"\x41\x42"') == "AB"
    assert parse_lua_value(r'"\xZZ"') == "xZZ"  # bad hex -> 'x' then the two Zs


def test_unterminated_long_bracket_raises():
    with pytest.raises(LuaParseError, match="unterminated long bracket"):
        parse_lua_value("[[ no close here")


def test_long_bracket_drops_leading_crlf():
    # Lua drops a single newline after the opener; here it's a CRLF.
    assert parse_lua_value("[[\r\nbody]]") == "body"


def test_table_key_missing_closing_bracket_raises():
    with pytest.raises(LuaParseError, match=r"expected ']' after table key"):
        parse_lua_value('{ ["k" = 1 }')


def test_table_key_without_eq_raises():
    with pytest.raises(LuaParseError, match="expected '='"):
        parse_lua_value('{ ["k"] 1 }')


def test_bare_identifier_in_value_position_is_rejected():
    # An identifier not followed by '=' isn't a key; it's an attempted bare
    # value, which the grammar rejects (never a global).
    with pytest.raises(LuaParseError, match="unexpected token"):
        parse_lua_value("{ foo }")


def test_savedvariables_strips_utf8_bom():
    # A leading UTF-8 BOM is tolerated before the first global assignment.
    out = parse_savedvariables("﻿MyAddonDB = { x = 1 }")
    assert out["MyAddonDB"] == {"x": 1}


def test_savedvariables_expects_a_global_name():
    # A line that isn't a Name = value assignment is rejected.
    with pytest.raises(LuaParseError, match="expected a global name"):
        parse_savedvariables("= 1")


# --- dump (round-trip serialiser) -------------------------------------------


def test_dump_round_trips_scalars_tables_and_empty():
    data = {
        "Flag": True,
        "Off": False,
        "Nope": None,
        "Count": 3,
        "Ratio": 1.5,
        "Name": 'a "quoted"\tname',
        "Empty": {},
        "Nested": {"k": [1, 2]},  # list -> 1-indexed table
    }
    text = dump(data)
    assert "Empty = {}" in text
    reparsed = parse_savedvariables(text)
    assert reparsed["Flag"] is True
    assert reparsed["Off"] is False
    assert reparsed["Nope"] is None
    assert reparsed["Count"] == 3
    assert reparsed["Ratio"] == 1.5
    assert reparsed["Name"] == 'a "quoted"\tname'
    assert reparsed["Empty"] == {}
    assert reparsed["Nested"] == {"k": {1: 1, 2: 2}}


def test_dump_rejects_unserialisable_type():
    with pytest.raises(TypeError, match="cannot serialise"):
        dump({"bad": object()})
