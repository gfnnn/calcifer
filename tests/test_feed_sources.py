"""Coverage for the two laggard feed sources (wowaudit_raids, wcl_prog) and the
runner's source-builder + a couple of poll_once edge branches.

The parsing/building functions in these modules are pure, so they're tested
directly with canned dicts (the same fixture-driven style as test_raiderio_*).
The network-bound ``fetch`` methods are only exercised on their config-disabled
short-circuit (returns ``[]``) — the live paths need real API clients and are
out of scope for the offline suite.
"""

from __future__ import annotations

import dataclasses

from ftabot.config import Config
from ftabot.feeds.runner import build_sources, poll_once
from ftabot.feeds.wcl_prog import (
    WclProgSource,
    _iso_from_ms,
    _remaining_pct,
    prog_events,
    summarize_report,
)
from ftabot.feeds.wowaudit_raids import WowAuditRaidSource, _epoch, _reminder_event

from .test_feeds import _event, _FakeSource, _Recorder

# --- wowaudit_raids: _epoch -------------------------------------------------


def test_epoch_parses_valid_iso():
    # Round-trips an ISO datetime to a positive epoch second.
    ts = _epoch("2026-06-06T20:00:00+00:00")
    assert isinstance(ts, int)
    assert ts > 0


def test_epoch_handles_none_and_garbage():
    assert _epoch(None) is None
    assert _epoch("") is None
    assert _epoch("not-a-date") is None


# --- wowaudit_raids: _reminder_event severities -----------------------------


def test_reminder_event_warns_when_undersigned():
    ev = _reminder_event(
        {"instance": "Manaforge", "difficulty": "Mythic", "date": "2026-06-06",
         "time": "20:00", "present": 10, "total": 20, "start": None}
    )
    assert ev.severity == "warning"  # 10/20 is below the 80% bar
    assert "10/20 signed up" in ev.summary
    assert ev.title == "🔥 Raid reminder: Manaforge Mythic"


def test_reminder_event_info_when_well_signed():
    ev = _reminder_event({"instance": "Manaforge", "present": 18, "total": 20})
    assert ev.severity == "info"
    assert "18/20 signed up" in ev.summary


def test_reminder_event_present_only_and_open():
    present_only = _reminder_event({"instance": "Manaforge", "present": 12, "total": None})
    assert present_only.severity == "info"
    assert "12 signed up" in present_only.summary

    open_signups = _reminder_event({"instance": "Manaforge"})
    assert "sign-ups open" in open_signups.summary


def test_reminder_event_uses_relative_timestamp_when_start_known():
    with_ts = _reminder_event({"instance": "Raid", "start": "2026-06-06T20:00:00+00:00"})
    assert "<t:" in with_ts.summary and ":R>" in with_ts.summary

    no_ts = _reminder_event({"instance": "Raid", "time": "Friday 20:00", "start": None})
    assert "Friday 20:00" in no_ts.summary


async def test_wowaudit_source_disabled_returns_empty():
    # No API key => wowaudit_enabled is False => fetch short-circuits.
    src = WowAuditRaidSource(Config())
    assert await src.fetch(session=None) == []


# --- wcl_prog: _remaining_pct -----------------------------------------------


def test_remaining_pct_prefers_boss_then_fight():
    assert _remaining_pct(4.2, 10.0) == 4.2  # boss% wins
    assert _remaining_pct(None, 12.0) == 12.0  # falls back to fight%
    assert _remaining_pct(None, None) is None


def test_remaining_pct_normalises_hundredths_and_skips_garbage():
    assert _remaining_pct(450, None) == 4.5  # >100 => divided by 100
    assert _remaining_pct("oops", 8.0) == 8.0  # bad value skipped, fight% used
    assert _remaining_pct(-5, None) == 0.0  # clamped at 0


# --- wcl_prog: summarize_report ---------------------------------------------


def test_summarize_report_tracks_best_pull_and_kills():
    fights = [
        {"name": "Boss A", "difficulty": "Mythic", "bossPercentage": 30},
        {"name": "Boss A", "difficulty": "Mythic", "bossPercentage": 12},  # better pull
        {"name": "Boss A", "difficulty": "Mythic", "kill": True},  # killed
        {"name": "Boss B", "difficulty": "Mythic", "fightPercentage": 55},
        {"name": None},  # nameless trash pull -> skipped
    ]
    rows = {(r["boss"], r["difficulty"]): r for r in summarize_report(fights)}
    a = rows[("Boss A", "Mythic")]
    assert a["pulls"] == 3
    assert a["killed"] is True
    assert a["best_pct"] == 0.0  # a kill sets best to 0
    b = rows[("Boss B", "Mythic")]
    assert b["killed"] is False
    assert b["best_pct"] == 55.0


