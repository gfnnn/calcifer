"""The personal /me card renderer (pure, offline)."""

from __future__ import annotations

from ftabot import brand
from ftabot.models import Member
from ftabot.ui import (
    FIELD_VALUE_LIMIT,
    TITLE_LIMIT,
    clip,
    me_card,
    member_embed,
    roster_embed,
)


def _m(name, **kw):
    return Member(name, **kw)


def test_me_card_leads_with_main_then_alts_by_score():
    members = [
        _m("AltLow", is_main=False, mythic_plus_score=1000, class_name="Mage"),
        _m("MainChar", is_main=True, mythic_plus_score=500, class_name="Paladin"),
        _m("AltHigh", is_main=False, mythic_plus_score=2000, class_name="Druid"),
    ]
    embed = me_card(members, display_name="Harry")
    names = [f.name for f in embed.fields]
    assert names[0].startswith("MainChar")        # main always leads
    assert "main" in names[0]
    assert names[1].startswith("AltHigh")          # then alts by score desc
    assert names[2].startswith("AltLow")


def test_me_card_shows_key_stats_and_vault_fallback():
    members = [
        _m("Geared", is_main=True, class_name="Warrior", active_spec="Fury",
           item_level=636.0, mythic_plus_score=3100.0, vault_slots_filled=6,
           wishlist_pct=42.0),
        _m("Fresh", is_main=False, class_name="Priest"),  # no stats yet
    ]
    embed = me_card(members, display_name="Harry")
    geared = embed.fields[0].value
    assert "ilvl 636" in geared and "M+ 3100" in geared
    assert "vault 6/9" in geared and "wishlist 42%" in geared
    fresh = embed.fields[1].value
    assert "vault –" in fresh  # missing vault falls back to an en dash, never em
    assert "wishlist" not in fresh  # omitted when unknown


def test_me_card_title_has_no_em_dash():
    embed = me_card([_m("Solo", is_main=True)], display_name="Harry")
    assert not brand.has_em_dash(embed.title)


def test_me_card_caps_fields_at_ten():
    many = [_m(f"Char{i}", is_main=(i == 0), mythic_plus_score=float(i)) for i in range(15)]
    embed = me_card(many, display_name="Harry")
    assert len(embed.fields) == 10


def _notes_fields(embed):
    return [f for f in embed.fields if f.name == "Notes"]


def test_member_embed_hides_notes_by_default():
    # Public /member lookups must not leak officer-owned notes.
    m = _m("Calcifer", notes="bench candidate — attitude issues")
    embed = member_embed(m)
    assert _notes_fields(embed) == []


def test_member_embed_shows_notes_for_officers():
    m = _m("Calcifer", notes="bench candidate")
    embed = member_embed(m, include_private=True)
    assert [f.value for f in _notes_fields(embed)] == ["bench candidate"]


# --- embed-limit safety -----------------------------------------------------


def test_clip_passes_short_and_truncates_long_with_ellipsis():
    assert clip("short", 256) == "short"
    out = clip("x" * 500, 256)
    assert len(out) == 256
    assert out.endswith("…")


def test_member_embed_clamps_an_overlong_notes_field():
    m = _m("Verbose", notes="n" * 5000)
    embed = member_embed(m, include_private=True)
    notes = next(f for f in embed.fields if f.name == "Notes")
    assert len(notes.value) <= FIELD_VALUE_LIMIT


def test_roster_embed_clamps_an_overlong_title():
    embed = roster_embed([_m("A")], title="T" * 1000, color=0)
    assert len(embed.title) <= TITLE_LIMIT


# --- member_embed optional fields -------------------------------------------


def test_member_embed_renders_thumbnail_parse_and_discord_fields():
    # A fully-populated member exercises the optional thumbnail, best-parse and
    # Discord-mention branches of the card.
    m = _m(
        "Calcifer", class_name="Mage", active_spec="Fire",
        item_level=636.0, mythic_plus_score=3100,
        thumbnail_url="https://img/calcifer.png",
        best_parse_avg=88.0, discord_id="12345", is_main=True,
    )
    embed = member_embed(m)
    assert embed.thumbnail.url == "https://img/calcifer.png"
    by_name = {f.name: f.value for f in embed.fields}
    assert "88" in by_name["Best Parse Avg"]
    assert by_name["Discord"] == "<@12345>"


def test_member_embed_falls_back_to_raid_progression_string():
    # No structured raid_progression in details, but a summary string is set:
    # the card shows the plain "Raid" field instead of per-raid lines.
    m = _m("Slacker", raid_progression="Voidspire: 8/8 N, 2/8 M")
    embed = member_embed(m)
    raid = next(f for f in embed.fields if f.name == "Raid")
    assert raid.value == "Voidspire: 8/8 N, 2/8 M"


# --- roster_embed empty + truncation paths ----------------------------------


def test_roster_embed_empty_says_no_members():
    embed = roster_embed([], title="Roster", color=0)
    assert embed.description == "No matching members."
    assert embed.footer.text is None


def test_roster_embed_truncates_and_reports_total():
    members = [_m(f"Char{i}") for i in range(30)]
    embed = roster_embed(members, title="Roster", color=0, per_page=25)
    assert "Showing 25 of 30 members." in embed.footer.text
