"""Remaining branch coverage for the officer cogs ``admin`` and ``feeds``.

test_cogs.py covers the happy paths; this fills in the denial guards, the
member/rule not-found branches, the clear/unlink variants, and the two render
paths that need a bit more scaffolding (feed route with a ping, and /feed test
posting a sample to a channel). Same offline harness as test_cogs.
"""

from __future__ import annotations

import dataclasses

import discord

from ftabot.cogs.admin import AdminCog
from ftabot.cogs.feeds import FeedsCog

from .test_cogs import _Bot, _cfg, _Channel, _choice, _Interaction, _Perms, _seed, _User


def _cfg_with(**over):
    """The shared test config with a few fields overridden (it takes no kwargs)."""
    return dataclasses.replace(_cfg(), **over)

# --- admin: denial guards ---------------------------------------------------


async def test_admin_commands_denied_for_non_admin(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    invocations = [
        lambda it: AdminCog.sync.callback(cog, it, prune=True),
        lambda it: AdminCog.link.callback(cog, it, "Ashbringer", user=_User(id=1)),
        lambda it: AdminCog.setmain.callback(cog, it, "Emberlyn", main="Ashbringer"),
        lambda it: AdminCog.setteam.callback(cog, it, "Ashbringer", team="Cinder"),
        lambda it: AdminCog.setofficer.callback(cog, it, "Ashbringer", role="Loot"),
    ]
    for invoke in invocations:
        it = _Interaction(perms=_Perms())  # plain user
        await invoke(it)
        assert it.response.ephemeral is True
        assert "Officer" in it.response.content
    # The roster was never mutated by a denied command (raid_team stays as the
    # seed set it; setmain didn't flip is_main).
    m = await db.get_member("Emberlyn", "hellfire", "eu")
    assert m.is_main is True and m.raid_team == "Cinder"


# --- admin: link unlink + not found -----------------------------------------


async def test_admin_link_unlinks_when_user_omitted(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    # First link, then unlink (user=None).
    await AdminCog.link.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer", user=_User(id=5)
    )
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.link.callback(cog, it, "Ashbringer", user=None)
    assert "Unlinked" in it.response.content
    m = await db.get_member("Ashbringer", "hellfire", "eu")
    assert m.discord_id is None


async def test_admin_link_unknown_member(db):
    cog = AdminCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.link.callback(cog, it, "Ghost", user=_User(id=5))
    assert it.response.ephemeral is True
    assert "No member" in it.response.content


# --- admin: setmain variants ------------------------------------------------


async def test_admin_setmain_flags_as_main_when_main_omitted(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    # Make Emberlyn an alt first, then clear it back to a main.
    await AdminCog.setmain.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Emberlyn", main="Ashbringer"
    )
    it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setmain.callback(cog, it, "Emberlyn", main=None)
    assert "flagged as a main" in it.response.content
    m = await db.get_member("Emberlyn", "hellfire", "eu")
    assert m.is_main is True and m.main_character_id is None


async def test_admin_setmain_unknown_alt_and_main(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))

    alt_missing = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setmain.callback(cog, alt_missing, "Ghost", main="Ashbringer")
    assert "No member named **Ghost**" in alt_missing.response.content

    main_missing = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setmain.callback(cog, main_missing, "Emberlyn", main="Ghost")
    assert "No main named **Ghost**" in main_missing.response.content


# --- admin: setteam / setofficer not found (the shared _lookup) --------------


async def test_admin_setteam_and_setofficer_unknown_member(db):
    cog = AdminCog(_Bot(db, _cfg()))

    team_it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setteam.callback(cog, team_it, "Ghost", team="Cinder")
    assert "No member" in team_it.response.content

    off_it = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setofficer.callback(cog, off_it, "Ghost", role="Loot")
    assert "No member" in off_it.response.content


async def test_admin_setteam_and_setofficer_clear(db):
    await _seed(db)
    cog = AdminCog(_Bot(db, _cfg()))
    # Set then clear, to cover the "removed/cleared" message branches.
    await AdminCog.setteam.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer", team="Cinder"
    )
    clear_team = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setteam.callback(cog, clear_team, "Ashbringer", team=None)
    assert "removed from their raid team" in clear_team.response.content

    await AdminCog.setofficer.callback(
        cog, _Interaction(perms=_Perms(manage_guild=True)), "Ashbringer", role="Loot"
    )
    clear_off = _Interaction(perms=_Perms(manage_guild=True))
    await AdminCog.setofficer.callback(cog, clear_off, "Ashbringer", role=None)
    assert "Cleared officer role" in clear_off.response.content


# --- feeds: denial guards ---------------------------------------------------


async def test_feed_commands_denied_for_non_admin(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    invocations = [
        lambda it: FeedsCog.list_rules.callback(cog, it),
        lambda it: FeedsCog.remove.callback(cog, it, 1),
        lambda it: FeedsCog.disable.callback(cog, it, 1),
        lambda it: FeedsCog.enable.callback(cog, it, 1),
        lambda it: FeedsCog.mute.callback(cog, it, _choice("wowhead")),
        lambda it: FeedsCog.unmute.callback(cog, it, _choice("wowhead")),
        lambda it: FeedsCog.test.callback(cog, it, _choice("wowhead")),
    ]
    for invoke in invocations:
        it = _Interaction(perms=_Perms())
        await invoke(it)
        assert it.response.ephemeral is True
        assert "Officer" in it.response.content


# --- feeds: route with a ping + colour --------------------------------------


class _Role:
    def __init__(self, mention="<@&77>"):
        self.mention = mention


async def test_feed_route_with_ping_and_colour(db):
    cog = FeedsCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.route.callback(
        cog, it, _choice("wowhead"), _Channel(123),
        color=_choice("amber"), ping=_Role("<@&77>"),
    )
    assert "ping <@&77>" in it.response.content
    rule = (await db.all_feed_rules())[0]
    assert rule["style"]["ping"] == "<@&77>"
    assert rule["style"]["color"] == "amber"


# --- feeds: list with muted sources + a default channel ---------------------


async def test_feed_list_shows_muted_and_default_channel(db):
    cog = FeedsCog(_Bot(db, _cfg_with(feed_default_channel_id="999")))
    await db.mute_source("wowhead")
    it = _Interaction(perms=_Perms(manage_guild=True))
    await FeedsCog.list_rules.callback(cog, it)
    desc = it.response.embed.description
    assert "Muted: wowhead" in desc
    assert "Default channel: <#999>" in desc


# --- feeds: /feed test posts a sample ---------------------------------------


class _PostChannel:
    def __init__(self, cid=999):
        self.id = cid
        self.mention = f"<#{cid}>"
        self.sent = []

    async def send(self, *, content=None, embed=None):
        self.sent.append((content, embed))


class _Guild:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, cid):
        return self._channel


async def test_feed_test_posts_sample_to_default_channel(db):
    channel = _PostChannel(999)
    cog = FeedsCog(_Bot(db, _cfg_with(feed_default_channel_id="999")))
    it = _Interaction(perms=_Perms(manage_guild=True))
    it.guild = _Guild(channel)  # routes to the default channel via the guild

    await FeedsCog.test.callback(cog, it, _choice("raiderio_mplus"))

    assert len(channel.sent) == 1
    _content, embed = channel.sent[0]
    assert isinstance(embed, discord.Embed)
    assert "Posted a **raiderio_mplus** sample" in it.response.content
