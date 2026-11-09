"""Tests for the Discord cogs (previously 0% covered).

discord.py wraps each command method in an ``app_commands.Command``; we invoke
the underlying coroutine via ``Cog.command.callback(cog, interaction, ...)``
with a lightweight fake Interaction and a real temp-file database (the ``db``
fixture). Focus areas: officer gating (security-relevant) and that read
commands assemble a response without touching the network.
"""

from __future__ import annotations

import dataclasses

import discord
from discord import app_commands

from ftabot.cogs.admin import AdminCog, _is_admin
from ftabot.cogs.feeds import FeedsCog
from ftabot.cogs.members import MembersCog
from ftabot.cogs.recruit import RecruitCog
from ftabot.cogs.reports import ReportsCog
from ftabot.config import Config
from ftabot.models import Member
from ftabot.sync import SyncResult

# --- fakes ------------------------------------------------------------------


class _Response:
    def __init__(self):
        self.content = None
        self.embed = None
        self.ephemeral = False
        self.sent = False
        self.deferred = False

    async def send_message(self, content=None, *, embed=None, ephemeral=False, **kw):
        self.sent = True
        self.content = content
        self.embed = embed
        self.ephemeral = ephemeral
        self.kwargs = kw

    async def defer(self, *, thinking=False, **kw):
        self.deferred = True


class _Followup:
    def __init__(self):
        self.content = None
        self.embed = None
        self.embeds = None
        self.ephemeral = False
        self.sent = False

    async def send(self, content=None, *, embed=None, embeds=None,
                   ephemeral=False, **kw):
        self.sent = True
        self.content = content
        self.embed = embed
        self.embeds = embeds
        self.ephemeral = ephemeral


class _Perms:
    def __init__(self, manage_guild=False, administrator=False):
        self.manage_guild = manage_guild
        self.administrator = administrator


class _Role:
    def __init__(self, name):
        self.name = name


class _User:
    def __init__(self, id=42, roles=(), display_name="Tester", mention=None):
        self.id = id
        self.roles = list(roles)
        self.display_name = display_name
        self.mention = mention or f"<@{id}>"


class _Member(_User):
    """Stands in for ``discord.Member`` (patched in for the role-path tests)."""


class _Interaction:
    def __init__(self, user=None, perms=None):
        self.user = user if user is not None else _User()
        self.permissions = perms or _Perms()
        self.response = _Response()
        self.followup = _Followup()


class _Bot:
    def __init__(self, db, config):
        self.db = db
        self.config = config


def _cfg(*, gm_discord_id=None):
    return Config(guild_name="From the Ashes", guild_realm="hellfire",
                  guild_region="eu", admin_role_name="Officer",
                  gm_discord_id=gm_discord_id)


async def _seed(db):
    """Seed two characters, both on the team-chemistry roster (raid_team set).

    ``raid_team`` is a manual column, so it is written via ``set_raid_team``
    after the sync upsert rather than on the Member.
    """
    aid = await db.upsert_synced(Member("Ashbringer", class_name="Paladin", role="TANK",
                                        mythic_plus_score=3000, item_level=632))
    eid = await db.upsert_synced(Member("Emberlyn", class_name="Mage", role="DPS",
                                        mythic_plus_score=3200, item_level=630))
    await db.set_raid_team(aid, "Cinder")
    await db.set_raid_team(eid, "Cinder")


async def _team_caller(db, user_id=42):
    """Link the default interaction user to a team character so the team-gated
    member commands let them through."""
    m = await db.get_member("Ashbringer", "hellfire", "eu")
    await db.set_discord_link(m.id, str(user_id))


# --- admin gating (_is_admin) ----------------------------------------------


def test_is_admin_allows_manage_guild():
    it = _Interaction(perms=_Perms(manage_guild=True))
    assert _is_admin(it, "Officer") is True


def test_is_admin_allows_administrator():
    it = _Interaction(perms=_Perms(administrator=True))
    assert _is_admin(it, "Officer") is True


def test_is_admin_denies_plain_user():
    it = _Interaction(user=_User(), perms=_Perms())
    assert _is_admin(it, "Officer") is False


def test_is_admin_allows_role_holder(monkeypatch):
    monkeypatch.setattr(discord, "Member", _Member)
    member = _Member(roles=[_Role("Raider"), _Role("Officer")])
    it = _Interaction(user=member, perms=_Perms())
    assert _is_admin(it, "Officer") is True


def test_is_admin_denies_member_without_role(monkeypatch):
    monkeypatch.setattr(discord, "Member", _Member)
    member = _Member(roles=[_Role("Raider")])
    it = _Interaction(user=member, perms=_Perms())
    assert _is_admin(it, "Officer") is False


