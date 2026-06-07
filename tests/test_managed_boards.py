"""Guides & Resources + Rules & Info managed-content boards (offline)."""

from __future__ import annotations

from ftabot import brand, content
from ftabot.boards import REGISTRY
from ftabot.boards.guides import GUIDES_TYPE, GuidesBoard
from ftabot.boards.rules import RULES_TYPE, RulesInfoBoard
from ftabot.config import Config
from ftabot.models import Member

# --- content loader ---------------------------------------------------------


def test_content_loads_bundled_guides_and_rules():
    guides = content.load("guides")
    rules = content.load("rules")
    assert guides.get("sections") and rules.get("sections")


def test_content_load_missing_returns_default():
    assert content.load("does-not-exist", {"sections": []}) == {"sections": []}


# --- guides board -----------------------------------------------------------

_GUIDES = {
    "title": "Guides & Resources",
    "intro": "Tidy links.",
    "sections": [
        {"name": "Raiding", "emoji": "⚔️", "links": [
            {"label": "Warcraft Logs", "url": "https://wcl.gg"}]},
        {"name": "Empty", "emoji": "🛠️", "links": []},
    ],
}


def test_guides_board_is_registered():
    assert isinstance(REGISTRY.get(GUIDES_TYPE), GuidesBoard)


async def test_guides_renders_masked_links_and_skips_empty_sections(db):
    payload = await GuidesBoard().render(db, Config(), {"content": _GUIDES})
    desc = payload.embed.description
    assert "[Warcraft Logs](https://wcl.gg)" in desc
    assert "Raiding" in desc
    assert "Empty" not in desc  # a section with no links is dropped


async def test_guides_posts_nothing_without_sections(db):
    assert await GuidesBoard().render(db, Config(), {"content": {"sections": []}}) is None


async def test_guides_bundled_content_renders(db):
    # The shipped guides.json renders cleanly with no em dashes in the framing.
    payload = await GuidesBoard().render(db, Config(), {})
    assert payload is not None
    assert not brand.has_em_dash(payload.embed.title)
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_guides_fingerprint_changes_with_content(db):
    board = GuidesBoard()
    p1 = await board.render(db, Config(), {"content": _GUIDES})
    p2 = await board.render(db, Config(), {"content": _GUIDES})
    assert p1.fingerprint == p2.fingerprint
    changed = {**_GUIDES, "title": "New Title"}
    p3 = await board.render(db, Config(), {"content": changed})
    assert p3.fingerprint != p1.fingerprint


# --- rules board (live tokens) ----------------------------------------------

_RULES = {
    "title": "Rules & Info",
    "sections": [
        {"heading": "Who we are", "body": "We are {member_count} strong, {active_pct} active."},
        {"heading": "Raiding", "body": "Progress: {progression}."},
        {"heading": "Recruiting", "body": "Open: {recruiting}."},
        {"heading": "Unknown", "body": "Keeps {mystery} visible."},
    ],
}


def test_rules_board_is_registered():
    assert isinstance(REGISTRY.get(RULES_TYPE), RulesInfoBoard)


async def test_rules_substitutes_live_tokens(db):
    await db.upsert_synced(Member("Emberlyn", raid_progression="8/8 N, 3/8 M"))
    await db.upsert_synced(Member("Ashbringer"))
    cfg = Config(recruit_roles_open=("HEALER", "DPS"))
    payload = await RulesInfoBoard().render(db, cfg, {"content": _RULES})
    desc = payload.embed.description
    assert "2 strong" in desc                 # member_count
    assert "8/8 N, 3/8 M" in desc             # progression from the furthest member
    assert "Healer, Dps" in desc              # recruiting from config
    assert "{mystery}" in desc                # unknown token left visible, no crash


async def test_rules_posts_nothing_without_sections(db):
    assert await RulesInfoBoard().render(db, Config(), {"content": {"sections": []}}) is None


async def test_rules_framing_has_no_em_dash(db):
    await db.upsert_synced(Member("Solo"))
    payload = await RulesInfoBoard().render(db, Config(), {"content": _RULES})
    assert not brand.has_em_dash(payload.embed.title)
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_rules_fingerprint_tracks_live_data(db):
    await db.upsert_synced(Member("One"))
    board = RulesInfoBoard()
    p1 = await board.render(db, Config(), {"content": _RULES})
    await db.upsert_synced(Member("Two"))  # member_count token changes
    p2 = await board.render(db, Config(), {"content": _RULES})
    assert p1.fingerprint != p2.fingerprint
