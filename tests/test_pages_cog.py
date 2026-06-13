"""The /page cog: officer gating + the post/move/no-op placement semantics.

Reuses the lightweight interaction fakes from ``test_cogs`` and adds a fake
bot with real-enough channels (send/fetch/edit/delete) so the cog's
``BotBoardMessenger`` path runs end to end, fully offline.
"""

from __future__ import annotations

import discord
from discord import app_commands

from ftabot.cogs.pages import PagesCog
from ftabot.config import Config
from ftabot.pages import PAGES

from .test_cogs import _Channel, _Interaction, _Perms


def _page_choice(value="rules_info"):
    page = PAGES[value]
    return app_commands.Choice(name=page.title, value=page.name)


class _NotFoundResponse:
    status = 404
    reason = "Not Found"


class _FakeMessage:
    def __init__(self, message_id: int, channel: "_FakeDiscordChannel", content: str):
        self.id = message_id
        self.content = content
        self._channel = channel

    async def edit(self, *, content=None, embed=None, allowed_mentions=None):
        self.content = content

    async def delete(self):
        self._channel.messages.pop(self.id, None)


class _FakeDiscordChannel:
    """Stands in for a discord.TextChannel reached via bot.get_channel."""

    _next_id = 1000

    def __init__(self, channel_id: int):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.messages: dict[int, _FakeMessage] = {}

    async def send(self, content=None, *, embed=None, allowed_mentions=None):
        _FakeDiscordChannel._next_id += 1
        message = _FakeMessage(_FakeDiscordChannel._next_id, self, content)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int):
        if message_id not in self.messages:
            raise discord.NotFound(_NotFoundResponse(), "message gone")
        return self.messages[message_id]


class _PageBot:
    """A fake FtaBot: db + config + a channel registry for the messenger."""

    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.channels: dict[int, _FakeDiscordChannel] = {}

    def get_channel(self, channel_id: int):
        return self.channels.setdefault(channel_id, _FakeDiscordChannel(channel_id))


def _live_cfg():
    return Config(admin_role_name="Officer", posting_mode="live")


def _admin():
    return _Interaction(perms=_Perms(manage_guild=True))


# --- gating -------------------------------------------------------------------


async def test_page_post_denied_for_non_admin(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _Interaction(perms=_Perms())  # plain member
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert it.response.ephemeral is True
    assert "Officer" in it.response.content
    assert await db.get_sticky_page("rules_info") is None  # nothing registered


# --- the post -> move -> no-op flow ---------------------------------------------


async def test_page_post_first_time_creates(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    it = _admin()
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert it.response.deferred is True
    assert "Posted" in it.followup.content and "<#111>" in it.followup.content

    row = await db.get_sticky_page("rules_info")
    assert row["posted_channel_id"] == "111"
    assert len(bot.channels[111].messages) == len(row["message_ids"]) == 3


async def test_page_post_same_channel_is_a_noop(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))
    before = (await db.get_sticky_page("rules_info"))["message_ids"]

    again = _admin()
    await PagesCog.post.callback(cog, again, _page_choice(), _Channel(111))
    assert "Nothing to do" in again.followup.content
    assert (await db.get_sticky_page("rules_info"))["message_ids"] == before


async def test_page_post_new_channel_moves_and_cleans_up(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))

    move = _admin()
    await PagesCog.post.callback(cog, move, _page_choice(), _Channel(222))
    assert "Moved" in move.followup.content and "<#222>" in move.followup.content
    assert bot.channels[111].messages == {}  # old copy deleted
    assert len(bot.channels[222].messages) == 3
    assert (await db.get_sticky_page("rules_info"))["posted_channel_id"] == "222"


async def test_page_post_same_channel_refreshes_stale_copy(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))
    ids = (await db.get_sticky_page("rules_info"))["message_ids"]

    await db.set_content_doc("page_rules_info", {"posts": ["# new", "copy", "here"]})
    it = _admin()
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert "refreshed the copy in place" in it.followup.content
    assert (await db.get_sticky_page("rules_info"))["message_ids"] == ids  # no re-post
    assert bot.channels[111].messages[int(ids[0])].content == "# new"


async def test_page_post_blocked_by_guardrail_registers_placement(db):
    bot = _PageBot(db, Config(admin_role_name="Officer"))  # POSTING_MODE=off
    cog = PagesCog(bot)
    it = _admin()
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert "Guardrail held" in it.followup.content
    row = await db.get_sticky_page("rules_info")
    assert row["channel_id"] == "111" and row["message_ids"] == []


async def test_page_post_without_copy_says_so(db):
    await db.set_content_doc("page_rules_info", {"posts": []})
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _admin()
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert "no copy" in it.followup.content


async def test_page_post_test_mode_lands_in_sandbox(db):
    cfg = Config(
        admin_role_name="Officer", posting_mode="test", test_channel_id="999"
    )
    bot = _PageBot(db, cfg)
    cog = PagesCog(bot)
    it = _admin()
    await PagesCog.post.callback(cog, it, _page_choice(), _Channel(111))
    assert "<#999>" in it.followup.content and "sandboxed" in it.followup.content
    assert len(bot.channels[999].messages) == 3
    assert 111 not in bot.channels


# --- list / refresh / remove ------------------------------------------------------


async def test_page_list_shows_unplaced_then_placed(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    empty = _admin()
    await PagesCog.list_pages.callback(cog, empty)
    assert "not placed yet" in empty.response.content

    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))
    placed = _admin()
    await PagesCog.list_pages.callback(cog, placed)
    assert "<#111>" in placed.response.content
    assert "3 message(s)" in placed.response.content


async def test_page_refresh_requires_placement(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _admin()
    await PagesCog.refresh.callback(cog, it, _page_choice())
    assert "isn't placed" in it.response.content


async def test_page_refresh_applies_dashboard_edit(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))

    current = _admin()
    await PagesCog.refresh.callback(cog, current, _page_choice())
    assert "already current" in current.followup.content

    await db.set_content_doc("page_rules_info", {"posts": ["a", "b", "c"]})
    edited = _admin()
    await PagesCog.refresh.callback(cog, edited, _page_choice())
    assert "edited" in edited.followup.content


async def test_page_remove_deletes_messages_and_placement(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))

    it = _admin()
    await PagesCog.remove.callback(cog, it, _page_choice())
    assert "deleted 3 message(s)" in it.followup.content
    assert bot.channels[111].messages == {}
    assert await db.get_sticky_page("rules_info") is None


async def test_page_remove_without_placement(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _admin()
    await PagesCog.remove.callback(cog, it, _page_choice())
    assert "isn't placed" in it.response.content