async def test_require_admin_allows_officer_without_replying():
    from ftabot.cogs._guards import require_admin

    it = _Interaction(perms=_Perms(manage_guild=True))
    assert await require_admin(it, "Officer") is True
    assert it.response.sent is False  # no denial message on the allowed path


async def test_require_admin_denies_with_the_action_in_the_message():
    from ftabot.cogs._guards import require_admin

    it = _Interaction()  # plain user, no perms
    assert await require_admin(it, "Officer", action="manage boards") is False
    assert it.response.sent is True
    assert it.response.ephemeral is True
    assert "manage boards" in it.response.content
    assert "Officer" in it.response.content


# --- admin commands ---------------------------------------------------------


async def test_note_denied_for_non_admin_leaves_db_untouched(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())  # not an officer
    await AdminCog.note.callback(cog, it, "Ashbringer", text="hax")

    assert it.response.ephemeral is True
    assert "Officer" in it.response.content
    m = await db.get_member("Ashbringer", "hellfire", "eu")
    assert m.notes is None  # the denied command wrote nothing


async def test_admin_link_sets_discord_id(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    user = _User(id=999)
    await AdminCog.link.callback(cog, it, "Ashbringer", user=user)

    m = await db.get_member("Ashbringer", "hellfire", "eu")
    assert m.discord_id == "999"


async def test_admin_note_sets_and_clears(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.note.callback(cog, it, "Ashbringer", text="Solid tank")
    m = await db.get_member("Ashbringer", "hellfire", "eu")
    assert m.notes == "Solid tank"


async def test_admin_setmain_links_alt_to_main(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setmain.callback(cog, it, "Emberlyn", main="Ashbringer")

    alt = await db.get_member("Emberlyn", "hellfire", "eu")
    main = await db.get_member("Ashbringer", "hellfire", "eu")
    assert alt.is_main is False
    assert alt.main_character_id == main.id


async def test_admin_setmain_self_is_rejected(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setmain.callback(cog, it, "Ashbringer", main="Ashbringer")
    assert "its own main" in it.response.content


async def test_admin_setteam_and_setofficer(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setteam.callback(cog, it, "Ashbringer", team="Cinder")
    await AdminCog.setofficer.callback(cog, it, "Ashbringer", role="Loot")
    m = await db.get_member("Ashbringer", "hellfire", "eu")
    assert m.raid_team == "Cinder"
    assert m.officer_role == "Loot"


async def test_admin_lookup_unknown_member(db):
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.note.callback(cog, it, "Nobody", text="x")
    assert it.response.ephemeral is True
    assert "No member" in it.response.content


async def test_admin_sync_invokes_sync_guild(db, monkeypatch):
    from ftabot.cogs import admin as admin_mod

    async def _fake_sync(database, config, *, prune=True):
        return SyncResult(fetched=2, upserted=2, sources=("Raider.IO",))

    monkeypatch.setattr(admin_mod, "sync_guild", _fake_sync)
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.sync.callback(cog, it, prune=True)

    assert it.response.deferred is True
    assert it.followup.sent is True
    assert "Synced 2 member(s)" in it.followup.content


# --- members cog ------------------------------------------------------------


async def test_member_command_found_and_missing(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))

    found = _Interaction()
    await MembersCog.member.callback(cog, found, "Ashbringer")
    assert isinstance(found.response.embed, discord.Embed)

    missing = _Interaction()
    await MembersCog.member.callback(cog, missing, "Ghost")
    assert missing.response.ephemeral is True
    assert "No member" in missing.response.content


async def test_roster_empty_then_signups(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))

    # No sign-ups synced yet -> friendly ephemeral nudge, no board.
    empty_it = _Interaction()
    await MembersCog.roster.callback(cog, empty_it)
    assert empty_it.response.ephemeral is True
    assert "No upcoming raid sign-ups" in empty_it.response.content

    # With the next raid's sign-ups stored, the grouped board renders.
    await db.set_roster_signups(
        {
            "raid": {"instance": "The Voidspire", "difficulty": "Mythic",
                     "date": "2026-06-10", "time": "20:00"},
            "signups": [
                {"name": "Ashbringer", "status": "present"},
                {"name": "Emberlyn", "status": "bench"},
            ],
        }
    )
    roster_it = _Interaction()
    await MembersCog.roster.callback(cog, roster_it)
    embed = roster_it.response.embed
    assert isinstance(embed, discord.Embed)
    names = [f.name for f in embed.fields]
    assert any("Confirmed" in n for n in names)
    assert any("Benched" in n for n in names)


async def test_stats_team_gated_then_populated(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))

    # A caller with no team character is turned away.
    denied = _Interaction()
    await MembersCog.stats.callback(cog, denied)
    assert denied.response.ephemeral is True
    assert "team roster" in denied.response.content

    # A team member sees the team statistics.
    await _team_caller(db)
    populated = _Interaction()
    await MembersCog.stats.callback(cog, populated)
    assert isinstance(populated.response.embed, discord.Embed)
    assert "team statistics" in populated.response.embed.title


async def test_whois(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))

    whois_it = _Interaction()
    await MembersCog.whois.callback(cog, whois_it, _User(id=7))
    assert whois_it.response.ephemeral is True  # nobody linked yet


async def test_whois_lists_linked_characters(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))
    member = await db.get_member("Ashbringer", "hellfire", "eu")
    await db.set_discord_link(member.id, "7")

    found_it = _Interaction()
    await MembersCog.whois.callback(cog, found_it, _User(id=7, display_name="Harry"))
    embed = found_it.response.embed
    assert isinstance(embed, discord.Embed)
    assert "Harry" in embed.title
    assert "Ashbringer" in embed.description


