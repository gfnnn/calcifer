"""Tests for the Discord bot entry point (ftabot/bot.py), previously 0%.

The only network touchpoint in ``setup_hook`` / ``on_ready`` is the Discord
gateway (``tree.sync`` and ``change_presence``); we stub those so the rest —
db wiring, cog loading, the commands-disabled branch, guild-vs-global sync, and
the ``main`` entry guard — runs fully offline against the real temp-file db.
"""

from __future__ import annotations

import pytest

from ftabot import bot as bot_mod
from ftabot.bot import FtaBot
from ftabot.config import Config


def _cfg(tmp_path, **over):
    base = dict(
        guild_name="From the Ashes",
        guild_realm="hellfire",
        guild_region="eu",
        database_path=str(tmp_path / "bot.db"),
    )
    base.update(over)
    return Config(**base)


def _stub_gateway(bot, monkeypatch):
    """Replace the two gateway calls so setup_hook/on_ready stay offline.
    Returns the list that records each tree.sync(guild=...) call."""
    synced: list = []

    async def _sync(*, guild=None):
        synced.append(guild)

    monkeypatch.setattr(bot.tree, "sync", _sync)
    return synced


# --- construction -----------------------------------------------------------


async def test_init_wires_config_and_db(tmp_path):
    cfg = _cfg(tmp_path)
    bot = FtaBot(cfg)
    assert bot.config is cfg
    assert bot.db is not None
    assert bot.command_prefix == "!fta "


# --- setup_hook: commands enabled -------------------------------------------


async def test_setup_hook_loads_all_cogs_and_syncs_globally(tmp_path, monkeypatch):
    bot = FtaBot(_cfg(tmp_path, bot_commands_enabled=True, discord_guild_id=None))
    synced = _stub_gateway(bot, monkeypatch)
    try:
        await bot.setup_hook()
        # All eight cogs are registered.
        assert len(bot.cogs) == 8
        assert {"MembersCog", "AdminCog", "ReportsCog", "RecruitCog",
                "FeedsCog", "BoardsCog", "ScoresCog", "AlertsCog"} <= set(bot.cogs)
        assert synced == [None]  # one global sync
    finally:
        await bot.close()


async def test_setup_hook_syncs_to_guild_when_configured(tmp_path, monkeypatch):
    bot = FtaBot(_cfg(tmp_path, bot_commands_enabled=True, discord_guild_id=4242))
    synced = _stub_gateway(bot, monkeypatch)
    try:
        await bot.setup_hook()
        assert len(bot.cogs) == 8
        # Synced to the guild object (instant), not globally.
        assert len(synced) == 1 and getattr(synced[0], "id", None) == 4242
    finally:
        await bot.close()


# --- setup_hook: commands disabled ------------------------------------------


async def test_setup_hook_disabled_clears_commands_globally(tmp_path, monkeypatch):
    bot = FtaBot(_cfg(tmp_path, bot_commands_enabled=False, discord_guild_id=None))
    synced = _stub_gateway(bot, monkeypatch)
    try:
        await bot.setup_hook()
        assert len(bot.cogs) == 0  # no command cogs loaded
        assert synced == [None]  # cleared + synced an empty global tree
    finally:
        await bot.close()


async def test_setup_hook_disabled_clears_guild_and_global(tmp_path, monkeypatch):
    bot = FtaBot(_cfg(tmp_path, bot_commands_enabled=False, discord_guild_id=99))
    synced = _stub_gateway(bot, monkeypatch)
    try:
        await bot.setup_hook()
        assert len(bot.cogs) == 0
        # Clears both the guild tree and the global tree.
        assert len(synced) == 2
        assert getattr(synced[0], "id", None) == 99
        assert synced[1] is None
    finally:
        await bot.close()


# --- on_ready / close -------------------------------------------------------


async def test_on_ready_sets_presence(tmp_path, monkeypatch):
    bot = FtaBot(_cfg(tmp_path))
    await bot.db.connect()
    presence = {}

    async def _change_presence(*, activity=None):
        presence["activity"] = activity

    monkeypatch.setattr(bot, "change_presence", _change_presence)
    # user is a read-only property on the real client; on_ready only logs it.
    monkeypatch.setattr(type(bot), "user", property(lambda self: "Calcifer#0001"))
    try:
        await bot.on_ready()
        assert presence["activity"].name == "the hearth of From the Ashes"
    finally:
        await bot.close()


async def test_close_closes_the_database(tmp_path):
    bot = FtaBot(_cfg(tmp_path))
    await bot.db.connect()
    await bot.close()
    # The db connection is released.
    assert bot.db._conn is None


# --- main entry point -------------------------------------------------------


def test_main_exits_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bot_mod.Config, "from_env",
        classmethod(lambda cls: _cfg(tmp_path, discord_token=None)),
    )
    with pytest.raises(SystemExit):
        bot_mod.main()


def test_main_runs_bot_with_token(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bot_mod.Config, "from_env",
        classmethod(lambda cls: _cfg(tmp_path, discord_token="s3cret")),
    )
    ran = {}
    # Stub the blocking run() so main() doesn't touch the network.
    monkeypatch.setattr(
        bot_mod.FtaBot, "run",
        lambda self, token, **kw: ran.update(token=token),
    )
    bot_mod.main()
    assert ran["token"] == "s3cret"
