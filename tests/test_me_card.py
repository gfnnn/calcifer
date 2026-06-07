"""The personal /me card renderer (pure, offline)."""

from __future__ import annotations

from ftabot import brand
from ftabot.models import Member
from ftabot.ui import me_card, member_embed


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
