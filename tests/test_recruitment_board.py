"""Recruitment board: renderer + the officer-configured state behind it."""

from __future__ import annotations

from ftabot import brand, content
from ftabot.boards import REGISTRY
from ftabot.boards.recruitment import RECRUITMENT_TYPE, RecruitmentBoard
from ftabot.config import Config
from ftabot.models import Member

_CONTENT = {
    "title": "We're Looking",
    "values_heading": "What we value in trials",
    "values": ["✓ Show up on time.", "✗ No drama."],
    "expectations_heading": "Trial expectations",
    "expectations": ["First 4 raids are a trial.", "Discord required."],
    "about_heading": "About us",
    "about": "We are {member_count} strong, progression {progression}.",
    "cta": "DM an officer. We'd love to hear from you.",
    "empty": "The guild's full for now. Always happy to hear from you.",
}


# --- db state round-trip ------------------------------------------------------


async def test_recruitment_state_defaults_empty(db):
    assert await db.recruitment_state() == {}


async def test_recruitment_state_round_trip(db):
    state = {"slots": {"tank": {"open": 1, "ilvl": 485}}, "rules": ["Be kind."]}
    await db.set_recruitment_state(state)
    assert await db.recruitment_state() == state
    # Overwrites, not appends: there is exactly one state row.
    await db.set_recruitment_state({})
    assert await db.recruitment_state() == {}


async def test_recruitment_state_survives_malformed_json(db):
    await db.conn.execute(
        "INSERT OR REPLACE INTO recruitment_config (id, state_json) VALUES (1, 'not json')"
    )
    await db.conn.commit()
    assert await db.recruitment_state() == {}


async def test_recruitment_state_ignores_non_dict_json(db):
    await db.conn.execute(
        "INSERT OR REPLACE INTO recruitment_config (id, state_json) VALUES (1, '[1, 2]')"
    )
    await db.conn.commit()
    assert await db.recruitment_state() == {}


# --- renderer -----------------------------------------------------------------


def test_recruitment_board_is_registered():
    assert isinstance(REGISTRY.get(RECRUITMENT_TYPE), RecruitmentBoard)


def test_bundled_recruitment_content_loads():
    data = content.load("recruitment")
    assert data.get("values") and data.get("expectations") and data.get("about")


async def test_empty_state_is_warm_and_keeps_the_pitch(db):
    await db.upsert_synced(Member("Emberlyn"))
    payload = await RecruitmentBoard().render(db, Config(), {"content": _CONTENT})
    desc = payload.embed.description
    assert "The guild's full for now" in desc
    assert "1 strong" in desc  # the about pitch stays, with live tokens
    assert "TANK" not in desc  # no slot rows
    assert payload.embed.title.endswith("We're Looking")


async def test_open_slots_render_with_ilvl_and_note(db):
    await db.set_recruitment_state(
        {
            "slots": {
                "tank": {"open": 1, "ilvl": 485, "note": "CE-capable"},
                "healer": {"open": 2},
                "dps": {"open": 0},
            }
        }
    )
    payload = await RecruitmentBoard().render(db, Config(), {"content": _CONTENT})
    desc = payload.embed.description
    assert "**TANK**: 1 slot open · ilvl 485+ · CE-capable" in desc
    assert "**HEALER**: 2 slots open" in desc
    assert "DPS" not in desc  # closed role is hidden
    # Culture first: values render, then the bundled default expectations.
    assert "What we value in trials" in desc
    assert "→ First 4 raids are a trial." in desc
    assert "DM an officer." in desc  # the CTA


async def test_officer_rules_replace_default_expectations(db):
    await db.set_recruitment_state(
        {"slots": {"dps": {"open": 3}}, "rules": ["Raids start 20:30 sharp."]}
    )
    payload = await RecruitmentBoard().render(db, Config(), {"content": _CONTENT})
    desc = payload.embed.description
    assert "→ Raids start 20:30 sharp." in desc
    assert "First 4 raids" not in desc


async def test_message_override_replaces_the_about_pitch(db):
    await db.set_recruitment_state(
        {"slots": {"tank": {"open": 1}}, "message": "Custom pitch here."}
    )
    payload = await RecruitmentBoard().render(db, Config(), {"content": _CONTENT})
    assert "Custom pitch here." in payload.embed.description
    assert "strong, progression" not in payload.embed.description


async def test_bundled_content_renders_without_em_dashes(db):
    await db.set_recruitment_state({"slots": {"healer": {"open": 1}}})
    payload = await RecruitmentBoard().render(db, Config(), {})
    assert payload is not None
    assert not brand.has_em_dash(payload.embed.title)
    assert not brand.has_em_dash(payload.embed.description)
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_fingerprint_tracks_state_not_flavour(db):
    board = RecruitmentBoard()
    await db.set_recruitment_state({"slots": {"tank": {"open": 1}}})
    # Random flavour/signoff must not move the fingerprint: render twice in the
    # chattiest tone and once silent, expecting one stable fingerprint.
    p1 = await board.render(db, Config(persona_tone="full"), {"content": _CONTENT})
    p2 = await board.render(db, Config(persona_tone="full"), {"content": _CONTENT})
    p3 = await board.render(db, Config(persona_tone="off"), {"content": _CONTENT})
    assert p1.fingerprint == p2.fingerprint == p3.fingerprint

    await db.set_recruitment_state({"slots": {"tank": {"open": 2}}})
    p4 = await board.render(db, Config(persona_tone="off"), {"content": _CONTENT})
    assert p4.fingerprint != p1.fingerprint


async def test_garbage_slot_counts_are_treated_as_closed(db):
    await db.set_recruitment_state({"slots": {"tank": {"open": "lots"}}})
    payload = await RecruitmentBoard().render(db, Config(), {"content": _CONTENT})
    assert "The guild's full for now" in payload.embed.description
