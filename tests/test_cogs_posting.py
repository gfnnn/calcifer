"""The /posting cog: the Discord-side switch for the posting guardrail.

These commands are the only way (short of an env change) to open or close the
gate every outbound message resolves through, so the tests check the override
actually reaches ``effective_post_gate`` and that only the GM / a Discord admin
can flip it.
"""

from __future__ import annotations

from discord import app_commands

from ftabot.cogs.posting import PostingCog
from ftabot.config import Config
from ftabot.posting import PostingMode
from ftabot.runtime_settings import effective_feed_channel, effective_post_gate

from .test_cogs import _Bot, _Channel, _Interaction, _Perms


def _mode(value):
    return app_commands.Choice(name=value, value=value)


def _admin():
    return _Interaction(perms=_Perms(manage_guild=True))


def _cog(db, **cfg):
    return PostingCog(_Bot(db, Config(**cfg)))


async def test_status_shows_environment_defaults_then_overrides(db):
    cog = _cog(db, posting_mode="test", test_channel_id="555")
    it = _admin()
    await PostingCog.status.callback(cog, it)
    assert it.response.ephemeral is True
    assert "`test`" in it.response.content and "<#555>" in it.response.content
    assert "*not set*" in it.response.content  # no default feed channel
    assert "set here" not in it.response.content

    await PostingCog.mode.callback(cog, _admin(), _mode("live"))
    after = _admin()
    await PostingCog.status.callback(cog, after)
    assert "`live`" in after.response.content and "set here" in after.response.content


async def test_mode_override_drives_the_live_gate_and_default_reverts(db):
    cog = _cog(db, posting_mode="off", test_channel_id="555")
    config = cog.bot.config

    it = _admin()
    await PostingCog.mode.callback(cog, it, _mode("live"))
    assert "`live`" in it.response.content
    gate = await effective_post_gate(db, config)
    assert gate.mode is PostingMode.LIVE
    assert gate.resolve("999", promoted=True) == "999"

    back = _admin()
    await PostingCog.mode.callback(cog, back, _mode("default"))
    assert "`off`" in back.response.content
    assert (await effective_post_gate(db, config)).mode is PostingMode.OFF


async def test_mode_warns_when_no_test_channel_is_set(db):
    cog = _cog(db)  # no TEST_CHANNEL_ID
    it = _admin()
    await PostingCog.mode.callback(cog, it, _mode("test"))
    assert "No test channel is set" in it.response.content

    quiet = _admin()
    await PostingCog.mode.callback(cog, quiet, _mode("off"))
    assert "No test channel" not in quiet.response.content


async def test_test_channel_set_and_clear(db):
    cog = _cog(db, posting_mode="test", test_channel_id="555")
    config = cog.bot.config

    it = _admin()
    await PostingCog.test_channel.callback(cog, it, _Channel(777))
    assert "<#777>" in it.response.content
    assert (await effective_post_gate(db, config)).resolve("1") == "777"

    cleared = _admin()
    await PostingCog.test_channel.callback(cog, cleared, None)
    assert "<#555>" in cleared.response.content  # back to the env default
    assert (await effective_post_gate(db, config)).resolve("1") == "555"


async def test_feed_channel_set_and_clear(db):
    cog = _cog(db)
    config = cog.bot.config

    await PostingCog.feed_channel.callback(cog, _admin(), _Channel(888))
    assert await effective_feed_channel(db, config) == "888"

    cleared = _admin()
    await PostingCog.feed_channel.callback(cog, cleared, None)
    assert "*not set*" in cleared.response.content
    assert await effective_feed_channel(db, config) is None


async def test_posting_commands_are_gm_or_discord_admin_only(db):
    cog = _cog(db, admin_role_name="Officer")
    for invoke in (
        lambda it: PostingCog.status.callback(cog, it),
        lambda it: PostingCog.mode.callback(cog, it, _mode("live")),
        lambda it: PostingCog.test_channel.callback(cog, it, _Channel(1)),
        lambda it: PostingCog.feed_channel.callback(cog, it, _Channel(1)),
    ):
        it = _Interaction(perms=_Perms())
        await invoke(it)
        assert it.response.ephemeral is True
        assert "Guild Master" in it.response.content
    assert await db.get_settings() == {}  # nothing was written
