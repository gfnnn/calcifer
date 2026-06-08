"""Edge-case coverage for the targeted M+ score refresh (ftabot/scores.py).

The selection logic and faked-client happy path are covered in test_scores.py;
this adds the duplicate-character dedup, the owns-its-own-session path, the
per-character error swallow, and refresh_boards' unknown-type skip + per-board
error swallow — all offline by stubbing the Raider.IO enrich call and the board
refresh.
"""

from __future__ import annotations

import dataclasses

from ftabot import scores
from ftabot.config import Config
from ftabot.models import Member
from ftabot.services.raiderio import RaiderIOClient

CFG = Config()


# --- select_active dedup ----------------------------------------------------


def test_select_active_dedups_same_character():
    a = Member("Ashbringer", class_name="Paladin", mythic_plus_score=3000)
    dup = Member("ashbringer", class_name="Paladin", mythic_plus_score=3000)  # same key
    picked = scores.select_active([a, dup], CFG)
    assert len(picked) == 1


# --- refresh_scores ---------------------------------------------------------


async def test_refresh_scores_owns_session_and_reports_changes(db, monkeypatch):
    member = Member("Emberlyn", class_name="Mage", mythic_plus_score=3000)
    await db.upsert_synced(member)

    async def _enrich(self, m):
        m.mythic_plus_score = 3500  # a real change

    monkeypatch.setattr(RaiderIOClient, "enrich_character", _enrich)

    # session=None -> refresh_scores creates and closes its own ClientSession.
    changed = await scores.refresh_scores(db, CFG, [member])
    assert [m.character_name for m in changed] == ["Emberlyn"]
    stored = await db.get_member("Emberlyn", member.realm_slug, member.region)
    assert stored.mythic_plus_score == 3500


async def test_refresh_scores_swallows_a_per_character_error(db, monkeypatch, caplog):
    import logging

    member = Member("Emberlyn", class_name="Mage", mythic_plus_score=3000)
    await db.upsert_synced(member)

    async def _boom(self, m):
        raise RuntimeError("raider.io 500")

    monkeypatch.setattr(RaiderIOClient, "enrich_character", _boom)
    caplog.set_level(logging.WARNING)

    changed = await scores.refresh_scores(db, CFG, [member])
    assert changed == []  # the failure didn't count as a change
    assert "score refresh failed" in caplog.text


async def test_refresh_scores_empty_is_a_noop(db):
    assert await scores.refresh_scores(db, CFG, []) == []


# --- refresh_boards ---------------------------------------------------------


async def test_refresh_boards_renders_known_skips_unknown(db, monkeypatch):
    await db.add_board("hot20", "100")
    await db.add_board("bogus_type", "200")  # not in REGISTRY -> skipped

    rendered_rows = []

    async def _fake_refresh(database, config, board, row, messenger, gate):
        rendered_rows.append(row["board_type"])
        return "unchanged"

    monkeypatch.setattr("ftabot.boards.refresh_board", _fake_refresh)
    n = await scores.refresh_boards(db, CFG, bot=object())
    assert n == 1  # only the known board rendered
    assert rendered_rows == ["hot20"]


async def test_refresh_boards_swallows_a_per_board_error(db, monkeypatch, caplog):
    import logging

    await db.add_board("hot20", "100")

    async def _boom(database, config, board, row, messenger, gate):
        raise RuntimeError("discord edit failed")

    monkeypatch.setattr("ftabot.boards.refresh_board", _boom)
    caplog.set_level(logging.WARNING)

    n = await scores.refresh_boards(db, CFG, bot=object())
    assert n == 0  # the failing board didn't increment the count
    assert "board refresh failed" in caplog.text


def test_config_replace_smoke():
    # Guards the CFG module global the tests lean on.
    assert isinstance(dataclasses.replace(CFG, posting_mode="off"), Config)
