"""Calcifer's Hot 20 board: top-20 rendering, movement, and diff stability."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ftabot import brand
from ftabot.boards import REGISTRY
from ftabot.boards.hot20 import HOT20_TYPE, Hot20Board, weekly_reset_before
from ftabot.config import Config
from ftabot.models import Member


async def _seed(db, scored: list[tuple[str, float]]):
    for name, score in scored:
        await db.upsert_synced(
            Member(name, class_name="Mage", role="DPS", mythic_plus_score=score)
        )


def test_hot20_is_registered():
    assert isinstance(REGISTRY.get(HOT20_TYPE), Hot20Board)


async def test_renders_only_the_top_20_by_score(db):
    # 25 members; the board should show the highest 20
    await _seed(db, [(f"Char{i:02d}", float(i * 100)) for i in range(1, 26)])

    payload = await Hot20Board().render(db, Config(), {})

    assert payload is not None
    desc = payload.embed.description
    # 20 ranked rows (each begins with a `rank` cell)
    ranked_lines = [line for line in desc.splitlines() if line.startswith("`")]
    assert len(ranked_lines) == 20
    # the highest scorer leads and is highlighted
    assert "Char25" in desc
    assert "Topping the embers" in desc
    # the lowest five are cut
    assert "Char05" not in desc and "Char01" not in desc


async def test_no_scored_members_posts_nothing(db):
    await db.upsert_synced(Member("Unranked", mythic_plus_score=None))
    assert await Hot20Board().render(db, Config(), {}) is None


async def test_output_never_contains_an_em_dash(db):
    await _seed(db, [(f"Char{i}", float(i * 100)) for i in range(1, 6)])
    payload = await Hot20Board().render(db, Config(), {})
    assert not brand.has_em_dash(payload.embed.description)
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_fingerprint_is_stable_until_data_changes(db):
    await _seed(db, [("Emberlyn", 3200.0), ("Ashbringer", 3000.0)])
    board = Hot20Board()

    await board.render(db, Config(), {})  # first render seeds the baseline
    p1 = await board.render(db, Config(), {})
    p2 = await board.render(db, Config(), {})
    assert p1.fingerprint == p2.fingerprint  # unchanged -> identical (no needless edit)

    await db.upsert_synced(Member("Emberlyn", class_name="Mage", role="DPS", mythic_plus_score=3500.0))
    p3 = await board.render(db, Config(), {})
    assert p3.fingerprint != p2.fingerprint  # a score change moves the fingerprint


async def test_movement_arrows_track_rank_changes(db):
    await _seed(db, [("Emberlyn", 3000.0), ("Ashbringer", 2000.0)])
    board = Hot20Board()

    # first render seeds the baseline (Emberlyn #1, Ashbringer #2)
    await board.render(db, Config(), {})

    # Ashbringer overtakes Emberlyn
    await db.upsert_synced(Member("Ashbringer", class_name="Mage", role="DPS", mythic_plus_score=4000.0))
    payload = await board.render(db, Config(), {})
    desc = payload.embed.description

    # Ashbringer climbed to #1 (▲), Emberlyn slipped to #2 (▼).
    # Match the ranked rows (they start with a `rank` cell), not the highlight.
    rows = [line for line in desc.splitlines() if line.startswith("`")]
    ash_line = next(line for line in rows if "Ashbringer" in line)
    ember_line = next(line for line in rows if "Emberlyn" in line)
    assert "▲" in ash_line
    assert "▼" in ember_line


def test_weekly_reset_before_lands_on_wednesday_morning():
    tz = ZoneInfo("Europe/Paris")
    # A Friday: the most recent reset is the Wednesday 07:00 just before it.
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    reset = weekly_reset_before(now, tz)
    local = reset.astimezone(tz)
    assert local.weekday() == 2  # Wednesday
    assert local.hour == 7
    assert reset <= now


def test_weekly_reset_before_wednesday_pre_reset_uses_last_week():
    tz = ZoneInfo("Europe/Paris")
    # Wednesday 04:00 UTC is 06:00 Paris (CEST), before the 07:00 reset, so the
    # boundary is the previous Wednesday, not today.
    now = datetime(2026, 6, 10, 4, 0, tzinfo=timezone.utc)
    reset = weekly_reset_before(now, tz)
    assert reset < now
    assert (now - reset).days >= 6


async def test_baseline_rolls_after_a_weekly_reset(db):
    await _seed(db, [("Emberlyn", 3000.0), ("Ashbringer", 2000.0)])
    board = Hot20Board()
    await board.render(db, Config(), {})  # seeds the baseline at "now"

    # Backdate the baseline so a Wednesday reset has since passed.
    await db.conn.execute(
        "UPDATE board_baselines SET captured_at = ? WHERE board_type = ?",
        ("2020-01-01T00:00:00+00:00", HOT20_TYPE),
    )
    await db.conn.commit()

    await board.render(db, Config(), {})  # this render should roll the baseline
    _ranks, captured = await db.board_baseline(HOT20_TYPE)
    assert captured != "2020-01-01T00:00:00+00:00"  # rolled forward to now