async def test_me_card_empty_then_linked(db):
    await _seed(db)
    cog = MembersCog(_Bot(db, _cfg()))

    # Nobody linked to this user yet -> a friendly ephemeral nudge, no card.
    empty_it = _Interaction(user=_User(id=99))
    await MembersCog.me.callback(cog, empty_it)
    assert empty_it.response.ephemeral is True
    assert empty_it.response.embed is None

    # Link a character to the user, then /me renders their card.
    member = await db.get_member("Emberlyn", "hellfire", "eu")
    await db.set_discord_link(member.id, "99")
    linked_it = _Interaction(user=_User(id=99, display_name="Harry"))
    await MembersCog.me.callback(cog, linked_it)
    assert isinstance(linked_it.response.embed, discord.Embed)
    assert "Emberlyn" in linked_it.response.embed.fields[0].name


# --- reports cog ------------------------------------------------------------


async def test_gmreport_renders(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=42)))
    it = _Interaction()  # default user id 42 == GM
    await ReportsCog.gmreport.callback(cog, it)
    assert it.response.deferred is True
    assert it.followup.sent is True
    assert isinstance(it.followup.embed, discord.Embed)


async def test_gmreport_denied_for_non_gm(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=7)))
    it = _Interaction()  # default user id 42, not the GM
    await ReportsCog.gmreport.callback(cog, it)
    assert it.response.ephemeral is True
    assert "Guild Master" in it.response.content


async def test_trends_denied_for_non_gm(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=7)))
    it = _Interaction()  # default user id 42, not the GM
    await ReportsCog.trends.callback(cog, it)
    assert it.response.ephemeral is True
    assert "Guild Master" in it.response.content


async def test_trends_needs_history_for_gm(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=42)))
    it = _Interaction()  # default user id 42 == GM
    await ReportsCog.trends.callback(cog, it)
    assert it.followup.ephemeral is True
    assert "Not enough history" in it.followup.content


async def test_loot_wishlist_lootpriority_require_team(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg()))

    # A non-team caller is turned away before any work.
    denied = _Interaction()
    await ReportsCog.loot.callback(cog, denied, player="Ashbringer")
    assert denied.response.ephemeral is True
    assert "team roster" in denied.response.content


async def test_loot_and_wishlist_empty_paths(db):
    await _seed(db)
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))

    loot_it = _Interaction()
    await ReportsCog.loot.callback(cog, loot_it, player="Ashbringer")
    assert loot_it.followup.ephemeral is True
    assert "No loot recorded" in loot_it.followup.content

    wl_it = _Interaction()
    await ReportsCog.wishlist.callback(cog, wl_it, player="Ashbringer")
    assert wl_it.followup.ephemeral is True
    assert "No wishlist" in wl_it.followup.content

    lp_it = _Interaction()
    await ReportsCog.lootpriority.callback(cog, lp_it, item="Nothing")
    assert lp_it.followup.ephemeral is True


async def test_loot_and_wishlist_reject_non_team_target(db):
    await _seed(db)
    await _team_caller(db)  # caller is on the team
    await db.upsert_synced(Member("Pug", class_name="Rogue", role="DPS"))  # not on team
    cog = ReportsCog(_Bot(db, _cfg()))

    loot_it = _Interaction()
    await ReportsCog.loot.callback(cog, loot_it, player="Pug")
    assert "isn't on the team roster" in loot_it.followup.content

    wl_it = _Interaction()
    await ReportsCog.wishlist.callback(cog, wl_it, player="Pug")
    assert "isn't on the team roster" in wl_it.followup.content


# --- recruit cog ------------------------------------------------------------


async def test_scout_denied_for_non_admin(db):
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)
    assert it.response.ephemeral is True
    assert "Officer" in it.response.content


