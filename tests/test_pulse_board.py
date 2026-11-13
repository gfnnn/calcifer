"""Roster Pulse board: ambient guild vitals (offline)."""

from __future__ import annotations

from ftabot import brand
from ftabot.boards import REGISTRY
from ftabot.boards.pulse import PULSE_TYPE, RosterPulseBoard
from ftabot.config import Config
from ftabot.models import Member


async def _seed(db, specs):
    for name, role, ilvl, score in specs:
        await db.upsert_synced(
            Member(name, class_name="Mage", role=role, item_level=ilvl,
                   mythic_plus_score=score)
        )


def test_pulse_board_is_registered():
    assert isinstance(REGISTRY.get(PULSE_TYPE), RosterPulseBoard)


async def test_pulse_posts_nothing_when_empty(db):
    assert await RosterPulseBoard().render(db, Config(), {}) is None


async def test_pulse_renders_vitals(db):
    await _seed(db, [
        ("Tankie", "TANK", 630.0, 2800.0),
        ("Healz", "HEALER", 628.0, 2600.0),
        ("Stabby", "DPS", 632.0, 3200.0),
    ])
    payload = await RosterPulseBoard().render(db, Config(), {})
    assert payload is not None
    fields = {f.name: f.value for f in payload.embed.fields}
    assert fields["Members"] == "3"
    assert "Avg ilvl" in fields and fields["Avg ilvl"] == "630"
    assert "Roles" in fields  # the role split renders
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_pulse_fingerprint_moves_with_the_guild(db):
    await _seed(db, [("One", "DPS", 600.0, 1000.0)])
    board = RosterPulseBoard()
    p1 = await board.render(db, Config(), {})
    p2 = await board.render(db, Config(), {})
    assert p1.fingerprint == p2.fingerprint  # steady state -> no needless edit
    await _seed(db, [("Two", "TANK", 640.0, 3000.0)])  # roster grows
    p3 = await board.render(db, Config(), {})
    assert p3.fingerprint != p1.fingerprint


async def test_pulse_shows_progression_when_known(db):
    # A member carrying a raid_progression summary surfaces the guild's
    # progression field (the `if progression:` branch).
    await db.upsert_synced(
        Member("Pusher", class_name="Mage", role="DPS", item_level=630.0,
               mythic_plus_score=3000.0, raid_progression="4/8 M")
    )
    payload = await RosterPulseBoard().render(db, Config(), {})
    fields = {f.name: f.value for f in payload.embed.fields}
    assert "Progression" in fields
    assert "4/8 M" in fields["Progression"]
    assert not brand.has_em_dash(fields["Progression"])


async def test_pulse_averages_ignore_dormant_characters(db):
    # A character not seen for a year keeps old-scale gear and no Mythic+ score;
    # counting it would describe nobody who is actually playing.
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    await db.upsert_synced(
        Member("Playing", role="DPS", item_level=330.0, mythic_plus_score=3000.0,
               last_crawled_at=(now - timedelta(days=1)).isoformat())
    )
    await db.upsert_synced(
        Member("Dormant", role="DPS", item_level=740.0, mythic_plus_score=0.0,
               last_crawled_at=(now - timedelta(days=400)).isoformat())
    )
    fields = {
        f.name: f.value
        for f in (await RosterPulseBoard().render(db, Config(), {})).embed.fields
    }
    assert fields["Members"] == "2"
    assert fields["Active"] == "1 (50%)"
    assert fields["Avg ilvl"] == "330"
    assert fields["Avg M+"] == "3000"
