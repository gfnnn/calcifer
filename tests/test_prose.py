"""Managed-prose token helpers + the content loader (offline).

These back the Rules & Info / Guides & Resources sticky pages and the
Recruitment board's live ``{tokens}``. The helpers used to live in the (now
removed) ``boards/rules`` module; they were centralised into ``ftabot.prose``
when the duplicate Rules/Guides *boards* were retired in favour of the pages.
"""

from __future__ import annotations

from ftabot import content
from ftabot.config import Config
from ftabot.models import Member
from ftabot.prose import SafeDict, live_tokens, safe_format

# --- content loader ---------------------------------------------------------


def test_content_loads_bundled_pages_and_recruitment():
    rules = content.load("page_rules_info")
    guides = content.load("page_guides_resources")
    recruitment = content.load("recruitment")
    assert rules.get("posts") and guides.get("posts")
    assert recruitment  # the recruitment board's bundled copy


def test_content_load_missing_returns_default():
    assert content.load("does-not-exist", {"posts": []}) == {"posts": []}


# --- token substitution -----------------------------------------------------


def test_safe_format_keeps_unknown_token_visible():
    tokens = SafeDict({"member_count": "42"})
    assert safe_format("We are {member_count} strong.", tokens) == "We are 42 strong."
    # An unknown token is left verbatim rather than raising.
    assert safe_format("Keeps {mystery} visible.", tokens) == "Keeps {mystery} visible."


def test_safe_format_falls_back_on_malformed_spec():
    # A stray brace would raise in str.format_map; we return the raw text instead.
    assert safe_format("100% { done", SafeDict({})) == "100% { done"


async def test_live_tokens_reflect_the_roster(db):
    await db.upsert_synced(Member("Emberlyn", raid_progression="8/8 N, 3/8 M"))
    await db.upsert_synced(Member("Ashbringer"))
    cfg = Config(recruit_roles_open=("HEALER", "DPS"))
    tokens = await live_tokens(db, cfg)
    assert tokens["member_count"] == "2"
    assert tokens["progression"] == "8/8 N, 3/8 M"
    assert tokens["recruiting"] == "Healer, Dps"


async def test_live_tokens_defaults_when_empty(db):
    tokens = await live_tokens(db, Config(recruit_roles_open=()))
    assert tokens["member_count"] == "0"
    assert tokens["progression"] == "early days yet"
    assert tokens["recruiting"] == "currently closed"
