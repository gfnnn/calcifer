"""The /page cog: officer gating + the post/move/no-op placement semantics.

Reuses the lightweight interaction fakes from ``test_cogs`` and adds a fake
bot with real-enough channels (send/fetch/edit/delete) so the cog's
``BotBoardMessenger`` path runs end to end, fully offline.
"""

from __future__ import annotations

import json

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


async def test_page_list_denied_for_non_admin(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _Interaction(perms=_Perms())
    await PagesCog.list_pages.callback(cog, it)
    assert "Officer" in it.response.content


async def test_page_refresh_denied_for_non_admin(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _Interaction(perms=_Perms())
    await PagesCog.refresh.callback(cog, it, _page_choice())
    assert "Officer" in it.response.content


async def test_page_remove_denied_for_non_admin(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _Interaction(perms=_Perms())
    await PagesCog.remove.callback(cog, it, _page_choice())
    assert "Officer" in it.response.content


async def test_page_post_unknown_page_is_rejected(db):
    # A choice value that isn't a registered page (shouldn't happen via the UI,
    # but the cog guards it) is turned away before any placement/defer.
    cog = PagesCog(_PageBot(db, _live_cfg()))
    ghost = app_commands.Choice(name="Ghost", value="ghost")
    it = _admin()
    await PagesCog.post.callback(cog, it, ghost, _Channel(111))
    assert "Unknown page" in it.response.content
    assert await db.get_sticky_page("ghost") is None


async def test_page_refresh_reports_guardrail_block(db):
    # Page is placed, but posting is off -> refresh holds at the guardrail.
    bot = _PageBot(db, Config(admin_role_name="Officer"))  # POSTING_MODE=off
    cog = PagesCog(bot)
    await db.upsert_sticky_page("rules_info", "111")  # registered, nothing posted
    it = _admin()
    await PagesCog.refresh.callback(cog, it, _page_choice())
    assert "Guardrail held" in it.followup.content


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


async def test_page_refresh_applies_copy_change(db):
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


# --- copy: export / import / reset ----------------------------------------------


class _Attachment:
    """Stands in for discord.Attachment: a size and a read()."""

    def __init__(self, payload, *, size=None):
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.size = len(self._data) if size is None else size

    async def read(self):
        return self._data


async def test_page_export_sends_the_bundled_copy_as_json(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _admin()
    await PagesCog.export.callback(cog, it, _page_choice())
    assert it.response.ephemeral is True and "bundled copy" in it.response.content
    sent = it.response.kwargs["file"]
    assert sent.filename == "page_rules_info.json"
    assert json.loads(sent.fp.read())["posts"]  # round-trips as a page document


async def test_page_import_saves_override_and_updates_the_posted_page(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))

    it = _admin()
    await PagesCog.import_copy.callback(
        cog, it, _page_choice(), _Attachment({"posts": ["new one", "new two"]})
    )
    assert "Imported 2 message(s)" in it.followup.content
    assert "was updated" in it.followup.content
    assert [m.content for m in bot.channels[111].messages.values()] == ["new one", "new two"]

    # The override is now what export hands back, and /page list flags it.
    exported = _admin()
    await PagesCog.export.callback(cog, exported, _page_choice())
    assert "imported copy" in exported.response.content
    assert json.loads(exported.response.kwargs["file"].fp.read())["posts"] == [
        "new one", "new two",
    ]
    listed = _admin()
    await PagesCog.list_pages.callback(cog, listed)
    assert "imported copy" in listed.response.content


async def test_page_import_before_placement_just_saves(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    it = _admin()
    await PagesCog.import_copy.callback(cog, it, _page_choice(), _Attachment({"posts": ["x"]}))
    assert "isn't placed yet" in it.followup.content
    assert (await db.get_content_doc("page_rules_info"))["posts"] == ["x"]


async def test_page_import_reports_a_guardrail_hold(db):
    bot = _PageBot(db, Config(admin_role_name="Officer"))  # posting off
    cog = PagesCog(bot)
    await db.upsert_sticky_page("rules_info", "111")
    it = _admin()
    await PagesCog.import_copy.callback(cog, it, _page_choice(), _Attachment({"posts": ["x"]}))
    assert "Not posted yet" in it.followup.content


async def test_page_import_rejects_bad_files_without_changing_anything(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    cases = [
        (_Attachment(b"{not json"), "not valid JSON"),
        (_Attachment(b"\xff\xfe"), "not valid JSON"),
        (_Attachment({"posts": []}), "non-empty"),
        (_Attachment(["a"]), "non-empty"),
        (_Attachment({"posts": ["ok", "  "]}), "Message 2 is empty"),
        (_Attachment({"posts": ["x" * 2001]}), "2001 characters"),
        (_Attachment({"posts": ["x"] * 11}), "At most 10"),
    ]
    for attachment, expected in cases:
        it = _admin()
        await PagesCog.import_copy.callback(cog, it, _page_choice(), attachment)
        assert "Nothing changed" in it.followup.content
        assert expected in it.followup.content
    oversized = _admin()
    await PagesCog.import_copy.callback(
        cog, oversized, _page_choice(), _Attachment({"posts": ["x"]}, size=10**7)
    )
    assert "too large" in oversized.response.content
    assert await db.get_content_doc("page_rules_info") is None


async def test_page_reset_restores_the_bundled_copy(db):
    bot = _PageBot(db, _live_cfg())
    cog = PagesCog(bot)
    await PagesCog.post.callback(cog, _admin(), _page_choice(), _Channel(111))
    bundled = [m.content for m in bot.channels[111].messages.values()]
    await PagesCog.import_copy.callback(
        cog, _admin(), _page_choice(), _Attachment({"posts": ["a", "b", "c"]})
    )

    it = _admin()
    await PagesCog.reset.callback(cog, it, _page_choice())
    assert "back on the bundled copy" in it.followup.content
    assert [m.content for m in bot.channels[111].messages.values()] == bundled
    assert await db.get_content_doc("page_rules_info") is None

    again = _admin()
    await PagesCog.reset.callback(cog, again, _page_choice())
    assert "already using the bundled copy" in again.followup.content


async def test_page_copy_commands_are_gated_and_reject_unknown_pages(db):
    cog = PagesCog(_PageBot(db, _live_cfg()))
    ghost = app_commands.Choice(name="Ghost", value="ghost")
    file = _Attachment({"posts": ["x"]})
    for invoke in (
        lambda it, page: PagesCog.export.callback(cog, it, page),
        lambda it, page: PagesCog.import_copy.callback(cog, it, page, file),
        lambda it, page: PagesCog.reset.callback(cog, it, page),
    ):
        denied = _Interaction(perms=_Perms())
        await invoke(denied, _page_choice())
        assert "Officer" in denied.response.content
        unknown = _admin()
        await invoke(unknown, ghost)
        assert "Unknown page" in unknown.response.content
    assert await db.get_content_doc("page_rules_info") is None
