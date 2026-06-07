"""Phase 3 tests: Raider.IO M+ feed + Raid-Helper webhook receiver."""

import asyncio
import dataclasses

from fastapi.testclient import TestClient

from ftabot.config import Config
from ftabot.db import Database
from ftabot.feeds.raiderio_mplus import RaiderIOMythicSource, _run_event, _select_members
from ftabot.models import Member
from ftabot.services.raidhelper import parse_webhook
from ftabot.web import create_app

# --- Raider.IO M+ source ----------------------------------------------------


def test_select_members_prefers_raiders_then_top_score():
    members = [
        Member("Casual1", mythic_plus_score=3500),
        Member("Raider1", mythic_plus_score=1000, raid_team="Main"),
        Member("Casual2", mythic_plus_score=2000),
    ]
    picked = _select_members(members, cap=2)
    assert picked[0].character_name == "Raider1"  # raider first regardless of score
    assert picked[1].character_name == "Casual1"  # then highest score
    assert len(picked) == 2


def test_select_members_keeps_same_name_different_realm():
    # Two characters named "Asha" on different realms are distinct people.
    members = [
        Member("Asha", realm_slug="hellfire", region="eu", mythic_plus_score=2000),
        Member("Asha", realm_slug="ravencrest", region="eu", mythic_plus_score=1500),
    ]
    picked = _select_members(members, cap=5)
    assert len(picked) == 2


def test_run_event_titles_a_timed_key():
    m = Member("Bob", realm_slug="ravencrest")
    event = _run_event(m, {"level": 20, "upgrades": 2, "dungeon": "Ara-Kara",
                            "url": "https://raider.io/run/1", "score": 180})
    assert event.source == "raiderio_mplus"
    assert "+20 Ara-Kara" in event.title
    assert event.external_id == "https://raider.io/run/1"


class _FakeRIO:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def recent_runs(self, name, realm, region):
        return [
            {"level": 22, "upgrades": 1, "dungeon": "City", "url": f"u/{name}/22", "score": 200},
            {"level": 10, "upgrades": 3, "dungeon": "Low", "url": f"u/{name}/10", "score": 80},
            {"level": 25, "upgrades": 0, "dungeon": "Depleted", "url": f"u/{name}/25"},  # not timed
        ]


def test_mplus_source_emits_only_high_timed_keys(tmp_path, monkeypatch):
    import ftabot.feeds.raiderio_mplus as mod

    async def run():
        path = str(tmp_path / "m.db")
        db = Database(path)
        await db.connect()
        await db.upsert_synced(Member("Raider1", raid_team="Main", mythic_plus_score=2500))
        await db.close()

        cfg = dataclasses.replace(Config(), database_path=path,
                                  mplus_alert_min_level=18, mplus_feed_member_cap=10)
        monkeypatch.setattr(mod, "RaiderIOClient", _FakeRIO)
        source = RaiderIOMythicSource(cfg)
        return await source.fetch(session=None)

    events = asyncio.run(run())
    # +22: above floor (18) → run lane (realtime alert); +25: depleted (upgrades=0) → dropped.
    # +10: timed but sub-floor → log lane (digest-mode fodder), not dropped.
    run_events = [e for e in events if e.event_type == "raiderio.mplus-run"]
    log_events = [e for e in events if e.event_type == "raiderio.mplus-log"]
    assert [e.extra["level"] for e in run_events] == [22]
    assert [e.extra["level"] for e in log_events] == [10]


# --- Raid-Helper webhook parsing -------------------------------------------


def test_parse_webhook_created_and_deleted():
    created = parse_webhook(
        {"type": "create", "event": {"id": "evt1", "title": "Heroic Night",
                                     "startTime": 1735000000, "leaderName": "Harl",
                                     "signUpCount": 18}}
    )
    assert created.event_type == "raidhelper.created"
    assert created.external_id == "raidhelper-evt1-created"
    assert "Heroic Night" in created.title
    assert created.severity == "info"

    deleted = parse_webhook({"action": "delete", "event": {"id": "evt1", "title": "X"}})
    assert deleted.event_type == "raidhelper.deleted"
    assert deleted.severity == "warning"


def test_parse_webhook_needs_an_id():
    assert parse_webhook({"type": "create", "event": {"title": "no id"}}) is None


def test_parse_webhook_flat_payload():
    event = parse_webhook({"id": 99, "name": "Flat Event"}, action="updated")
    assert event.external_id == "raidhelper-99-updated"
    assert "Flat Event" in event.title


# --- Raid-Helper webhook endpoint ------------------------------------------


def _webhook_app(tmp_path, **overrides):
    path = str(tmp_path / "wh.db")

    async def init():
        db = Database(path)
        await db.connect()
        await db.close()

    asyncio.run(init())
    fields = {
        "database_path": path,
        "persona_tone": "off",
        "raidhelper_webhook_secret": "s3cret",
        "raidhelper_default_channel_id": "555",
        **overrides,
    }
    return create_app(dataclasses.replace(Config(), **fields))


_BODY = {"type": "create", "event": {"id": "evt-7", "title": "Mythic Prog"}}


def test_webhook_rejects_bad_secret(tmp_path):
    with TestClient(_webhook_app(tmp_path)) as client:
        assert client.post("/webhooks/raidhelper?token=wrong", json=_BODY).status_code == 401


def test_webhook_disabled_without_secret(tmp_path):
    app = _webhook_app(tmp_path, raidhelper_webhook_secret=None)
    with TestClient(app) as client:
        assert client.post("/webhooks/raidhelper?token=x", json=_BODY).status_code == 503


def test_webhook_queues_then_dedups(tmp_path):
    with TestClient(_webhook_app(tmp_path)) as client:
        # No bot sender wired in a bare web app -> the event queues for the loop.
        r1 = client.post("/webhooks/raidhelper?token=s3cret", json=_BODY)
        assert r1.status_code == 200 and r1.json()["status"] == "queued"
        # Same event again -> deduped, not re-queued.
        r2 = client.post("/webhooks/raidhelper?token=s3cret", json=_BODY)
        assert r2.json()["status"] == "duplicate"


def test_webhook_posts_when_sender_present(tmp_path):
    app = _webhook_app(tmp_path)
    sent = []

    async def fake_send(channel_id, embed, content=None):
        sent.append((channel_id, embed.title))

    with TestClient(app) as client:
        app.state.feed_sender = fake_send
        r = client.post("/webhooks/raidhelper?token=s3cret", json=_BODY)
        assert r.json()["status"] == "posted"
    assert sent and sent[0][0] == "555"
