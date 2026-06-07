"""Phase 4 tests: WoW Audit raid reminders + WCL live-prog summaries."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ftabot.config import Config
from ftabot.feeds.wcl_prog import _remaining_pct, prog_events, summarize_report
from ftabot.feeds.wowaudit_raids import _reminder_event
from ftabot.services.wowaudit import (
    raid_start_dt,
    select_upcoming_raids,
)

# --- WoW Audit raid reminders ----------------------------------------------


def test_raid_start_dt_reads_wall_clock_in_zone():
    raid = {"date": "2026-06-07", "start_time": "19:15"}
    # A 19:15 wall-clock start in summer Paris (CEST, UTC+2) is 17:15 UTC.
    dt = raid_start_dt(raid, ZoneInfo("Europe/Paris"))
    assert dt == datetime(2026, 6, 7, 17, 15, tzinfo=timezone.utc)


def test_raid_start_dt_tracks_dst_across_the_year():
    paris = ZoneInfo("Europe/Paris")
    summer = raid_start_dt({"date": "2026-06-07", "start_time": "19:15"}, paris)
    winter = raid_start_dt({"date": "2026-01-07", "start_time": "19:15"}, paris)
    # Same 19:15 wall-clock, but CEST (UTC+2) in June and CET (UTC+1) in January.
    # A fixed offset would put both at the same UTC hour; the IANA zone must not,
    # which is the whole reason RAID_TZ replaced the static RAID_TZ_OFFSET_HOURS.
    assert summer == datetime(2026, 6, 7, 17, 15, tzinfo=timezone.utc)
    assert winter == datetime(2026, 1, 7, 18, 15, tzinfo=timezone.utc)


def test_raid_timezone_resolves_iana_zone():
    # The default server timezone is the DST-correct Europe/Paris IANA zone.
    assert Config().raid_timezone == ZoneInfo("Europe/Paris")
    # A fixed offset is expressible as a zone, so no separate offset knob exists.
    assert Config(raid_tz="Etc/GMT-2").raid_timezone == ZoneInfo("Etc/GMT-2")
    # An unknown zone name degrades to UTC rather than raising at startup.
    assert Config(raid_tz="Not/AZone").raid_timezone == timezone.utc


def test_select_upcoming_only_within_window():
    now = datetime(2026, 6, 7, 15, 0, tzinfo=timezone.utc)
    raids = [
        {"date": "2026-06-07", "start_time": "19:00"},  # +4h -> in a 6h window
        {"date": "2026-06-07", "start_time": "10:00"},  # past
        {"date": "2026-06-09", "start_time": "19:00"},  # too far
    ]
    due = select_upcoming_raids(raids, now, within_hours=6)
    assert [r["start_time"] for r, _ in due] == ["19:00"]


def test_reminder_event_flags_low_signups():
    full = _reminder_event(
        {"date": "2026-06-07", "time": "19:15", "instance": "Manaforge", "difficulty": "Heroic",
         "present": 20, "total": 22, "start": "2026-06-07T19:15:00+00:00"}
    )
    assert full.severity == "info"
    assert full.external_id == "raid-2026-06-07-19:15"

    thin = _reminder_event(
        {"date": "2026-06-07", "time": "19:15", "instance": "Manaforge", "difficulty": "Heroic",
         "present": 12, "total": 22, "start": "2026-06-07T19:15:00+00:00"}
    )
    assert thin.severity == "warning"  # under 80% signed up


# --- WCL live prog ----------------------------------------------------------


def test_remaining_pct_normalises_hundredths():
    assert _remaining_pct(1234, None) == 12.3  # hundredths -> percent
    assert _remaining_pct(8.5, None) == 8.5
    assert _remaining_pct(None, None) is None


def test_summarize_report_tracks_best_pull_and_kill():
    fights = [
        {"name": "Boss A", "difficulty": "Mythic", "kill": False, "bossPercentage": 45},
        {"name": "Boss A", "difficulty": "Mythic", "kill": False, "bossPercentage": 12},
        {"name": "Boss A", "difficulty": "Mythic", "kill": True, "bossPercentage": 0},
        {"name": "Boss B", "difficulty": "Mythic", "kill": False, "bossPercentage": 80},
    ]
    summary = {(b["boss"], b["difficulty"]): b for b in summarize_report(fights)}
    a = summary[("Boss A", "Mythic")]
    assert a["pulls"] == 3 and a["killed"] is True
    b = summary[("Boss B", "Mythic")]
    assert b["killed"] is False and b["best_pct"] == 80.0


def test_prog_events_kill_and_threshold_gating():
    bosses = [
        {"boss": "A", "difficulty": "Mythic", "pulls": 5, "best_pct": 0.0, "killed": True},
        {"boss": "B", "difficulty": "Mythic", "pulls": 9, "best_pct": 8.0, "killed": False},
        {"boss": "C", "difficulty": "Mythic", "pulls": 3, "best_pct": 70.0, "killed": False},
    ]
    events = prog_events("RPT", bosses, threshold=30.0)
    types = {e.event_type for e in events}
    # A killed -> announced; B close (8% <= 30) -> announced; C far (70%) -> silent.
    assert types == {"wcl.kill", "wcl.best-pull"}
    ids = {e.external_id for e in events}
    assert "RPT-A-Mythic-kill" in ids
    assert "RPT-B-Mythic-best-5" in ids  # 8% buckets to 5
    assert not any("C-" in i for i in ids)


def test_prog_events_bucket_changes_only_on_improvement():
    # Two polls: best improves 12% -> 7%. Buckets 10 then 5 -> two distinct ids.
    e1 = prog_events("RPT", [{"boss": "A", "difficulty": "M", "pulls": 4, "best_pct": 12.0, "killed": False}])
    e2 = prog_events("RPT", [{"boss": "A", "difficulty": "M", "pulls": 7, "best_pct": 7.0, "killed": False}])
    assert e1[0].external_id != e2[0].external_id