# --- wcl_prog: prog_events --------------------------------------------------


def test_prog_events_emits_kill_and_close_pull_but_not_far_ones():
    bosses = [
        {"boss": "Boss A", "difficulty": "Mythic", "pulls": 8, "best_pct": 0.0, "killed": True},
        {"boss": "Boss B", "difficulty": "Mythic", "pulls": 20, "best_pct": 4.0, "killed": False},
        {"boss": "Boss C", "difficulty": "Mythic", "pulls": 3, "best_pct": 60.0, "killed": False},
        {"boss": "Boss D", "difficulty": "Mythic", "pulls": 1, "best_pct": None, "killed": False},
    ]
    events = prog_events("RPT1", bosses, threshold=30.0)
    types = {e.event_type for e in events}
    assert "wcl.kill" in types  # Boss A killed
    assert "wcl.best-pull" in types  # Boss B is within threshold
    # Boss C (60% > threshold) and Boss D (no best) produce nothing.
    assert len(events) == 2
    kill = next(e for e in events if e.event_type == "wcl.kill")
    assert "defeated" in kill.title
    best = next(e for e in events if e.event_type == "wcl.best-pull")
    assert best.external_id == "RPT1-Boss B-Mythic-best-0"  # 4.0 buckets to 0


def test_prog_events_buckets_by_five_percent():
    bosses = [
        {"boss": "Boss", "difficulty": "M", "pulls": 5, "best_pct": 12.0, "killed": False},
    ]
    (event,) = prog_events("RPT", bosses, threshold=30.0)
    assert event.external_id.endswith("best-10")  # 12.0 // 5 * 5 == 10


# --- wcl_prog: _iso_from_ms + disabled fetch --------------------------------


def test_iso_from_ms():
    assert _iso_from_ms(1780000000000).startswith("20")  # a real ISO timestamp
    assert _iso_from_ms(None) is None
    assert _iso_from_ms("nope") is None


async def test_wcl_source_disabled_returns_empty():
    # No WCL credentials => fetch short-circuits to [].
    src = WclProgSource(Config())
    assert await src.fetch(session=None) == []


# --- runner.build_sources ---------------------------------------------------


def test_build_sources_empty_when_all_disabled():
    cfg = dataclasses.replace(
        Config(), wowhead_enabled=False, blizzard_news_enabled=False
    )
    assert build_sources(cfg) == []


def test_build_sources_includes_every_enabled_source():
    cfg = dataclasses.replace(
        Config(),
        wowhead_enabled=True,
        blizzard_news_enabled=True,
        mplus_feed_enabled=True,
        raid_reminder_enabled=True,
        wowaudit_api_key="key",  # makes wowaudit_enabled True
        prog_feed_enabled=True,
        warcraftlogs_client_id="cid",
        affixes_feed_enabled=True,
        realm_progress_feed_enabled=True,
    )
    names = {s.name for s in build_sources(cfg)}
    assert names == {
        "wowhead",
        "blizzard",
        "raiderio_mplus",
        "wowaudit_raids",
        "wcl_prog",
        "raiderio_affixes",
        "raiderio_realm_progress",
    }


def test_build_sources_builds_realm_progress_without_pinned_raids():
    cfg = dataclasses.replace(
        Config(), wowhead_enabled=False, blizzard_news_enabled=False,
        realm_progress_feed_enabled=True, realm_progress_raids=(),
    )
    # No raids pinned is the default: the source follows the open raids itself.
    assert [s.name for s in build_sources(cfg)] == ["raiderio_realm_progress"]


# --- runner.poll_once edge branches -----------------------------------------


class _BoomSource(_FakeSource):
    async def fetch(self, session):
        raise RuntimeError("feed upstream down")


def _cfg(channel=None):
    return dataclasses.replace(Config(), feed_default_channel_id=channel, persona_tone="off")


