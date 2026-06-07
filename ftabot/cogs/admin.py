"""Officer-only commands: roster sync, Discord links, mains/alts, notes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..sync import sync_guild
from ._guards import _is_admin, require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# _is_admin is re-exported here for the cogs/tests that import it from this cog.
__all__ = ["AdminCog", "_is_admin"]


class AdminCog(commands.Cog):
    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.config

    async def _ensure_admin(self, interaction: discord.Interaction) -> bool:
        return await require_admin(interaction, self.cfg.admin_role_name)

    @app_commands.command(name="sync", description="Refresh the roster from Raider.IO (+ Blizzard/WCL).")
    @app_commands.describe(prune="Remove characters no longer in the guild (default: yes)")
    async def sync(
        self, interaction: discord.Interaction, prune: bool = True
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        await interaction.response.defer(thinking=True)
        result = await sync_guild(self.bot.db, self.cfg, prune=prune)
        await interaction.followup.send(result.summary())

    @app_commands.command(name="link", description="Link a character to a Discord user.")
    @app_commands.describe(
        name="Character name",
        user="Discord user to link (omit to unlink)",
        realm="Realm slug (defaults to the guild realm)",
    )
    async def link(
        self,
        interaction: discord.Interaction,
        name: str,
        user: discord.User | None = None,
        realm: str | None = None,
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        realm = realm or self.cfg.guild_realm
        m = await self.bot.db.get_member(name, realm, self.cfg.guild_region)
        if m is None or m.id is None:
            await interaction.response.send_message(
                f"No member named **{name}** on **{realm}**.", ephemeral=True
            )
            return
        await self.bot.db.set_discord_link(m.id, str(user.id) if user else None)
        if user:
            await interaction.response.send_message(
                f"Linked **{m.character_name}** to {user.mention}."
            )
        else:
            await interaction.response.send_message(
                f"Unlinked **{m.character_name}**."
            )

    @app_commands.command(name="setmain", description="Mark a character as an alt of a main.")
    @app_commands.describe(
        alt="The alt character's name",
        main="The main character's name (omit to clear, making it a main)",
    )
    async def setmain(
        self,
        interaction: discord.Interaction,
        alt: str,
        main: str | None = None,
        realm: str | None = None,
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        realm = realm or self.cfg.guild_realm
        alt_m = await self.bot.db.get_member(alt, realm, self.cfg.guild_region)
        if alt_m is None or alt_m.id is None:
            await interaction.response.send_message(
                f"No member named **{alt}** on **{realm}**.", ephemeral=True
            )
            return

        if main is None:
            await self.bot.db.set_main_alt(
                alt_m.id, is_main=True, main_character_id=None
            )
            await interaction.response.send_message(
                f"**{alt_m.character_name}** is now flagged as a main."
            )
            return

        main_m = await self.bot.db.get_member(main, realm, self.cfg.guild_region)
        if main_m is None or main_m.id is None:
            await interaction.response.send_message(
                f"No main named **{main}** on **{realm}**.", ephemeral=True
            )
            return
        if main_m.id == alt_m.id:
            await interaction.response.send_message(
                "A character can't be its own main.", ephemeral=True
            )
            return
        await self.bot.db.set_main_alt(
            alt_m.id, is_main=False, main_character_id=main_m.id
        )
        await interaction.response.send_message(
            f"**{alt_m.character_name}** is now an alt of **{main_m.character_name}**."
        )

    @app_commands.command(name="note", description="Set or clear an officer note on a member.")
    @app_commands.describe(
        name="Character name",
        text="Note text (omit to clear the note)",
    )
    async def note(
        self,
        interaction: discord.Interaction,
        name: str,
        text: str | None = None,
        realm: str | None = None,
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        realm = realm or self.cfg.guild_realm
        m = await self.bot.db.get_member(name, realm, self.cfg.guild_region)
        if m is None or m.id is None:
            await interaction.response.send_message(
                f"No member named **{name}** on **{realm}**.", ephemeral=True
            )
            return
        await self.bot.db.set_note(m.id, text)
        verb = "set" if text else "cleared"
        await interaction.response.send_message(
            f"Note {verb} for **{m.character_name}**.", ephemeral=True
        )

    @app_commands.command(name="setteam", description="Assign a character to a raid team.")
    @app_commands.describe(
        name="Character name",
        team="Raid team name (omit to clear)",
    )
    async def setteam(
        self,
        interaction: discord.Interaction,
        name: str,
        team: str | None = None,
        realm: str | None = None,
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        m = await self._lookup(interaction, name, realm)
        if m is None:
            return
        await self.bot.db.set_raid_team(m.id, team)
        msg = (
            f"**{m.character_name}** assigned to team **{team}**."
            if team
            else f"**{m.character_name}** removed from their raid team."
        )
        await interaction.response.send_message(msg)

    @app_commands.command(name="setofficer", description="Set/clear a character's officer role.")
    @app_commands.describe(
        name="Character name",
        role="Officer responsibility, e.g. Recruitment, Loot (omit to clear)",
    )
    async def setofficer(
        self,
        interaction: discord.Interaction,
        name: str,
        role: str | None = None,
        realm: str | None = None,
    ) -> None:
        if not await self._ensure_admin(interaction):
            return
        m = await self._lookup(interaction, name, realm)
        if m is None:
            return
        await self.bot.db.set_officer_role(m.id, role)
        msg = (
            f"**{m.character_name}** is now officer for **{role}**."
            if role
            else f"Cleared officer role for **{m.character_name}**."
        )
        await interaction.response.send_message(msg)

    async def _lookup(self, interaction, name: str, realm: str | None):
        realm = realm or self.cfg.guild_realm
        m = await self.bot.db.get_member(name, realm, self.cfg.guild_region)
        if m is None or m.id is None:
            await interaction.response.send_message(
                f"No member named **{name}** on **{realm}**.", ephemeral=True
            )
            return None
        return m


async def setup(bot: "FtaBot") -> None:  # pragma: no cover - discord entrypoint
    await bot.add_cog(AdminCog(bot))
