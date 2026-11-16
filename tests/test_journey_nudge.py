"""J3: the daily nudge at 18:00 UK time.

The three lists are a pure function of the journey log and "now"; "is it
time" is a pure function of the UK wall clock (so DST is a unit test); the
post goes through the front-door gate; and the Checked in button logs once
and greys itself out on the message it was clicked on.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone

import discord
import pytest

from ftabot import journey, serve
from ftabot.cogs import _persistent
from ftabot.config import Config
from ftabot.journey_cards import NUDGE_CAP, render_nudge
from scripts.preflight import FAIL, OK, WARN, check

from .test_journey_cards import _RM, LAB, _cfg, _click, _cog, _press, _RoleGuild, _staff

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)


def _ago(**delta):
    return journey.iso(NOW - timedelta(**delta))


async def _joined(db, discord_id, **ago):
    await journey.record_join(
        db, discord_id=str(discord_id), display_name=f"user{discord_id}",
        joined_at=_ago(**ago), came_via="aaa",
    )


def _ids(statuses):
    return [s.discord_id for s in statuses]


# --- config -----------------------------------------------------------------


def test_nudge_flag_defaults_off_and_needs_the_journey(monkeypatch):
    assert Config().journey_nudge_enabled is False
    assert Config().journey_nudge_time == "18:00"
    assert Config(journey_nudge_enabled=True).journey_nudges_enabled is False
    monkeypatch.setenv("JOURNEY_ENABLED", "1")
    monkeypatch.setenv("JOURNEY_NUDGE_ENABLED", "1")
    monkeypatch.setenv("JOURNEY_NUDGE_TIME", "17:30")
    cfg = Config.from_env()
    assert cfg.journey_nudges_enabled and cfg.journey_nudge_time == "17:30"


def test_preflight_checks_the_nudge_time():
    def finding(**over):
        found = {f.name: f for f in check(Config(journey_nudge_enabled=True, **over))}
        return found["Journey nudge"]

    assert finding(journey_enabled=True).level == OK
    assert finding().level == WARN  # the journey itself is off
    for bad in ("6pm", "25:00", "18:61", "18"):
        assert finding(journey_enabled=True, journey_nudge_time=bad).level == FAIL
    assert "Journey nudge" not in {f.name for f in check(Config())}


# --- the clock: timezone and DST (pure) ----------------------------------------


def test_time_parsing_falls_back_to_six_pm():
    assert journey.parse_nudge_time("09:05") == time(9, 5)
    for bad in (None, "", "6pm", "25:00", "18:61", "1:2:3"):
        assert journey.parse_nudge_time(bad) == time(18, 0)


def test_due_at_six_pm_uk_time_in_winter_and_summer():
    six = time(18, 0)

    def due(y, mo, d, h, mi, last=None):
        return journey.nudge_due(datetime(y, mo, d, h, mi, tzinfo=timezone.utc), six, last)

    # Winter (GMT): 18:00 UK is 18:00 UTC.
    assert due(2026, 1, 15, 17, 59) is None
    assert due(2026, 1, 15, 18, 0).isoformat() == "2026-01-15"
    # Summer (BST): 18:00 UK is 17:00 UTC, an hour earlier on the UTC clock.
    assert due(2026, 7, 15, 16, 59) is None
    assert due(2026, 7, 15, 17, 0).isoformat() == "2026-07-15"
    # The spring-forward day itself (29 March 2026) and the day the clocks go back.
    assert due(2026, 3, 29, 16, 59) is None and due(2026, 3, 29, 17, 0) is not None
    assert due(2026, 10, 25, 17, 59) is None and due(2026, 10, 25, 18, 0) is not None


def test_due_only_once_a_day_and_catches_up_after_a_restart():
    six = time(18, 0)
    evening = datetime(2026, 1, 15, 21, 30, tzinfo=timezone.utc)
    # Restarted at 21:30 having missed 18:00: still today's to send...
    assert journey.nudge_due(evening, six, "2026-01-14").isoformat() == "2026-01-15"
    # ...but never twice.
    assert journey.nudge_due(evening, six, "2026-01-15") is None
    # Late UTC evening in summer is already tomorrow in no sense: 23:30 UTC is 00:30 UK.
    late = datetime(2026, 7, 15, 23, 30, tzinfo=timezone.utc)
    assert journey.nudge_due(late, six, "2026-07-15") is None  # 00:30 on the 16th, before 18:00


# --- the three selections, from the log ----------------------------------------


async def test_the_three_lists(db):
    cfg = _cfg()
    await journey.reconcile(db, [journey.Present("1", "veteran", _ago(days=400))])
    await _joined(db, 10, hours=30)  # not greeted after a day
    await _joined(db, 11, hours=20)  # too new for anything
    await _joined(db, 12, hours=50)  # not greeted AND not placed
    await _joined(db, 13, hours=50)  # greeted and placed: on no list
    await journey.record_greeted(db, cfg, "13", "900")
    await journey.record_placed(db, cfg, "13", "Ashes", "900")
    await _joined(db, 14, days=7, hours=1)  # day-7 check-in due
    await journey.record_greeted(db, cfg, "14", "900")
    await journey.record_placed(db, cfg, "14", "Visitor", "900")
    await _joined(db, 15, days=8)  # checked in already
    await journey.record_greeted(db, cfg, "15", "900")
    await journey.record_placed(db, cfg, "15", "Visitor", "900")
    await journey.record_checked_in(db, cfg, "15", "900")
    await _joined(db, 16, days=3)  # left: never nudged about
    await journey.record_leave(db, "16")
    await _joined(db, 17, days=30)  # past the horizon: backlog, not a nudge

    lists = await journey.nudge_for(db, cfg, NOW)
    assert _ids(lists.not_greeted) == ["12", "10"]  # longest wait first
    assert _ids(lists.not_placed) == ["12"]
    assert _ids(lists.check_in) == ["14"]
    assert not lists.quiet


async def test_a_rank_given_by_hand_counts_as_placed(db):
    cfg = _cfg()
    await _joined(db, 20, hours=60)
    await journey.record_roles_changed(db, "20", added=["Visitor"], removed=[])
    lists = await journey.nudge_for(db, cfg, NOW)
    assert _ids(lists.not_placed) == [] and _ids(lists.not_greeted) == ["20"]


async def test_people_repeat_only_while_still_outstanding(db):
    cfg = _cfg()
    await _joined(db, 30, hours=30)
    await _joined(db, 31, hours=30)
    day1 = await journey.nudge_for(db, cfg, NOW)
    assert _ids(day1.not_greeted) == ["30", "31"]

    await journey.record_greeted(db, cfg, "30", "900")
    day2 = await journey.nudge_for(db, cfg, NOW + timedelta(days=1))
    assert _ids(day2.not_greeted) == ["31"]  # 30 is done; 31 is still waiting
    assert _ids(day2.not_placed) == ["30", "31"]  # and both now need placing

    await journey.record_checked_in(db, cfg, "30", "900")
    again = await journey.record_checked_in(db, cfg, "30", "901")  # idempotent
    assert again.checked_in_by == "900"
    assert len(await db.journey_events(type="checked_in")) == 1
    assert await journey.record_checked_in(db, cfg, "404", "900") is None


async def test_baseline_members_are_never_nudged_about(db):
    await journey.reconcile(db, [
        journey.Present(str(i), f"old{i}", _ago(days=3)) for i in range(40)
    ])
    assert (await journey.nudge_for(db, _cfg(), NOW)).quiet


# --- the renderer: quiet day and the cap -----------------------------------------


def _status(i, **over):
    base = dict(discord_id=str(i), display_name=f"user{i}", joined_at=_ago(days=8),
                left_at=None, came_via="aaa")
    base.update(over)
    return journey.Status(**base)


def test_quiet_day_renders_nothing():
    assert render_nudge(journey.NudgeLists()) is None


def test_lists_are_capped_with_an_and_n_more_tail():
    many = tuple(_status(i) for i in range(1, 14))
    card = render_nudge(journey.NudgeLists(not_greeted=many, check_in=many))
    text = card.embed.description
    assert "**Not greeted after a day** (13)" in text
    assert "**Day-7 check-in due** (13)" in text
    assert "Not placed" not in text  # an empty list is left out
    assert text.count("*and 3 more*") == 2
    assert text.count("<@") == 2 * NUDGE_CAP
    # Numbered lines match numbered buttons, capped the same.
    assert "` 1` <@1> · **user1** · joined <t:" in text
    assert [b.label for b in card.buttons] == [
        f"Checked in · {n}" for n in range(1, NUDGE_CAP + 1)
    ]
    assert len(text) < 4096 and "—" not in card.embed.footer.text


def test_long_names_are_trimmed():
    card = render_nudge(journey.NudgeLists(
        not_placed=(_status(1, display_name="x" * 60),)))
    assert "x" * 23 + "…" in card.embed.description
    assert "x" * 25 not in card.embed.description
    assert card.buttons == []  # only the check-in list has buttons


# --- posting ------------------------------------------------------------------


async def test_nudge_posts_once_to_the_lab_and_skips_a_quiet_day(db):
    guild = _RoleGuild("Ashes", "Visitor")
    cog = _cog(db, guild, journey_nudge_enabled=True, welcome_cards_enabled=False)
    assert await cog.post_nudge(NOW) is False  # quiet: nothing to say
    assert cog.messenger.sent == []

    await _joined(db, 14, days=7, hours=1)
    assert await cog.post_nudge(NOW) is True
    channel, embed, view = cog.messenger.sent[0]
    assert channel == LAB  # test mode
    assert "Day-7 check-in due" in embed.description
    assert [c.custom_id for c in view.children] == ["calcifer:journey:checkin:14"]


async def test_nudge_respects_the_flag_and_the_gate(db):
    guild = _RoleGuild()
    await _joined(db, 14, days=7, hours=1)
    off = _cog(db, guild, journey_nudge_enabled=False)
    assert await off.post_nudge(NOW) is False
    muted = _cog(db, guild, journey_nudge_enabled=True, posting_mode="off")
    assert await muted.post_nudge(NOW) is False
    assert off.messenger.sent == [] and muted.messenger.sent == []


# --- the Checked in button ---------------------------------------------------------


class _Btn:
    def __init__(self, custom_id, label, disabled=False):
        self.custom_id, self.label, self.disabled = custom_id, label, disabled
        self.style = discord.ButtonStyle.secondary


class _Row:
    def __init__(self, *children):
        self.children = list(children)


def _nudge_message():
    class _Msg:
        id = "nudge1"
        components = [_Row(
            _Btn("calcifer:journey:checkin:14", "Checked in · 1"),
            _Btn("calcifer:journey:checkin:15", "Checked in · 2", disabled=True),
            _Btn("someone-elses-button", "x"),
        )]

    return _Msg()


async def test_checked_in_logs_once_and_greys_out_its_own_button(db):
    guild = _RoleGuild("Ashes", "Visitor")
    cog = _cog(db, guild, journey_nudge_enabled=True)
    guild.add(_RM(14, "user14"))
    await _joined(db, 14, days=7, hours=1)

    it = _click(guild, _staff(guild))
    it.message = _nudge_message()
    await _press("checkin", it, 14)
    event = (await db.journey_events(type="checked_in"))[0]
    assert (event["discord_id"], event["actor_id"]) == ("14", "900")
    _, view = it.response.edited
    assert [(c.custom_id, c.item.disabled) for c in view.children] == [
        ("calcifer:journey:checkin:14", True),
        ("calcifer:journey:checkin:15", True),  # already done stays done
    ]
    assert not it.response.allowed_mentions.users

    again = _click(guild, _staff(guild, user_id=901))
    again.message = _nudge_message()
    await _press("checkin", again, 14)
    assert len(await db.journey_events(type="checked_in")) == 1
    assert (await journey.nudge_for(db, cog.config, NOW)).check_in == ()


async def test_checked_in_is_staff_only(db):
    guild = _RoleGuild()
    cog = _cog(db, guild, journey_nudge_enabled=True)
    guild.add(_RM(14, "user14"))
    await _joined(db, 14, days=7, hours=1)
    it = _click(guild, _staff(guild, "Visitor"))
    it.message = _nudge_message()
    await _press("checkin", it, 14)
    assert "welcome team" in it.response.content
    assert await db.journey_events(type="checked_in") == []
    assert cog.messenger.sent == []


def test_rebuilt_view_of_a_message_without_components_is_empty():
    class _Bare:
        components = None

    assert _persistent.rebuilt_view(_Bare()).children == []


# --- the loop ------------------------------------------------------------------


class _LoopBot:
    def __init__(self, cog):
        self._cog = cog

    async def wait_until_ready(self):
        return None

    def get_cog(self, name):
        return self._cog


class _NudgeCog:
    def __init__(self, fail=False):
        self.posts = 0
        self.fail = fail

    async def post_nudge(self):
        self.posts += 1
        if self.fail:
            raise RuntimeError("boom")
        return True


def _stop_after(n):
    calls = {"n": 0}

    async def _sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= n:
            raise asyncio.CancelledError

    return _sleep


async def test_loop_posts_once_per_day_across_ticks(tmp_path, monkeypatch, caplog):
    cfg = Config(database_path=str(tmp_path / "n.db"), journey_enabled=True,
                 journey_nudge_enabled=True, journey_nudge_time="00:00")
    cog = _NudgeCog()
    monkeypatch.setattr(serve.asyncio, "sleep", _stop_after(3))
    with caplog.at_level("INFO", logger="ftabot.serve"), pytest.raises(asyncio.CancelledError):
        await serve._journey_nudge_loop(cfg, _LoopBot(cog))
    assert cog.posts == 1  # three ticks, one nudge: the date is remembered
    assert "daily nudge posted" in caplog.text


async def test_loop_waits_for_the_time_and_survives_errors(tmp_path, monkeypatch, caplog):
    path = str(tmp_path / "n.db")
    never = Config(database_path=path, journey_enabled=True, journey_nudge_enabled=True,
                   journey_nudge_time="23:59")
    cog = _NudgeCog()
    monkeypatch.setattr(serve.asyncio, "sleep", _stop_after(2))
    monkeypatch.setattr(journey, "nudge_due", lambda now, at, last: None)
    with pytest.raises(asyncio.CancelledError):
        await serve._journey_nudge_loop(never, _LoopBot(cog))
    assert cog.posts == 0

    monkeypatch.undo()
    broken = _NudgeCog(fail=True)
    cfg = Config(database_path=path, journey_enabled=True, journey_nudge_enabled=True,
                 journey_nudge_time="00:00")
    monkeypatch.setattr(serve.asyncio, "sleep", _stop_after(2))
    with caplog.at_level("WARNING", logger="ftabot.serve"), pytest.raises(asyncio.CancelledError):
        await serve._journey_nudge_loop(cfg, _LoopBot(broken))
    assert broken.posts == 1 and "journey nudge error: RuntimeError" in caplog.text
