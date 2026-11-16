"""J2: ``/journey unplaced``, the sweep of members with no rank role.

The selection is a pure function over live members, the page is a pure
renderer, and the list's "Set" buttons run the very same placement path as the
welcome card's (proved by pressing them through the button dispatcher).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ftabot import journey
from ftabot.cogs.journey import JourneyCog
from ftabot.config import Config
from ftabot.journey_cards import (
    UnplacedEntry,
    page_count,
    render_unplaced,
    unplaced_page_size,
)
from ftabot.models import Member

from .test_journey import _M, _R
from .test_journey_cards import (
    _RM,
    LAB,
    _cfg,
    _click,
    _cog,
    _press,
    _RoleGuild,
    _staff,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _guild_with(n, *, placed=()):
    """A server of ``n`` unplaced members (ids 1..n, oldest first)."""
    guild = _RoleGuild("Ashes", "Visitor", "Officer")
    for i in range(1, n + 1):
        roles = [guild.role("Visitor")] if i in placed else []
        guild.add(_RM(i, f"user{i}", roles=roles, joined_at=T0 + timedelta(days=i)))
    guild.members = list(guild._cache.values())
    return guild


async def _swept(db, n, **over):
    guild = _guild_with(n)
    cog = _cog(db, guild, **over)
    await cog.on_ready()  # baselines everyone, as on the real first run
    return cog, guild


# --- the selection (pure) ------------------------------------------------------


def test_selection_is_no_rank_role_oldest_first_without_bots():
    visitor, raider, other = _R(1, "visitor"), _R(2, "Raider"), _R(3, "Birthday")
    members = [
        _M(1, joined_at=T0 + timedelta(days=5)),
        _M(2, joined_at=T0 + timedelta(days=1), roles=[other]),  # no *rank* role
        _M(3, joined_at=T0, roles=[visitor]),  # placed (case-insensitive)
        _M(4, joined_at=T0, roles=[raider]),  # a rank Calcifer cannot give, still placed
        _M(5, joined_at=T0, bot=True),
        _M(6, joined_at=T0 + timedelta(days=3)),
    ]
    nodate = _M(7)
    nodate.joined_at = None
    found = journey.unplaced([*members, nodate], ("Ashes", "Visitor", "Raider"))
    assert [m.id for m in found] == [2, 6, 1, 7]


# --- the page (pure) -----------------------------------------------------------


def _entries(n):
    return [UnplacedEntry(str(i), f"user{i}", "2026-01-02T00:00:00+00:00") for i in range(1, n + 1)]


def test_page_lists_ten_with_numbered_buttons_in_five_rows():
    cfg = _cfg()
    assert unplaced_page_size(cfg) == 10
    match = journey.RosterMatch(Member("Emberlyn"), "Ashes", journey.EXACT)
    entries = _entries(10)
    entries[0] = UnplacedEntry("1", "Emberlyn", "2026-01-02T00:00:00+00:00", match)
    page = render_unplaced(entries, cfg, page=1, total=38)

    lines = page.embed.description.split("\n")
    assert len(lines) == 10
    assert lines[0].startswith("` 1` <@1> · **Emberlyn** · joined <t:")
    assert lines[0].endswith("**Emberlyn** · Ashes · exact")
    assert lines[9].startswith("`10` <@10>") and lines[9].endswith("no match")
    assert "Page 1 of 4 · 38 without a rank" in page.embed.footer.text
    assert "page:2" in page.embed.footer.text

    assert len(page.buttons) == 20  # two per member, within Discord's 25
    assert [(b.label, b.arg, b.row) for b in page.buttons[:4]] == [
        ("1 · Ashes", "Ashes:1", 0), ("1 · Visitor", "Visitor:1", 0),
        ("2 · Ashes", "Ashes:1", 0), ("2 · Visitor", "Visitor:1", 0),
    ]
    assert max(b.row for b in page.buttons) == 4


def test_last_page_empty_list_and_many_roles():
    cfg = _cfg()
    last = render_unplaced(_entries(8), cfg, page=4, total=38)
    assert "Page 4 of 4" in last.embed.footer.text and "Next" not in last.embed.footer.text
    empty = render_unplaced([], cfg, page=1, total=0)
    assert "Nothing to do" in empty.embed.description and empty.buttons == []
    assert page_count(0, 10) == 1 and page_count(10, 10) == 1 and page_count(11, 10) == 2

    # Three placement roles: 3 buttons each, so only 5 members fit a page.
    three = _cfg(journey_role_allowlist=("A", "B", "C"), journey_rank_roles=("A", "B", "C"))
    assert unplaced_page_size(three) == 5
    page = render_unplaced(_entries(5), three, page=1, total=5)
    assert len(page.buttons) == 15 and max(b.row for b in page.buttons) == 4
    # No placement roles at all still lists people (no buttons to press).
    none = _cfg(journey_role_allowlist=("X",), journey_rank_roles=("Ashes",))
    assert render_unplaced(_entries(3), none, page=1, total=3).buttons == []


# --- the command ---------------------------------------------------------------


async def test_command_is_staff_only_and_ephemeral(db):
    cog, guild = await _swept(db, 12)
    await db.upsert_synced(Member("User3", guild_rank=5))

    denied = _click(guild, _staff(guild, "Ashes"))
    await JourneyCog.unplaced.callback(cog, denied)
    assert "welcome team" in denied.response.content and denied.response.embed is None

    it = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, it)
    assert it.response.ephemeral is True
    lines = it.response.embed.description.split("\n")
    assert len(lines) == 10 and lines[0].startswith("` 1` <@1>")  # oldest first
    assert "**User3** · Rank 5 · exact" in lines[2]
    view = it.response.kwargs["view"]
    assert view.children[0].custom_id == "calcifer:journey:lplace:1:Ashes:1"
    assert not it.response.kwargs["allowed_mentions"].users
    assert cog.messenger.sent == []  # nothing public


async def test_paging_and_out_of_range_pages(db):
    cog, guild = await _swept(db, 12)
    second = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, second, page=2)
    lines = second.response.embed.description.split("\n")
    assert len(lines) == 2
    assert "<@11>" in lines[0] and "<@12>" in lines[1]
    assert second.response.kwargs["view"].children[0].custom_id.endswith(":2")

    # Past the end clamps to the last page; below 1 clamps to the first.
    far = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, far, page=99)
    assert "Page 2 of 2" in far.response.embed.footer.text
    low = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, low, page=0)
    assert "Page 1 of 2" in low.response.embed.footer.text


async def test_post_option_goes_through_the_gate(db):
    cog, guild = await _swept(db, 3)
    it = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, it, post=True)
    assert it.response.content == f"Posted in <#{LAB}>."  # test mode: the lab
    channel, embed, view = cog.messenger.sent[0]
    assert channel == LAB and len(view.children) == 6

    off, guild2 = await _swept(db, 3, posting_mode="off")
    blocked = _click(guild2, _staff(guild2))
    await JourneyCog.unplaced.callback(off, blocked, post=True)
    assert "can't post right now" in blocked.response.content and off.messenger.sent == []


async def test_everyone_placed_and_outside_a_server(db):
    guild = _guild_with(2, placed=(1, 2))
    cog = _cog(db, guild)
    it = _click(guild, _staff(guild))
    await JourneyCog.unplaced.callback(cog, it)
    assert "Nothing to do" in it.response.embed.description
    assert "view" not in it.response.kwargs

    dm = _click(None, _staff(guild))
    await JourneyCog.unplaced.callback(cog, dm)
    assert dm.response.content == "Run this inside the server."


# --- the list's buttons reuse the card's placement path ---------------------------


async def test_list_button_places_logs_and_redraws_the_list(db):
    cog, guild = await _swept(db, 12)
    member = guild.get_member(1)
    it = _click(guild, _staff(guild), message_id="the-list")
    await _press("lplace", it, 1, "Ashes", 1)

    # Same effects as the card button: role applied, `placed` logged with actor.
    assert [r.name for r in member.roles] == ["Ashes"]
    event = (await db.journey_events(type="placed"))[0]
    assert (event["discord_id"], event["actor_id"], event["data"]) == (
        "1", "900", {"role": "Ashes"},
    )
    # The list redraws in place: member 1 is gone, everyone moves up.
    embed, view = it.response.edited
    assert "<@1>" not in embed.description.split("\n")[0]
    assert embed.description.startswith("` 1` <@2>")
    assert "11 without a rank" in embed.footer.text
    assert not it.response.allowed_mentions.users


async def test_list_button_obeys_the_same_guards(db):
    cog, guild = await _swept(db, 3)
    outsider = _click(guild, _staff(guild, "Ashes"), message_id="the-list")
    await _press("lplace", outsider, 1, "Ashes", 1)
    assert "welcome team" in outsider.response.content

    forged = _click(guild, _staff(guild), message_id="the-list")
    await _press("lplace", forged, 1, "Officer", 1)
    assert "not allowed" in forged.response.content
    assert guild.get_member(1).roles == [] and await db.journey_events(type="placed") == []


async def test_list_placement_also_updates_an_open_welcome_card(db):
    cog, guild = await _swept(db, 2)
    newcomer = guild.add(_RM(50, "fresh"))
    guild.members.append(newcomer)
    await cog.on_member_join(newcomer)  # posts their card
    it = _click(guild, _staff(guild), message_id="the-list")
    await _press("lplace", it, 50, "Visitor", 1)
    _, message_id, embed, _ = cog.messenger.edits[-1]
    assert message_id == "msg1"
    status = next(f.value for f in embed.fields if f.name == "Status")
    assert "Placed: **Visitor** by <@900>" in status


def test_the_command_only_exists_with_the_journey_cog():
    names = [c.name for c in JourneyCog.journey_group.commands]
    assert names == ["unplaced"]
    assert Config().journey_enabled is False  # and the cog is not loaded by default
