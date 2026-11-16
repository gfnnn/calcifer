"""J4: the weekly Front door board.

A pure view-model over the journey log (``journey.front_door``) and a board
renderer on top, tested like the other boards: empty, typical, a stable
fingerprint, and the scout-lead reconciliation that feeds its last line.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ftabot import brand, journey
from ftabot.boards import BOARD_NAMES, REGISTRY, frontdoor
from ftabot.boards.frontdoor import FRONT_DOOR_TYPE, FrontDoorBoard
from ftabot.cogs.admin import AdminCog
from ftabot.config import Config
from ftabot.models import Member

from .test_cogs import _Bot, _Interaction, _Perms, _User

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
LABELS = {"aaa": "Raider.IO", "bbb": "Storefronts"}
KW = dict(labels=LABELS, rank_roles=("Ashes", "Visitor"), interest_roles=("Mythic+", "PvP"))


def _at(**ago):
    return journey.iso(NOW - timedelta(**ago))


class _Log:
    """Builds an event list the way the database returns it."""

    def __init__(self):
        self.events: list[dict] = []

    def add(self, who, kind, at, **data):
        self.events.append({"discord_id": str(who), "type": kind, "at": at,
                            "actor_id": None, "data": data})
        return self

    def join(self, who, via="aaa", rejoin=False, **ago):
        at = _at(**ago)
        self.add(who, "rejoined" if rejoin else "joined", at)
        return self.add(who, "came_via", at, code=via)


def _cfg(**over):
    base = dict(journey_enabled=True, journey_invite_labels=tuple(LABELS.items()),
                journey_interest_roles=("Mythic+", "PvP"))
    base.update(over)
    return Config(**base)


# --- stays (pure) -----------------------------------------------------------------


def test_stays_split_on_rejoin_and_ignore_people_without_a_join():
    log = _Log()
    log.add(99, "greeted", _at(days=1))  # a baseline member: no stay
    log.join(1, days=10).add(1, "greeted", _at(days=9)).add(1, "left", _at(days=8))
    log.add(1, "greeted", _at(days=7))  # after leaving: belongs to no stay
    log.join(1, via="bbb", rejoin=True, days=2)
    log.add(1, "roles_changed", _at(days=1), added=["Visitor", "PvP", "PvP"], removed=[])
    log.add(1, "placed", _at(hours=1), role="Visitor")  # the role came first
    first, second = journey.stays(log.events, rank_roles=("Visitor",), interest_roles=("PvP",))
    assert (first.came_via, first.left_at is not None) == ("aaa", True)
    assert first.greeted_at == _at(days=9)
    assert second.came_via == "bbb" and second.greeted_at is None
    assert second.placed_at == _at(days=1) and second.picked == ["PvP"]


def test_rate_has_no_percentage_for_an_empty_cohort():
    assert journey.Rate().percent is None
    assert journey.Rate(2, 3).percent == 67


# --- the view-model (pure) ----------------------------------------------------------


def _typical():
    log = _Log()
    # A: greeted in 2h and placed in a day. B: greeted late, never placed.
    log.join("A", days=3).add("A", "greeted", _at(days=3, hours=-2))
    log.add("A", "placed", _at(days=2), role="Ashes")
    log.add("A", "roles_changed", _at(days=2), added=["Mythic+"], removed=[])
    log.join("B", via="bbb", days=5).add("B", "greeted", _at(days=3))
    # C joined an hour ago: not yet due for anything.
    log.join("C", via="unknown", hours=1)
    # D joined 20 days ago, was placed by hand in time, then left 2 days ago.
    log.join("D", days=20).add("D", "roles_changed", _at(days=19), added=["Visitor"], removed=[])
    log.add("D", "left", _at(days=2))
    # The 30-to-37-day cohort: E stayed, F left after a week, G left after 31 days.
    log.join("E", days=33)
    log.join("F", days=35).add("F", "left", _at(days=28))
    log.join("G", days=36).add("G", "left", _at(days=5))
    log.join("H", days=40)  # too old for the cohort
    log.add("A", "lead_joined", _at(days=1), lead_id=1, name="Ashbringer", realm="hellfire")
    return log.events


def test_front_door_seven_and_thirty_day_windows():
    week, month = journey.front_door(_typical(), NOW, **KW).windows

    assert (week.days, week.joins) == (7, 3)
    assert dict(week.by_source) == {"Raider.IO": 1, "Storefronts": 1, "unknown": 1}
    # C is too new to count against either rate.
    assert week.greeted == journey.Rate(1, 2)  # A in time; B took two days
    assert week.placed == journey.Rate(1, 2)
    assert week.leavers == 2  # D and G left this week
    assert week.picked == (("Mythic+", 1),)
    assert week.leads == ("Ashbringer",)

    assert (month.days, month.joins) == (30, 4)
    assert month.by_source[0] == ("Raider.IO", 2)  # busiest source first
    assert month.greeted == journey.Rate(1, 3)
    assert month.placed == journey.Rate(2, 3)  # D's hand-given rank counts
    assert month.leavers == 3


def test_still_here_follows_the_30_to_37_day_cohort():
    stats = journey.front_door(_typical(), NOW, **KW)
    # E stayed, G was still here at day 30 (left on day 31), F was gone in a week.
    assert stats.still_here == journey.Rate(2, 3)
    assert journey.front_door([], NOW, **KW).still_here.percent is None


def test_an_unnamed_lead_event_still_shows():
    log = _Log().add("A", "lead_joined", _at(days=1))
    assert journey.front_door(log.events, NOW, **KW).windows[0].leads == ("a scout lead",)


# --- the board ---------------------------------------------------------------------


async def _store(db, events):
    await db.set_journey_baseline_at("2026-01-01T00:00:00+00:00")
    for e in events:
        await db.add_journey_event(e["discord_id"], e["type"], at=e["at"], data=e["data"])


def test_board_is_registered_with_a_friendly_name():
    assert isinstance(REGISTRY.get(FRONT_DOOR_TYPE), FrontDoorBoard)
    assert BOARD_NAMES[FRONT_DOOR_TYPE] == "Front Door"


async def test_board_posts_nothing_when_the_journey_is_off_or_never_ran(db):
    board = FrontDoorBoard()
    assert await board.render(db, _cfg(), {}) is None  # never ran: no baseline
    await _store(db, _typical())
    assert await board.render(db, Config(), {}) is None  # flag off


async def test_board_renders_both_windows(db, monkeypatch):
    monkeypatch.setattr(frontdoor, "_now", lambda: NOW)
    await _store(db, _typical())
    payload = await FrontDoorBoard().render(db, _cfg(), {})
    text = payload.embed.description
    week, month = text.split("**Last 30 days**")
    assert payload.embed.title == "🚪 Front door"
    assert "**Last 7 days**" in week and "Joined: `3` (" in week
    assert "Raider.IO `1`" in week and "Storefronts `1`" in week and "unknown `1`" in week
    assert "Greeted within a day: `50%` (1 of 2)" in week
    assert "Placed within two days: `50%` (1 of 2)" in week
    assert "Left: `2`" in week
    assert "Picked: Mythic+ `1`" in week
    assert "Scout leads who joined: **Ashbringer**" in week
    assert "Joined: `4` (Raider.IO `2`" in month
    assert "Placed within two days: `67%` (2 of 3)" in month
    assert "**Still here after 30 days**: `67%` (2 of 3) of those who joined 30 to 37" in month
    assert payload.embed.color.value == brand.GUILD_EMBER
    assert payload.embed.timestamp is not None
    assert not brand.has_em_dash(payload.embed.footer.text)
    assert "How the welcome is going." in payload.embed.footer.text
    assert "<@" not in text  # numbers, not people: nothing to ping


async def test_board_on_a_brand_new_journey_and_without_interest_roles(db, monkeypatch):
    monkeypatch.setattr(frontdoor, "_now", lambda: NOW)
    await _store(db, [])
    payload = await FrontDoorBoard().render(db, _cfg(journey_interest_roles=()), {})
    text = payload.embed.description
    assert text.count("Joined: `0`") == 2
    assert text.count("nobody due yet") == 5  # 2 x greeted, 2 x placed, still here
    assert "Picked" not in text  # no interest roles configured yet
    assert "Scout leads" not in text
    # With interest roles configured but nothing chosen yet.
    picked = await FrontDoorBoard().render(db, _cfg(), {})
    assert "Picked: nothing yet" in picked.embed.description


async def test_board_fingerprint_is_stable_until_something_happens(db, monkeypatch):
    monkeypatch.setattr(frontdoor, "_now", lambda: NOW)
    await _store(db, _typical())
    board = FrontDoorBoard()
    first = await board.render(db, _cfg(), {})
    second = await board.render(db, _cfg(), {})
    assert first.fingerprint == second.fingerprint
    await db.add_journey_event("Z", "joined", at=_at(hours=2))
    third = await board.render(db, _cfg(), {})
    assert third.fingerprint != first.fingerprint


async def test_board_caps_a_long_list_of_leads(db, monkeypatch):
    monkeypatch.setattr(frontdoor, "_now", lambda: NOW)
    log = _Log()
    for i in range(11):
        log.add("A", "lead_joined", _at(days=1), name=f"Lead{i}")
    await _store(db, log.events)
    text = (await FrontDoorBoard().render(db, _cfg(), {})).embed.description
    assert "**Lead7**" in text and "**Lead8**" not in text and "and 3 more" in text


def test_default_clock_is_utc_now():
    assert abs((frontdoor._now() - datetime.now(timezone.utc)).total_seconds()) < 5


# --- scout leads who joined ----------------------------------------------------------


async def _lead(db, name, state=None):
    lead_id = await db.upsert_lead({"name": name, "realm": "hellfire", "region": "eu"})
    if state:
        await db.set_lead_state(lead_id, state)
    return lead_id


async def test_a_linked_newcomer_moves_their_lead_to_joined(db):
    assert await journey.reconcile_scout_leads(db) == 0  # no leads at all
    lead_id = await _lead(db, "Çinder")
    passed = await _lead(db, "Emberlyn", "passed")
    await _lead(db, "Nobody")
    for name in ("Çinder", "Emberlyn"):
        await db.upsert_synced(Member(name))
    await journey.record_join(db, discord_id="50", display_name="cinder")
    await journey.record_join(db, discord_id="51", display_name="ember")
    for name, discord_id in (("Çinder", "50"), ("Emberlyn", "51")):
        member = await db.get_member(name, "hellfire", "eu")
        await db.set_discord_link(member.id, discord_id)

    assert await journey.reconcile_scout_leads(db) == 1
    assert (await db.get_lead(lead_id))["state"] == "joined"
    assert (await db.get_lead(passed))["state"] == "passed"  # an officer's call stands
    event = (await db.journey_events(type="lead_joined"))[0]
    assert event["discord_id"] == "50"
    assert event["data"] == {"lead_id": lead_id, "name": "Çinder", "realm": "hellfire"}
    # Idempotent: the lead is no longer open, so nothing is logged twice.
    assert await journey.reconcile_scout_leads(db) == 0
    assert len(await db.journey_events(type="lead_joined")) == 1


async def test_baseline_members_and_unlinked_characters_do_not_count(db):
    lead_id = await _lead(db, "Veteran")
    await db.upsert_synced(Member("Veteran"))
    await journey.reconcile(db, [journey.Present("1", "vet", None)])  # baseline
    member = await db.get_member("Veteran", "hellfire", "eu")
    await db.set_discord_link(member.id, "1")
    assert await journey.reconcile_scout_leads(db) == 0
    assert (await db.get_lead(lead_id))["state"] == "new"


async def test_link_command_recognises_a_lead_through_linked_main(db):
    lead_id = await _lead(db, "Ashbringer", "trialing")
    await db.upsert_synced(Member("Ashbringer"))
    await journey.record_join(db, discord_id="999", display_name="ash")
    cog = AdminCog(_Bot(db, Config(journey_enabled=True)))
    await AdminCog.link.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer",
        user=_User(id=999),
    )
    assert (await db.get_lead(lead_id))["state"] == "joined"
    assert [e["type"] for e in await db.journey_events(discord_id="999")][-2:] == [
        "character_linked", "lead_joined",
    ]


async def test_linked_main_alone_is_enough(db):
    lead_id = await _lead(db, "Solo")
    await journey.record_join(db, discord_id="7", display_name="solo")
    await db.set_person_linked_main("7", "solo|hellfire|eu")
    assert await journey.reconcile_scout_leads(db) == 1
    assert (await db.get_lead(lead_id))["state"] == "joined"
