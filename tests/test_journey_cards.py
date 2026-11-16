"""J1: welcome cards in #front-door.

The card renderer is pure (tested like a board), the buttons are driven
through the persistent-button dispatcher exactly as a real click arrives, and
every send/edit goes through a recording messenger so the posting gate, the
no-ping rule and the role allow-list are all checked offline.
"""

from __future__ import annotations

import discord
import pytest

from ftabot import journey
from ftabot.cogs import _persistent
from ftabot.cogs._guards import is_staff
from ftabot.cogs.journey import JourneyCog, RoleMissing, set_rank_role, to_view
from ftabot.cogs.posting import PostingCog
from ftabot.config import Config
from ftabot.journey_cards import account_created_unix, render_card, stamp
from ftabot.models import Member
from ftabot.posting import PostGate, PostingMode
from ftabot.runtime_settings import effective_front_door_channel

from .test_cogs import _Interaction, _Perms, _Response
from .test_journey import _M, _R, GUILD_ID, _Forbidden, _Guild, _JBot

LAB, FRONT_DOOR = "111", "222"
NEWBIE = 175928847299117063  # a real-shaped snowflake (created 2016-04-30)


# --- fakes ------------------------------------------------------------------


class _Messenger:
    def __init__(self):
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.gone = False
        self.fail = False

    async def send(self, channel_id, embed, view=None):
        if self.fail:
            raise RuntimeError("discord is down")
        self.sent.append((channel_id, embed, view))
        return f"msg{len(self.sent)}"

    async def edit(self, channel_id, message_id, embed, view=None):
        if self.gone:
            return False
        self.edits.append((channel_id, message_id, embed, view))
        return True


class _ClickResponse(_Response):
    def __init__(self):
        super().__init__()
        self.edited = None

    async def edit_message(self, *, embed=None, view=None, allowed_mentions=None):
        self.edited = (embed, view)
        self.allowed_mentions = allowed_mentions


class _Message:
    def __init__(self, id):
        self.id = id


class _RoleGuild(_Guild):
    """A guild with roles and a member cache, for the placement buttons."""

    def __init__(self, *role_names, deny=False):
        super().__init__()
        self.roles = [_R(i + 10, name) for i, name in enumerate(role_names)]
        self._cache: dict[int, object] = {}
        self.deny = deny

    def role(self, name):
        return next(r for r in self.roles if r.name == name)

    def get_member(self, mid):
        return self._cache.get(mid)

    def add(self, member):
        member.guild = self
        self._cache[member.id] = member
        return member


