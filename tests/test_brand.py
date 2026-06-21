"""Calcifer's brand/voice layer: the house style rules and line picking."""

from __future__ import annotations

import random

from ftabot import brand
from ftabot.brand import Mood, Voice


def test_sanitize_removes_em_dashes():
    assert brand.has_em_dash("fast — and loud")
    cleaned = brand.sanitize("fast — and loud")
    assert not brand.has_em_dash(cleaned)
    assert "—" not in cleaned
    # plain text is untouched
    assert brand.sanitize("no dashes here") == "no dashes here"
    assert brand.sanitize(None) is None


def test_sanitize_collapses_runs_of_spaces():
    # The em-dash swap can leave double spaces; sanitize collapses any run of
    # whitespace down to a single space (the while-loop branch).
    assert brand.sanitize("too    many     spaces") == "too many spaces"


def test_pick_returns_none_when_bank_and_default_are_empty():
    # A key with no lines and no _default fallback yields None rather than
    # raising on an empty choice pool.
    voice = Voice(banks={"empty": []}, rng=random.Random(0))
    assert voice.line("empty") is None


def test_no_bank_line_has_an_em_dash():
    for key, lines in brand._BANKS.items():
        for line in lines:
            assert not brand.has_em_dash(line), f"em dash in bank {key}: {line!r}"


def test_voice_never_emits_an_em_dash():
    voice = Voice(rng=random.Random(1))
    for _ in range(200):
        for key in ("thinking", "not_found", "mplus", "raid", "logs"):
            line = voice.line(key) or ""
            assert "—" not in line


def test_line_avoids_immediate_repeats():
    # a bank with several lines should never repeat back-to-back
    voice = Voice(rng=random.Random(7))
    previous = None
    for _ in range(60):
        current = voice.line("thinking")
        assert current is not None
        assert current != previous
        previous = current


def test_line_is_always_returned_for_structural_keys():
    voice = Voice(tone="off", rng=random.Random(0))
    # structural lines ignore the tone dial
    assert voice.line("not_found") is not None
    # an unknown key falls back to the default bank rather than None
    assert voice.line("totally-unknown-key") is not None


def test_flavour_respects_the_tone_dial():
    # off -> never
    off = Voice(tone="off", rng=random.Random(0))
    assert all(off.flavour("mplus") is None for _ in range(50))
    # full -> always
    full = Voice(tone="full", rng=random.Random(0))
    assert all(full.flavour("mplus") is not None for _ in range(50))
    # light -> sometimes (roughly half)
    light = Voice(tone="light", rng=random.Random(0))
    hits = sum(light.flavour("mplus") is not None for _ in range(400))
    assert 100 < hits < 300


def test_palette_and_icons_present():
    assert brand.color_for("info") == brand.BLUE_FLAME
    assert brand.color_for("warning") == brand.AMBER
    assert brand.color_for("critical") == brand.EMBER_RED
    assert brand.color_for("nonsense") == brand.BLUE_FLAME
    assert brand.icon("mplus") == "🔑"
    assert brand.mood_emoji(Mood.THINKING) == "🤔"
    assert brand.mood_emoji(Mood.NEUTRAL) == ""


def test_custom_emoji_names_map_to_uploaded_set():
    # domain glyphs use the :fta_*: convention
    assert brand.emoji_name("mplus") == "fta_keystone"
    assert brand.emoji_name("vault") == "fta_vault"
    # roles reuse the guild's existing emoji names (no underscore), so the
    # already-uploaded role emoji resolve without a re-upload
    assert brand.emoji_name("tank") == "roletank"
    assert brand.emoji_name("healer") == "roleheal"
    assert brand.emoji_name("dps") == "roledps"
    # keys with no custom emoji fall through to the unicode glyph
    assert brand.emoji_name("up") is None
    # moods carry their :calcifer_*: name; neutral has none
    assert brand.mood_emoji_name(Mood.THINKING) == "calcifer_think"
    assert brand.mood_emoji_name(Mood.NEUTRAL) is None


def test_class_crest_emoji_cover_every_class_but_evoker():
    from ftabot import models

    # title-case lookup matches the names used in models.CLASS_COLORS
    assert brand.class_emoji_name("Death Knight") == "class_dk"
    assert brand.class_emoji_name("Demon Hunter") == "class_dh"
    assert brand.class_emoji_name("mage") == "class_mage"  # case-insensitive
    # Evoker has no crest in the uploaded set
    assert brand.class_emoji_name("Evoker") is None
    assert brand.class_emoji_name(None) is None
    # every class except Evoker has a crest, and every key is a real class
    expected = {c for c in models.CLASSES if c != "Evoker"}
    assert set(brand.CLASS_EMOJI) == expected


# --- emoji-first rendering helpers ------------------------------------------

_EMOJI = {  # a fake guild map: name -> rendered markup
    "roleheal": "<:roleheal:1>",
    "roletank": "<:roletank:2>",
    "roledps": "<:roledps:3>",
    "class_dk": "<:class_dk:4>",
}


def test_role_icon_prefers_custom_emoji_then_unicode():
    # the healer icon the board was missing now resolves to the uploaded emoji
    assert brand.role_icon("HEALER", _EMOJI) == "<:roleheal:1>"
    assert brand.role_icon("Tank", _EMOJI) == "<:roletank:2>"
    # no map -> unicode fallback (keeps offline renders + tests working)
    assert brand.role_icon("HEALER") == brand.ICONS["healer"]
    assert brand.role_icon("dps", {}) == brand.ICONS["dps"]
    assert brand.role_icon(None) == ""


def test_class_label_carries_the_colour_crest_then_falls_back():
    # crest present -> name is prefixed with the class-colour emoji
    assert brand.class_label("Death Knight", _EMOJI) == "<:class_dk:4> Death Knight"
    # crest not uploaded -> bare name (no unicode crest exists)
    assert brand.class_label("Mage", _EMOJI) == "Mage"
    assert brand.class_icon("Mage", _EMOJI) == ""
    assert brand.class_label(None) == "?"


def test_score_color_maps_the_raiderio_scale_exactly():
    assert brand.score_color(4300) == 0xFF8000  # top legendary orange
    assert brand.score_color(4275) == 0xFF8000  # band is inclusive at its floor
    assert brand.score_color(2400) == 0x1EFF00  # the uncommon-green anchor
    assert brand.score_color(200) == 0xFFFFFF
    assert brand.score_color(150) == 0xFFFFFF   # below the floor stays white
    assert brand.score_color(None) == 0xFFFFFF
