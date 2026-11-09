"""Officer commands for the posting guardrail: the live off/test/live switch.

``POSTING_MODE`` / ``TEST_CHANNEL_ID`` / ``FEED_DEFAULT_CHANNEL_ID`` in the
environment are the boot defaults. These commands save an override into the
``settings`` table (see ``runtime_settings.py``); the board/feed loops and the
``/board`` + ``/page`` cogs resolve the gate from it on every post, so a change
here applies immediately with no redeploy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..runtime_settings import normalise, settings_overview
from ._guards import require_gm_or_discord_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

_ENV_DEFAULT = "default"

_MODE_CHOICES = [
    app_commands.Choice(name="off: post nothing anywhere", value="off"),
    app_commands.Choice(name="test: everything goes to the test channel", value="test"),
    app_commands.Choice(name="live: promoted surfaces reach their real channel", value="live"),
    app_commands.Choice(name="default: back to the environment setting", value=_ENV_DEFAULT),
]


def _show(spec: dict, value: object) -> str:
    if value in (None, ""):
        return "*not set*"
    return f"<#{value}>" if spec["kind"] == "channel" else f"`{value}`"


class PostingCog(commands.Cog):
    posting = app_commands.Group(
        name="posting", description="Control where (and whether) Calcifer posts."
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_gm_or_discord_admin(
            interaction, self.bot.config.gm_discord_id, action="change posting settings"
        )

    async def _effective(self, key: str) -> object:
        overview = await settings_overview(self.bot.db, self.bot.config)
        return next(s["effective"] for s in overview if s["key"] == key)

    async def _set_channel(
        self,
        interaction: discord.Interaction,
        key: str,
        label: str,
        channel: discord.TextChannel | None,
    ) -> None:
        if not await self._guard(interaction):
            return
        if channel is None:
            await self.bot.db.delete_setting(key)
            env = await self._effective(key)
            now = f"<#{env}>" if env else "*not set*"
            message = f"{label} override cleared; back to the environment default ({now})."
        else:
            await self.bot.db.set_setting(key, normalise(key, channel.id))
            message = f"{label} is now {channel.mention}."
        await interaction.response.send_message(message, ephemeral=True)

    @posting.command(name="status", description="Show the posting mode and channels in effect.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        lines = []
        for spec in await settings_overview(self.bot.db, self.bot.config):
            source = "set here" if spec["override"] is not None else "environment default"
            lines.append(
                f"**{spec['label']}**: {_show(spec, spec['effective'])} · {source}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @posting.command(name="mode", description="Switch the posting guardrail: off, test or live.")
    @app_commands.describe(mode="The stage to switch to")
    @app_commands.choices(mode=_MODE_CHOICES)
    async def mode(
        self, interaction: discord.Interaction, mode: app_commands.Choice[str]
    ) -> None:
        if not await self._guard(interaction):
            return
        if mode.value == _ENV_DEFAULT:
            await self.bot.db.delete_setting("posting_mode")
        else:
            await self.bot.db.set_setting(
                "posting_mode", normalise("posting_mode", mode.value)
            )
        effective = await self._effective("posting_mode")
        message = f"Posting mode is now `{effective}`."
        if effective != "off" and not await self._effective("test_channel_id"):
            message += (
                "\nNo test channel is set, so sandboxed posts are held. "
                "Set one with `/posting test-channel`."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @posting.command(
        name="test-channel",
        description="Set the sandbox channel for test and un-promoted posts.",
    )
    @app_commands.describe(channel="The test channel (leave empty to use the environment default)")
    async def test_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        await self._set_channel(interaction, "test_channel_id", "Test channel", channel)

    @posting.command(
        name="feed-channel",
        description="Set the default channel for feed alerts with no routing rule.",
    )
    @app_commands.describe(channel="The channel (leave empty to use the environment default)")
    async def feed_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        await self._set_channel(
            interaction, "feed_default_channel_id", "Default feed channel", channel
        )