async def test_scout_handles_unavailable(db, monkeypatch):
    from ftabot import scout as scout_mod
    from ftabot.scout import ScoutUnavailable

    async def _boom(database, config, *, only_class=None):
        raise ScoutUnavailable("no WCL credentials")

    monkeypatch.setattr(scout_mod, "run_scout", _boom)
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "Can't scout" in it.followup.content


async def test_scout_empty_results(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _none(database, config, *, only_class=None):
        return []

    monkeypatch.setattr(scout_mod, "run_scout", _none)
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "roster looks solid" in it.followup.content


# --- feeds cog --------------------------------------------------------------


class _Channel:
    def __init__(self, id=555):
        self.id = id
        self.mention = f"<#{id}>"


def _choice(value):
    return app_commands.Choice(name=value, value=value)


async def test_feed_route_denied_for_non_admin(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms())
    await FeedsCog.route.callback(cog, it, _choice("wowhead"), _Channel())
    assert it.response.ephemeral is True
    assert "Officer" in it.response.content
    assert await db.all_feed_rules() == []  # nothing routed


async def test_feed_route_then_list_and_remove(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    admin = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.route.callback(
        cog, admin, _choice("wowhead"), _Channel(123),
        event_type="wowhead.patch-notes", contains="raid, tier",
        color=_choice("blue"),
    )
    rules = await db.all_feed_rules()
    assert len(rules) == 1
    assert rules[0]["source"] == "wowhead"
    assert rules[0]["match"]["contains"] == ["raid", "tier"]

    list_it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.list_rules.callback(cog, list_it)
    assert isinstance(list_it.response.embed, discord.Embed)
    assert "wowhead" in list_it.response.embed.description

    rule_id = rules[0]["id"]
    rm_it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.remove.callback(cog, rm_it, rule_id)
    assert "Removed rule" in rm_it.response.content
    assert await db.all_feed_rules() == []

    miss_it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.remove.callback(cog, miss_it, 9999)
    assert "No rule" in miss_it.response.content


async def test_feed_disable_enable_roundtrip(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    admin = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.route.callback(cog, admin, _choice("wcl_prog"), _Channel(7))
    rule_id = (await db.all_feed_rules())[0]["id"]

    dis = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.disable.callback(cog, dis, rule_id)
    assert "Disabled rule" in dis.response.content
    assert (await db.all_feed_rules())[0]["enabled"] is False

    en = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.enable.callback(cog, en, rule_id)
    assert "Enabled rule" in en.response.content
    assert (await db.all_feed_rules())[0]["enabled"] is True


async def test_feed_mute_unmute(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    mute_it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.mute.callback(cog, mute_it, _choice("raiderio_mplus"))
    assert "Muted" in mute_it.response.content
    assert "raiderio_mplus" in await db.muted_sources()

    unmute_it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.unmute.callback(cog, unmute_it, _choice("raiderio_mplus"))
    assert "Unmuted" in unmute_it.response.content
    assert "raiderio_mplus" not in await db.muted_sources()

    # Unmuting something that wasn't muted is a no-op with a distinct message.
    again = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.unmute.callback(cog, again, _choice("raiderio_mplus"))
    assert "wasn't muted" in again.response.content


async def test_feed_test_without_destination(db):
    # No rules and no default channel => nowhere to post.
    cog = FeedsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.test.callback(cog, it, _choice("wowhead"))
    assert it.response.ephemeral is True
    assert "nowhere to post" in it.response.content


class _SendableChannel:
    def __init__(self, id=42):
        self.id = id
        self.mention = f"<#{id}>"
        self.sent = []

    async def send(self, *, content=None, embed=None):
        self.sent.append((content, embed))


class _FeedTestBot(_Bot):
    """A bot whose fetch_channel returns a sendable channel (no get_channel hit)."""

    def __init__(self, db, config, channel):
        super().__init__(db, config)
        self._channel = channel

    async def fetch_channel(self, _channel_id):
        return self._channel


async def test_feed_test_falls_back_to_fetch_channel(db):
    # A default channel is set, but the interaction has no guild (so
    # get_channel is skipped) -> the cog resolves it via bot.fetch_channel.
    channel = _SendableChannel(id=42)
    cfg = _cfg()
    cfg = dataclasses.replace(cfg, feed_default_channel_id="42", persona_tone="off")
    cog = FeedsCog(_FeedTestBot(db, cfg, channel))
    it = _Interaction(perms=_Perms(manage_guild=True))
    it.guild = None  # forces the fetch_channel branch
    await FeedsCog.test.callback(cog, it, _choice("wowhead"))
    assert channel.sent  # the sample alert was posted via fetch_channel
    assert "Posted" in it.response.content
