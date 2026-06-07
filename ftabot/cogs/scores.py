"""Self-serve Mythic+ score refresh for Flamekeepers and above.

Lets a member pull their own character(s)' latest M+ score on demand and have
the boards reflect it at once, instead of waiting for the recurring pass. Uses
the character<->Discord link (``discord_id``) so it refreshes every character
tied to the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..scores import refresh_boards, refresh_scores
from .admin import _is_admin

if TYPE_CHECKING:
    from ..bot import FtaBot


class ScoresCog(commands.Cog):
    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @app_commands.command(
        name="refreshme",
        description="Refresh your character(s)' Mythic+ score now and update the boards.",
    )
    async def refreshme(self, interaction: discord.Interaction) -> None:
        # "Flame rank or up": the officer/Flamekeeper gate (or Manage Server).
        if not _is_admin(interaction, self.bot.config.admin_role_name):
            await interaction.response.send_message(
                "Self-refresh is for Flamekeepers and above for now.", ephemeral=True
            )
            return

        characters = await self.bot.db.search_members(discord_id=str(interaction.user.id))
        if not characters:
            await interaction.response.send_message(
                "No characters are linked to your Discord account yet, so there's "
                "nothing for me to refresh. An officer can link you with `/link`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        changed = await refresh_scores(self.bot.db, self.bot.config, characters)
        await refresh_boards(self.bot.db, self.bot.config, self.bot)

        if changed:
            lines = "\n".join(
                f"**{m.character_name}**: `{(m.mythic_plus_score or 0):.0f}`" for m in changed
            )
            await interaction.followup.send(
                f"Re-stoked the coals. New scores:\n{lines}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Refreshed {len(characters)} character(s); no score changes since last time.",
                ephemeral=True,
            )
