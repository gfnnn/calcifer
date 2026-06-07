"""Calendar events store + the Weekly Events living board (offline)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ftabot import brand
from ftabot.boards import REGISTRY
from ftabot.boards.events import EVENTS_TYPE, WeeklyEventsBoard
from ftabot.config import Config
from ftabot.db import Database
from ftabot.services.raidhelper import parse_webhook
from ftabot.web import create_app

_NOW = int(datetime.now(timezone.utc).timestamp())
_DAY = 86400


# --- parse_webhook now exposes structured fields ----------------------------


def test_parse_webhook_exposes_structured_extra():
    event = parse_webhook({
        "type": "create",
        "event": {"id": "e1", "title": "Heroic Night", "startTime": 1735000000,
                  "leaderName": "Harl", "signUpCount": 18, "url": "https://rh/e1"},
    })
    ex = event.extra
    assert ex["start_epoch"] == 1735000000
    assert ex["signup_count"] == 18
    assert ex["title_plain"] == "Heroic Night"
    assert ex["leader"] == "Harl"
    assert ex["url"] == "https://rh/e1"


def test_parse_webhook_tolerates_missing_time():
    event = parse_webhook({"id": "e2", "name": "No time"}, action="created")
    assert event.extra["start_epoch"] is None
    assert event.extra["signup_count"] is None


# --- DB: upsert / upcoming --------------------------------------------------


async def test_upsert_is_latest_wins(db):
    await db.upsert_calendar_event("e1", title="Night", start_epoch=_NOW + _DAY,
                                   signup_count=5)
    await db.upsert_calendar_event("e1", title="Night", start_epoch=_NOW + _DAY,
                                   signup_count=12)
    rows = await db.upcoming_events(now_epoch=_NOW)
    assert len(rows) == 1 and rows[0]["signup_count"] == 12


async def test_upcoming_filters_window_status_and_past(db):
    await db.upsert_calendar_event("soon", title="Soon", start_epoch=_NOW + _DAY)
    await db.upsert_calendar_event("far", title="Far", start_epoch=_NOW + 30 * _DAY)
    await db.upsert_calendar_event("past", title="Past", start_epoch=_NOW - _DAY)
    await db.upsert_calendar_event("cancelled", title="Off", start_epoch=_NOW + _DAY,
                                   status="cancelled")
    await db.upsert_calendar_event("notime", title="No time", start_epoch=None)

    rows = await db.upcoming_events(now_epoch=_NOW, within_days=7)
    assert [r["event_id"] for r in rows] == ["soon"]
    # A wider horizon pulls in the far one, soonest first.
    rows = await db.upcoming_events(now_epoch=_NOW, within_days=60)
    assert [r["event_id"] for r in rows] == ["soon", "far"]


async def test_upsert_prunes_old_events(db):
    await db.upsert_calendar_event("old", title="Old", start_epoch=_NOW - 30 * _DAY)
    await db.upsert_calendar_event("new", title="New", start_epoch=_NOW + _DAY,
                                   prune_before_epoch=_NOW - 14 * _DAY)
    # The old event (older than the prune horizon) is gone.
    cur = await db.conn.execute("SELECT COUNT(*) AS n FROM calendar_events")
    assert (await cur.fetchone())["n"] == 1


# --- the board --------------------------------------------------------------


def test_events_board_is_registered():
    assert isinstance(REGISTRY.get(EVENTS_TYPE), WeeklyEventsBoard)


async def test_board_posts_nothing_when_no_events(db):
    assert await WeeklyEventsBoard().render(db, Config(), {}) is None


async def test_board_renders_upcoming_with_stamp_and_jumplink(db):
    await db.upsert_calendar_event("e1", title="Mythic Prog", start_epoch=_NOW + _DAY,
                                   signup_count=14, url="https://rh/e1")
    payload = await WeeklyEventsBoard().render(db, Config(), {})
    assert payload is not None
    desc = payload.embed.description
    assert "[Mythic Prog](https://rh/e1)" in desc
    assert f"<t:{_NOW + _DAY}:F>" in desc
    assert "14 signed up" in desc
    assert "Sign up ➜" in desc


async def test_board_excludes_cancelled_and_past(db):
    await db.upsert_calendar_event("live", title="Live", start_epoch=_NOW + _DAY)
    await db.upsert_calendar_event("dead", title="Dead", start_epoch=_NOW + _DAY,
                                   status="cancelled")
    payload = await WeeklyEventsBoard().render(db, Config(), {})
    assert "Live" in payload.embed.description
    assert "Dead" not in payload.embed.description


async def test_board_framing_has_no_em_dash_but_title_is_verbatim(db):
    await db.upsert_calendar_event("e1", title="Raid — Edge of Chaos",
                                   start_epoch=_NOW + _DAY)
    payload = await WeeklyEventsBoard().render(db, Config(), {})
    # The relayed event title keeps its em dash; Calcifer's framing never has one.
    assert "Raid — Edge of Chaos" in payload.embed.description
    assert not brand.has_em_dash(payload.embed.title)
    assert not brand.has_em_dash(payload.embed.footer.text)


async def test_board_fingerprint_tracks_signups(db):
    await db.upsert_calendar_event("e1", title="N", start_epoch=_NOW + _DAY,
                                   signup_count=4)
    board = WeeklyEventsBoard()
    p1 = await board.render(db, Config(), {})
    await db.upsert_calendar_event("e1", title="N", start_epoch=_NOW + _DAY,
                                   signup_count=9)
    p2 = await board.render(db, Config(), {})
    assert p1.fingerprint != p2.fingerprint  # board edits as the night fills


# --- the webhook persists for the board -------------------------------------


def _webhook_app(tmp_path):
    path = str(tmp_path / "wh.db")

    async def init():
        async with Database(path):
            pass

    asyncio.run(init())
    return create_app(dataclasses.replace(
        Config(), database_path=path, persona_tone="off",
        raidhelper_webhook_secret="s3cret", raidhelper_default_channel_id="555",
    )), path


def test_webhook_persists_event_for_the_board(tmp_path):
    app, path = _webhook_app(tmp_path)
    body = {"type": "create", "event": {
        "id": "evt-9", "title": "Heroic Night", "startTime": _NOW + 2 * _DAY,
        "signUpCount": 7, "url": "https://rh/evt-9"}}
    with TestClient(app) as client:
        assert client.post("/webhooks/raidhelper?token=s3cret", json=body).status_code == 200

    async def check():
        async with Database(path) as db:
            return await db.upcoming_events(now_epoch=_NOW)

    rows = asyncio.run(check())
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-9" and rows[0]["signup_count"] == 7