async def test_poll_once_survives_a_source_fetch_error(db, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    send = _Recorder()
    posted = await poll_once(db, _cfg("42"), [_BoomSource([])], session=None, send=send)
    assert posted == 0
    assert send.sent == []
    assert "fetch failed" in caplog.text  # logged, loop kept going


async def test_poll_once_marks_seen_when_event_routes_nowhere(db):
    # No routing rules AND no default channel => the event has nowhere to go, so
    # it is silently marked seen rather than posted.
    cfg = _cfg(channel=None)
    send = _Recorder()

    # Prime the source first (first poll is always silent).
    await poll_once(db, cfg, [_FakeSource([_event(external_id="seed")])], None, send)
    new = _event(external_id="homeless", title="Nowhere to go")
    posted = await poll_once(db, cfg, [_FakeSource([new])], None, send)

    assert posted == 0
    assert send.sent == []
    # It was marked seen, so a re-poll doesn't reconsider it.
    assert await db.filter_unseen([new]) == []


# --- wcl_prog.fetch live path (faked WCL client) ----------------------------


class _FakeWclClient:
    """Stands in for WarcraftLogsClient: an async-context client returning a
    single report plus its fights, so the source's fetch body runs offline."""

    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).last_kwargs = kwargs
        self._reports = [{"code": "RPT9"}]
        self._fights = {
            "startTime": 1780000000000,
            "fights": [
                {"name": "Boss", "difficulty": 5, "bossPercentage": 4.0},
                {"name": "Boss", "difficulty": 5, "kill": True},
            ],
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def guild_reports(self, *a, **k):
        return self._reports

    async def report_fights(self, code):
        assert code == "RPT9"
        return self._fights


def _wcl_cfg():
    return dataclasses.replace(
        Config(),
        warcraftlogs_client_id="cid",
        warcraftlogs_client_secret="secret",
        prog_alert_threshold=30.0,
    )


async def test_wcl_source_fetch_builds_events_from_a_report(monkeypatch):
    # Exercises the credentialed fetch path: pull one report, summarise its
    # fights, and emit a kill event (best pull collapses into the kill).
    from ftabot.services import warcraftlogs as wcl_mod

    monkeypatch.setattr(wcl_mod, "WarcraftLogsClient", _FakeWclClient)
    events = await WclProgSource(_wcl_cfg()).fetch(session=None)

    assert {e.event_type for e in events} == {"wcl.kill"}
    kill = events[0]
    assert "defeated" in kill.title
    assert kill.published_at and kill.published_at.startswith("20")  # _iso_from_ms ran
    # The zone falls back to 0 when unconfigured (the `or 0` branch in fetch).
    assert _FakeWclClient.last_kwargs.get("session") is None


async def test_wcl_source_fetch_returns_empty_without_reports(monkeypatch):
    from ftabot.services import warcraftlogs as wcl_mod

    class _NoReports(_FakeWclClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._reports = []

    monkeypatch.setattr(wcl_mod, "WarcraftLogsClient", _NoReports)
    assert await WclProgSource(_wcl_cfg()).fetch(session=None) == []


# --- wowaudit_raids.fetch live path (faked WoW Audit client) ----------------


class _FakeWowAuditClient:
    """Stands in for WowAuditClient: returns one upcoming raid so the source's
    enabled fetch body runs offline."""

    def __init__(self, *args, **kwargs):
        self._raids = [
            {"instance": "Manaforge", "difficulty": "Mythic", "date": "2026-06-06",
             "time": "20:00", "present": 18, "total": 20, "start": None},
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def upcoming_raids(self, **kwargs):
        return self._raids


async def test_wowaudit_source_fetch_builds_reminders(monkeypatch):
    from ftabot.services import wowaudit as wa_mod

    monkeypatch.setattr(wa_mod, "WowAuditClient", _FakeWowAuditClient)
    cfg = dataclasses.replace(Config(), wowaudit_api_key="key")  # enables the source
    events = await WowAuditRaidSource(cfg).fetch(session=None)

    assert len(events) == 1
    assert events[0].event_type == "wowaudit.raid-reminder"
    assert events[0].title == "🔥 Raid reminder: Manaforge Mythic"


# --- wowprogress.fetch ranked path (faked WoWProgress client) ---------------


async def test_wowprogress_source_fetch_emits_a_rank_event(monkeypatch):
    # When the guild is ranked, fetch builds exactly one rank event. (The
    # unranked short-circuit is covered in test_wowprogress.py.)
    from ftabot.feeds import wowprogress as wp_mod
    from ftabot.feeds.wowprogress import WowProgressSource

    class _RankedClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def guild_rank(self, *a, **k):
            return {"world_rank": 120, "realm_rank": 1, "area_rank": 12, "score": 4000}

    monkeypatch.setattr(wp_mod, "WowProgressClient", _RankedClient)
    events = await WowProgressSource(Config()).fetch(session=None)

    assert len(events) == 1
    assert events[0].event_type == "wowprogress.rank"
    assert "realm #1" in events[0].title


# --- raiderio_mplus._select_members cap guard -------------------------------


def test_select_members_cap_zero_picks_nobody():
    # A cap of zero (or less) means "poll nobody" — the early return.
    from ftabot.feeds.raiderio_mplus import _select_members
    from ftabot.models import Member

    members = [Member("Stabby", mythic_plus_score=3000.0, raid_team="A")]
    assert _select_members(members, 0) == []
    assert _select_members(members, -5) == []
    # A positive cap still selects the raider.
    assert [m.character_name for m in _select_members(members, 5)] == ["Stabby"]