class _RM(_M):
    """A member whose add/remove_roles really change ``roles``."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reasons: list[str] = []

    async def add_roles(self, *roles, reason=None):
        if self.guild.deny:
            raise _Forbidden()
        self.roles.extend(roles)
        self.reasons.append(reason)

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]


def _staff(guild, role="Flamekeeper", user_id=900):
    return _M(user_id, "keeper", guild=guild, roles=[_R(1, role)])


def _click(guild, user, message_id=None):
    it = _Interaction(user=user, perms=_Perms())
    it.guild = guild
    it.response = _ClickResponse()
    it.message = _Message(message_id) if message_id else None
    return it


def _cfg(**over):
    base = dict(
        journey_enabled=True, welcome_cards_enabled=True, discord_guild_id=GUILD_ID,
        posting_mode="test", test_channel_id=LAB, front_door_channel_id=FRONT_DOOR,
        journey_invite_labels=(("storefront02", "Storefronts"),),
    )
    base.update(over)
    return Config(**base)


def _cog(db, guild, **over):
    cog = JourneyCog(_JBot(db, _cfg(**over), [guild]))
    cog.messenger = _Messenger()
    return cog


async def _arrive(cog, guild, member_id=NEWBIE, **kw):
    member = guild.add(_RM(member_id, kw.pop("name", "Emberlyn (Dave)"), **kw))
    await cog.on_member_join(member)
    return member


def _field(embed, name):
    return next(f.value for f in embed.fields if f.name == name)


async def _press(action, it, *args):
    """A click, routed the way discord.py routes it after a restart."""
    await _persistent.dispatch(it, "journey", action, [str(a) for a in args])


# --- config -----------------------------------------------------------------


def test_new_flags_default_off_and_parse(monkeypatch):
    cfg = Config()
    assert cfg.welcome_cards_enabled is False and cfg.journey_cards_enabled is False
    assert cfg.journey_staff_roles == ("Flamekeeper", "Officer", "GM")
    assert cfg.journey_role_allowlist == ("Ashes", "Visitor")
    assert cfg.journey_placement_roles == ("Ashes", "Visitor")
    assert cfg.front_door_stage == "test"
    # Cards need the journey itself.
    assert Config(welcome_cards_enabled=True).journey_cards_enabled is False

    for key, value in {
        "WELCOME_CARDS_ENABLED": "1", "JOURNEY_ENABLED": "1",
        "FRONT_DOOR_CHANNEL_ID": "222",
        "JOURNEY_STAFF_ROLES": "Flamekeeper, Officer",
        "JOURNEY_ROLE_ALLOWLIST": "Ashes,Visitor",
        "JOURNEY_RANK_ROLES": "Ashes,Visitor,Raider",
        "JOURNEY_INTEREST_ROLES": "Mythic+, PvP",
        "JOURNEY_INVITE_LABELS": "storefront02=Storefronts, bad, =x, oldShared01=Old shared link",
        "GAME_LINKED_CHANNEL_IDS": "5,6",
    }.items():
        monkeypatch.setenv(key, value)
    cfg = Config.from_env()
    assert cfg.journey_cards_enabled and cfg.front_door_channel_id == "222"
    assert cfg.journey_staff_roles == ("Flamekeeper", "Officer")
    assert cfg.journey_interest_roles == ("Mythic+", "PvP")
    assert cfg.invite_labels == {
        "storefront02": "Storefronts", "oldShared01": "Old shared link",
    }
    # Raider counts as placed but is not Calcifer's to give: no button for it.
    assert cfg.journey_placement_roles == ("Ashes", "Visitor")
    assert cfg.game_linked_channel_ids == ("5", "6")


# --- the posting gate ---------------------------------------------------------


def test_gate_never_resolves_to_a_game_linked_channel():
    live = PostGate(PostingMode.LIVE, LAB, frozenset({"500"}))
    assert live.resolve("500", promoted=True) is None
    assert live.resolve("600", promoted=True) == "600"
    # Even a test channel pointed at one by mistake is refused.
    assert PostGate(PostingMode.TEST, "500", frozenset({"500"})).resolve("1") is None
    assert PostGate.from_config(
        Config(posting_mode="live", game_linked_channel_ids=("500",))
    ).resolve("500", promoted=True) is None


async def test_front_door_resolves_through_the_gate(db):
    assert await effective_front_door_channel(db, _cfg(posting_mode="off")) is None
    assert await effective_front_door_channel(db, _cfg()) == LAB
    # Live, but not promoted yet: still the lab.
    live = _cfg(posting_mode="live")
    assert await effective_front_door_channel(db, live) == LAB

    cog = PostingCog(_JBot(db, live))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await PostingCog.front_door.callback(cog, it, discord.app_commands.Choice(
        name="live", value="live"))
    assert f"<#{FRONT_DOOR}>" in it.response.content
    assert await effective_front_door_channel(db, live) == FRONT_DOOR
    # Promotion never overrides test mode.
    assert await effective_front_door_channel(db, _cfg()) == LAB

    back = _Interaction(perms=_Perms(manage_guild=True))
    await PostingCog.front_door.callback(cog, back, discord.app_commands.Choice(
        name="test", value="test"))
    assert "sandboxed" in back.response.content
    assert await effective_front_door_channel(db, live) == LAB

    denied = _Interaction()
    await PostingCog.front_door.callback(cog, denied, discord.app_commands.Choice(
        name="live", value="live"))
    assert await effective_front_door_channel(db, live) == LAB


async def test_front_door_in_a_game_linked_channel_posts_nowhere(db):
    cfg = _cfg(posting_mode="live", front_door_stage="live",
               game_linked_channel_ids=(FRONT_DOOR,))
    assert await effective_front_door_channel(db, cfg) is None


# --- the renderer (pure) -------------------------------------------------------


def _status(**over):
    base = dict(
        discord_id=str(NEWBIE), display_name="Emberlyn (Dave)",
        joined_at="2026-11-16T12:00:00+00:00", left_at=None, came_via="storefront02",
    )
    base.update(over)
    return journey.Status(**base)


def test_card_renders_a_new_member():
    match = journey.RosterMatch(Member("Emberlyn"), "Ashes", journey.LIKELY)
    card = render_card(_status(), _cfg(), match)
    embed = card.embed
    assert embed.title == "👋 New member"
    assert f"<@{NEWBIE}>" in embed.description
    assert f"<t:{account_created_unix(str(NEWBIE))}:R>" in embed.description
    assert "<t:1794830400:F>" in embed.description
    assert _field(embed, "Came via") == "Storefronts"
    assert _field(embed, "Picked") == "nothing yet"
    assert _field(embed, "Roster match") == "**Emberlyn** · Ashes · likely"
    assert _field(embed, "Status") == "Not greeted yet\nNot placed yet"
    assert [(b.label, b.disabled) for b in card.buttons] == [
        ("Said hi", False), ("Set Ashes", False), ("Set Visitor", False),
    ]
    assert "—" not in embed.footer.text


def test_card_shows_who_did_what_and_greys_out_done_buttons():
    status = _status(
        greeted_by="900", greeted_at="2026-11-16T12:05:00+00:00",
        placed_role="Ashes", placed_by="901", placed_at="2026-11-16T12:06:00+00:00",
        checked_in_by="900", checked_in_at="2026-11-23T12:00:00+00:00",
    )
    card = render_card(status, _cfg(), picked=("Mythic+",))
    lines = _field(card.embed, "Status").split("\n")
    assert lines[0].startswith("Said hi: <@900> <t:")
    assert lines[1].startswith("Placed: **Ashes** by <@901> <t:")
    assert lines[2].startswith("Checked in: <@900>")
    assert _field(card.embed, "Picked") == "Mythic+"
    assert _field(card.embed, "Roster match") == "no match"
    assert [(b.label, b.disabled) for b in card.buttons] == [
        ("Said hi", True), ("Set Ashes", True), ("Set Visitor", False),
    ]


def test_card_for_a_rejoin_a_leaver_and_odd_sources():
    rejoin = render_card(
        _status(rejoin=True, previous_joined_at="2026-06-01T09:00:00+00:00",
                came_via="unknown"), _cfg())
    assert rejoin.embed.title == "👋 Back again"
    assert _field(rejoin.embed, "Previous join").endswith(":D>")
    assert _field(rejoin.embed, "Came via") == "unknown"

    left = render_card(_status(left_at="2026-11-16T13:00:00+00:00", came_via="zzz"), _cfg())
    assert "before anyone got to them" in _field(left.embed, "Status")
    assert all(b.disabled for b in left.buttons)
    assert _field(left.embed, "Came via") == "`zzz`"  # unlabelled, still traceable

    handled = render_card(
        _status(left_at="2026-11-16T13:00:00+00:00", placed_role="Visitor",
                placed_at="2026-11-16T12:30:00+00:00", came_via="vanity"), _cfg())
    text = _field(handled.embed, "Status")
    assert "**Left**" in text and "before anyone" not in text
    assert "someone, in Discord" in text  # placed by hand: nobody to credit
    assert _field(handled.embed, "Came via") == "the vanity link"
    assert journey.invite_label(None, {}) == "unknown"
    assert journey.invite_label("baseline", {}) == "here before the journey began"


def test_time_helpers_cope_with_junk():
    assert stamp(None) == "" and stamp("not a date") == ""
    assert account_created_unix("abc") is None
    bare = render_card(_status(discord_id="abc", joined_at=None, display_name=None), _cfg())
    assert "unknown" in bare.embed.description


# --- status from the log (pure) -------------------------------------------------


def _ev(kind, at, actor=None, **data):
    return {"type": kind, "at": at, "actor_id": actor, "data": data}


def test_status_counts_only_the_current_stay():
    person = {"discord_id": "1", "display_name": "x", "joined_at": "t5",
              "left_at": None, "came_via": "aaa"}
    events = [
        _ev("joined", "t1"), _ev("greeted", "t2", "900"),
        _ev("placed", "t3", "900", role="Visitor"), _ev("left", "t4"),
        _ev("rejoined", "t5", previous_joined_at="t1"), _ev("came_via", "t5", code="aaa"),
    ]
    status = journey.status_from(person, events, rank_roles=("Ashes", "Visitor"))
    assert status.rejoin and status.previous_joined_at == "t1"
    assert not status.greeted and not status.placed and not status.handled


def test_status_reads_hand_given_roles_and_interest_picks():
    person = {"discord_id": "1", "display_name": "x", "joined_at": "t1",
              "left_at": None, "came_via": "aaa"}
    kw = dict(rank_roles=("Ashes", "Visitor"), interest_roles=("Mythic+", "PvP"))
    events = [
        _ev("joined", "t1"),
        _ev("roles_changed", "t2", added=["Mythic+", "PvP", "Visitor"], removed=[]),
    ]
    status = journey.status_from(person, events, **kw)
    assert status.placed_role == "Visitor" and status.placed_by is None
    assert status.picked == ("Mythic+", "PvP")

    # A button click after the role landed credits the clicker...
    events.append(_ev("placed", "t3", "900", role="Visitor"))
    assert journey.status_from(person, events, **kw).placed_by == "900"
    # ...and the role event arriving after the click does not un-credit them.
    events.append(_ev("roles_changed", "t4", added=["Visitor"], removed=["PvP"]))
    status = journey.status_from(person, events, **kw)
    assert status.placed_by == "900" and status.picked == ("Mythic+",)
    # Taking the role away again un-places them.
    events.append(_ev("roles_changed", "t5", added=[], removed=["Visitor"]))
    assert journey.status_from(person, events, **kw).placed is False


async def test_staff_actions_on_a_stranger_do_nothing(db):
    cfg = _cfg()
    assert await journey.status_for(db, cfg, "404") is None
    assert await journey.record_greeted(db, cfg, "404", "900") is None
    assert await journey.record_placed(db, cfg, "404", "Ashes", "900") is None
    assert await db.journey_events() == []


# --- the role allow-list ----------------------------------------------------------


async def test_set_rank_role_swaps_within_the_allow_list_only():
    guild = _RoleGuild("Ashes", "Visitor", "Officer")
    cfg = _cfg()
    member = guild.add(_RM(1, roles=[guild.role("Visitor"), guild.role("Officer")]))
    await set_rank_role(member, "ashes", cfg, reason="test")
    assert {r.name for r in member.roles} == {"Ashes", "Officer"}  # Officer untouched
    await set_rank_role(member, "Ashes", cfg, reason="test")  # already held: no-op
    assert len(member.reasons) == 1

    with pytest.raises(journey.RoleNotAllowed):
        await set_rank_role(member, "Officer", cfg, reason="test")
    assert {r.name for r in member.roles} == {"Ashes", "Officer"}
    with pytest.raises(RoleMissing):  # allowed, but the server has no such role
        await set_rank_role(
            member, "Ghost", _cfg(journey_role_allowlist=("Ghost",)), reason="test"
        )


def test_interest_roles_join_the_allow_list_and_others_never_do():
    cfg = _cfg(journey_interest_roles=("Mythic+",))
    journey.check_role_allowed("mythic+", cfg)
    journey.check_role_allowed("Visitor", cfg)
    for role in ("Officer", "Flamekeeper", "@everyone", "Admin"):
        with pytest.raises(journey.RoleNotAllowed):
            journey.check_role_allowed(role, cfg)


# --- the staff guard --------------------------------------------------------------


def test_staff_guard_is_role_or_gm_only():
    cfg = _cfg(gm_discord_id=7)
    guild = _RoleGuild()
    assert is_staff(_click(guild, _staff(guild, "Flamekeeper")), cfg)
    assert is_staff(_click(guild, _staff(guild, "officer")), cfg)  # case-insensitive
    assert is_staff(_click(guild, _M(7, guild=guild)), cfg)  # the GM, by id
    assert not is_staff(_click(guild, _staff(guild, "Ashes")), cfg)
    # Manage Server alone is not the welcome team.
    admin = _click(guild, _M(5, guild=guild))
    admin.permissions = _Perms(manage_guild=True)
    assert not is_staff(admin, cfg)


# --- posting the card ---------------------------------------------------------------


async def test_join_posts_one_card_to_the_lab_in_test_mode(db):
    guild = _RoleGuild("Ashes", "Visitor")
    cog = _cog(db, guild)
    await db.upsert_synced(Member("Emberlyn", guild_rank=4))
    await cog.on_ready()
    await _arrive(cog, guild)

    assert len(cog.messenger.sent) == 1
    channel, embed, view = cog.messenger.sent[0]
    assert channel == LAB  # test mode: never the real channel
    assert _field(embed, "Roster match") == "**Emberlyn** · Rank 4 · likely"
    ids = [item.custom_id for item in view.children]
    assert ids == [
        f"calcifer:journey:greet:{NEWBIE}",
        f"calcifer:journey:place:{NEWBIE}:Ashes",
        f"calcifer:journey:place:{NEWBIE}:Visitor",
    ]
    assert view.timeout is None  # persistent
    card = await db.get_journey_card(str(NEWBIE))
    assert card["message_id"] == "msg1" and card["channel_id"] == LAB


async def test_no_card_when_off_blocked_or_broken(db, caplog):
    guild = _RoleGuild("Ashes", "Visitor")
    for over in ({"welcome_cards_enabled": False}, {"posting_mode": "off"}):
        cog = _cog(db, guild, **over)
        await _arrive(cog, guild, member_id=NEWBIE + len(over))
        assert cog.messenger.sent == []

    cog = _cog(db, guild)
    cog.messenger.fail = True
    with caplog.at_level("WARNING", logger="ftabot.journey"):
        await _arrive(cog, guild, member_id=42, name="Secretname")
    # The join is still recorded, and the log names the failure, not the person.
    assert await db.get_person("42") is not None
    assert "welcome card failed (RuntimeError)" in caplog.text
    assert "Secretname" not in caplog.text and "42" not in caplog.text.replace("RuntimeError", "")
    # A stranger has no card to build or refresh.
    assert await cog.build_card("nobody") is None
    assert await cog.refresh_card("nobody") is False
    assert await cog.post_card(_RM(404)) is False


async def test_messenger_sends_and_edits_without_pings(db):
    from ftabot.cogs.journey import FrontDoorMessenger

    class _Msg:
        id = 77

        async def edit(self, **kw):
            self.kw = kw

    class _Chan:
        def __init__(self):
            self.msg = _Msg()

        async def send(self, **kw):
            self.kw = kw
            return self.msg

        async def fetch_message(self, mid):
            if mid == 404:
                raise discord.NotFound.__new__(discord.NotFound)
            return self.msg

    class _B:
        def __init__(self):
            self.chan = _Chan()

        def get_channel(self, cid):
            return None

        async def fetch_channel(self, cid):
            return self.chan

    bot = _B()
    messenger = FrontDoorMessenger(bot)
    embed = discord.Embed(title="x")
    assert await messenger.send("1", embed) == "77"
    assert "view" not in bot.chan.kw
    none = bot.chan.kw["allowed_mentions"]
    assert not (none.everyone or none.users or none.roles)
    view = to_view(render_card(_status(), _cfg()).buttons)
    assert await messenger.send("1", embed, view) == "77" and bot.chan.kw["view"] is view
    assert await messenger.edit("1", "77", embed, view) is True
    assert not bot.chan.msg.kw["allowed_mentions"].users
    assert await messenger.edit("1", "404", embed) is False
    assert to_view([]) is None


# --- the buttons ------------------------------------------------------------------


async def _ready(db, **over):
    guild = _RoleGuild("Ashes", "Visitor", "Officer")
    cog = _cog(db, guild, **over)
    await cog.on_ready()
    member = await _arrive(cog, guild)
    return cog, guild, member


async def test_said_hi_logs_once_and_edits_the_card(db):
    cog, guild, _ = await _ready(db)
    it = _click(guild, _staff(guild), message_id="msg1")
    await _press("greet", it, NEWBIE)

    event = (await db.journey_events(type="greeted"))[0]
    assert event["actor_id"] == "900"
    embed, view = it.response.edited
    assert "Said hi: <@900>" in _field(embed, "Status")
    assert view.children[0].item.disabled is True  # greyed out
    assert not it.response.allowed_mentions.users

    # A second click (a stale client, another Flamekeeper) logs nothing more.
    again = _click(guild, _staff(guild, user_id=901), message_id="msg1")
    await _press("greet", again, NEWBIE)
    assert len(await db.journey_events(type="greeted")) == 1
    assert "Said hi: <@900>" in _field(again.response.edited[0], "Status")


async def test_set_ashes_applies_the_role_logs_and_is_idempotent(db):
    cog, guild, member = await _ready(db)
    it = _click(guild, _staff(guild, "Officer"), message_id="msg1")
    await _press("place", it, NEWBIE, "Ashes")

    assert [r.name for r in member.roles] == ["Ashes"]
    assert member.reasons == ["Placed by 900 via Calcifer"]
    event = (await db.journey_events(type="placed"))[0]
    assert event["actor_id"] == "900" and event["data"] == {"role": "Ashes"}
    embed, view = it.response.edited
    assert "Placed: **Ashes** by <@900>" in _field(embed, "Status")
    assert [c.item.disabled for c in view.children] == [False, True, False]

    await _press("place", _click(guild, _staff(guild), message_id="msg1"), NEWBIE, "Ashes")
    assert len(await db.journey_events(type="placed")) == 1

    # A correction to Visitor swaps the role and is a new placement.
    await _press("place", _click(guild, _staff(guild), message_id="msg1"), NEWBIE, "Visitor")
    assert [r.name for r in member.roles] == ["Visitor"]
    assert [e["data"]["role"] for e in await db.journey_events(type="placed")] == [
        "Ashes", "Visitor",
    ]


async def test_buttons_refuse_non_staff(db):
    cog, guild, member = await _ready(db)
    for action, args in (("greet", ()), ("place", ("Ashes",))):
        it = _click(guild, _staff(guild, "Ashes"), message_id="msg1")
        await _press(action, it, NEWBIE, *args)
        assert it.response.ephemeral and "welcome team" in it.response.content
        assert it.response.edited is None
    assert member.roles == []
    assert await db.journey_events(type="greeted") == []
    assert await db.journey_events(type="placed") == []


async def test_a_forged_button_cannot_set_a_role_off_the_list(db):
    cog, guild, member = await _ready(db)
    it = _click(guild, _staff(guild), message_id="msg1")
    await _press("place", it, NEWBIE, "Officer")  # the role exists in the server
    assert "not allowed" in it.response.content and it.response.ephemeral
    assert member.roles == [] and await db.journey_events(type="placed") == []


async def test_placement_problems_are_explained_and_not_logged(db):
    cog, guild, member = await _ready(db, journey_role_allowlist=("Ashes", "Ghost"),
                                      journey_rank_roles=("Ashes", "Ghost"))
    missing = _click(guild, _staff(guild), message_id="msg1")
    await _press("place", missing, NEWBIE, "Ghost")
    assert "no role called **Ghost**" in missing.response.content

    guild.deny = True
    denied = _click(guild, _staff(guild), message_id="msg1")
    await _press("place", denied, NEWBIE, "Ashes")
    assert "Manage Roles" in denied.response.content
    assert await db.journey_events(type="placed") == []

    stranger = _click(guild, _staff(guild), message_id="msg1")
    await _press("greet", stranger, 31337)
    assert "no record" in stranger.response.content


async def test_a_click_from_another_message_replies_privately_and_updates_the_card(db):
    cog, guild, _ = await _ready(db)
    it = _click(guild, _staff(guild), message_id="some-list-message")
    await _press("greet", it, NEWBIE)
    assert it.response.edited is None
    assert it.response.ephemeral and "you said hi" in it.response.content
    assert not it.response.kwargs["allowed_mentions"].users
    assert cog.messenger.edits[-1][1] == "msg1"

    again = _click(guild, _staff(guild), message_id="some-list-message")
    await _press("greet", again, NEWBIE)
    assert "Already greeted by <@900>" in again.response.content


async def test_an_unknown_button_gets_a_quiet_reply():
    it = _click(_RoleGuild(), _M(1))
    await _persistent.dispatch(it, "journey", "retired_action", [])
    assert it.response.ephemeral and "no longer in use" in it.response.content


# --- the card keeps itself current --------------------------------------------------


async def test_member_leaving_unhandled_updates_the_card(db):
    cog, guild, member = await _ready(db)
    guild._cache.pop(member.id)
    await cog.on_member_remove(member)
    _, message_id, embed, view = cog.messenger.edits[-1]
    assert message_id == "msg1"
    assert "before anyone got to them" in _field(embed, "Status")
    assert all(c.item.disabled for c in view.children)

    # A click on the stale card is turned away, kindly.
    it = _click(guild, _staff(guild), message_id="msg1")
    await _press("greet", it, NEWBIE)
    assert it.response.content == "They have left the server."
    assert await db.journey_events(type="greeted") == []


async def test_rejoin_gets_a_fresh_card_marked_as_a_rejoin(db):
    cog, guild, member = await _ready(db)
    await _press("greet", _click(guild, _staff(guild), message_id="msg1"), NEWBIE)
    await cog.on_member_remove(member)
    await cog.on_member_join(member)

    assert len(cog.messenger.sent) == 2
    embed = cog.messenger.sent[1][1]
    assert embed.title == "👋 Back again"
    assert _field(embed, "Previous join").startswith("<t:")
    assert _field(embed, "Status").startswith("Not greeted yet")  # a clean slate
    assert (await db.get_journey_card(str(NEWBIE)))["message_id"] == "msg2"


async def test_roles_and_nickname_changes_refresh_the_card(db):
    cog, guild, member = await _ready(db, journey_interest_roles=("Mythic+",))
    before = _RM(NEWBIE, "Emberlyn (Dave)", guild=guild)
    member.roles = [_R(50, "Mythic+"), guild.role("Visitor")]
    await cog.on_member_update(before, member)
    embed = cog.messenger.edits[-1][2]
    assert _field(embed, "Picked") == "Mythic+"
    assert "Placed: **Visitor** by someone, in Discord" in _field(embed, "Status")

    # An unrelated role changes nothing on the card.
    edits = len(cog.messenger.edits)
    other = _RM(NEWBIE, "Emberlyn (Dave)", guild=guild,
                roles=[*member.roles, _R(60, "Birthday")])
    await cog.on_member_update(member, other)
    assert len(cog.messenger.edits) == edits


async def test_a_deleted_card_is_forgotten_not_reposted(db):
    cog, guild, member = await _ready(db)
    cog.messenger.gone = True
    assert await cog.refresh_card(str(NEWBIE), member) is False
    assert await db.get_journey_card(str(NEWBIE)) is None
    assert len(cog.messenger.sent) == 1


# --- restart-proof buttons ----------------------------------------------------------


def test_custom_ids_are_stable_and_validated():
    assert _persistent.custom_id("journey", "greet", 5) == "calcifer:journey:greet:5"
    with pytest.raises(ValueError):
        _persistent.custom_id("journey", "place", 5, "bad:role")
    with pytest.raises(ValueError):
        _persistent.custom_id("journey", "place", "x" * 100)


async def test_a_button_is_rebuilt_from_its_custom_id_after_a_restart(db):
    cog, guild, member = await _ready(db)
    template = _persistent.ActionButton.__discord_ui_compiled_template__
    match = template.fullmatch(f"calcifer:journey:place:{NEWBIE}:Visitor")
    it = _click(guild, _staff(guild), message_id="msg1")
    rebuilt = await _persistent.ActionButton.from_custom_id(it, None, match)
    assert (rebuilt.namespace, rebuilt.action, rebuilt.args) == (
        "journey", "place", [str(NEWBIE), "Visitor"],
    )
    await rebuilt.callback(it)
    assert [r.name for r in member.roles] == ["Visitor"]
    bare = template.fullmatch("calcifer:journey:noargs")
    assert (await _persistent.ActionButton.from_custom_id(it, None, bare)).args == []


async def test_setup_hook_registers_the_dynamic_button(tmp_path, monkeypatch):
    from ftabot.bot import FtaBot

    bot = FtaBot(Config(database_path=str(tmp_path / "b.db"), journey_enabled=True,
                        bot_commands_enabled=False))
    seen = []
    monkeypatch.setattr(bot, "add_dynamic_items", lambda *items: seen.extend(items))

    async def _sync(*, guild=None):
        return None

    monkeypatch.setattr(bot.tree, "sync", _sync)
    try:
        await bot.setup_hook()
        assert seen == [_persistent.ActionButton]
    finally:
        await bot.close()


async def test_retention_also_drops_the_card_pointer(db):
    await journey.record_join(db, discord_id="1", display_name="x")
    await db.set_journey_card("1", LAB, "m1")
    await journey.record_leave(db, "1", at="2020-01-01T00:00:00+00:00")
    assert await journey.prune(db, 180) == 1
    assert await db.get_journey_card("1") is None
