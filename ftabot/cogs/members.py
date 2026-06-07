"""Read-only member lookup commands available to everyone."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..models import CLASSES, DEFAULT_EMBED_COLOR, ROLES
from ..ui import me_card, member_embed, roster_embed
from .admin import _is_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

_SORT_CHOICES = [
    app_commands.Choice(name="Mythic+ score", value="mythic_plus_score"),
    app_commands.Choice(name="Item level", value="item_level"),
    app_commands.Choice(name="Best parse avg", value="best_parse_avg"),
    app_commands.Choice(name="Guild rank", value="guild_rank"),
    app_commands.Choice(name="Name", value="name"),
]


class MembersCog(commands.Cog):
    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.config

    @app_commands.command(name="member", description="Show a guild member's profile.")
    @app_commands.describe(
        name="Character name",
        realm="Realm slug (defaults to the guild realm)",
    )
    async def member(
        self,
        interaction: discord.Interaction,
        name: str,
        realm: str | None = None,
    ) -> None:
        realm = realm or self.cfg.guild_realm
        m = await self.bot.db.get_member(name, realm, self.cfg.guild_region)
        if m is None:
            await interaction.response.send_message(
                f"No member named **{name}** on **{realm}**. "
                "Try `/roster` to see who's tracked, or ask an officer to `/sync`.",
                ephemeral=True,
            )
            return
        officer = _is_admin(interaction, self.cfg.admin_role_name)
        await interaction.response.send_message(
            embed=member_embed(m, include_private=officer)
        )

    @app_commands.command(name="roster", description="List the guild roster.")
    @app_commands.describe(sort="How to order the list")
    @app_commands.choices(sort=_SORT_CHOICES)
    async def roster(
        self,
        interaction: discord.Interaction,
        sort: app_commands.Choice[str] | None = None,
    ) -> None:
        order = sort.value if sort else "mythic_plus_score"
        members = await self.bot.db.all_members(order_by=order)
        label = sort.name if sort else "Mythic+ score"
        embed = roster_embed(
            members,
            title=f"{self.cfg.guild_name} · roster (by {label})",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="search", description="Search members by class/role/name.")
    @app_commands.describe(
        wow_class="Filter by class",
        role="Filter by role",
        name="Filter by (partial) character name",
    )
    @app_commands.choices(
        wow_class=[app_commands.Choice(name=c, value=c) for c in CLASSES],
        role=[app_commands.Choice(name=r.title(), value=r) for r in ROLES],
    )
    async def search(
        self,
        interaction: discord.Interaction,
        wow_class: app_commands.Choice[str] | None = None,
        role: app_commands.Choice[str] | None = None,
        name: str | None = None,
    ) -> None:
        members = await self.bot.db.search_members(
            name=name,
            class_name=wow_class.value if wow_class else None,
            role=role.value if role else None,
        )
        filters = ", ".join(
            f for f in (
                wow_class.value if wow_class else None,
                role.name if role else None,
                f'"{name}"' if name else None,
            ) if f
        ) or "all"
        embed = roster_embed(
            members,
            title=f"Search results ({filters})",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="alts", description="Show a member's alts.")
    @app_commands.describe(name="Main character name")
    async def alts(
        self,
        interaction: discord.Interaction,
        name: str,
        realm: str | None = None,
    ) -> None:
        realm = realm or self.cfg.guild_realm
        main = await self.bot.db.get_member(name, realm, self.cfg.guild_region)
        if main is None or main.id is None:
            await interaction.response.send_message(
                f"No member named **{name}** on **{realm}**.", ephemeral=True
            )
            return
        alts = await self.bot.db.alts_of(main.id)
        embed = roster_embed(
            alts,
            title=f"Alts of {main.character_name}",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="me", description="Your linked characters at a glance.")
    async def me(self, interaction: discord.Interaction) -> None:
        members = await self.bot.db.search_members(discord_id=str(interaction.user.id))
        if not members:
            await interaction.response.send_message(
                "No characters are linked to you yet. Ask a Flamekeeper to link "
                "your mains and alts, and your hearth will light up here.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=me_card(members, display_name=interaction.user.display_name),
            ephemeral=True,
        )

    @app_commands.command(name="whois", description="Find characters linked to a Discord user.")
    @app_commands.describe(user="The Discord user to look up")
    async def whois(
        self, interaction: discord.Interaction, user: discord.User
    ) -> None:
        members = await self.bot.db.search_members(discord_id=str(user.id))
        if not members:
            await interaction.response.send_message(
                f"No characters are linked to {user.mention}.", ephemeral=True
            )
            return
        embed = roster_embed(
            members,
            title=f"Characters linked to {user.display_name}",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="Show guild roster statistics.")
    async def stats(self, interaction: discord.Interaction) -> None:
        members = await self.bot.db.all_members()
        total = len(members)
        if total == 0:
            await interaction.response.send_message(
                "No members tracked yet. An officer can run `/sync`.", ephemeral=True
            )
            return

        by_role: dict[str, int] = {}
        by_class: dict[str, int] = {}
        scores = [m.mythic_plus_score for m in members if m.mythic_plus_score]
        for m in members:
            if m.role:
                by_role[m.role] = by_role.get(m.role, 0) + 1
            if m.class_name:
                by_class[m.class_name] = by_class.get(m.class_name, 0) + 1

        embed = discord.Embed(
            title=f"{self.cfg.guild_name} · statistics",
            color=DEFAULT_EMBED_COLOR,
        )
        embed.add_field(name="Members tracked", value=str(total))
        if scores:
            embed.add_field(name="Avg M+ score", value=f"{sum(scores) / len(scores):.0f}")
            embed.add_field(name="Top M+ score", value=f"{max(scores):.0f}")
        if by_role:
            embed.add_field(
                name="Roles",
                value=" · ".join(f"{r}: {n}" for r, n in sorted(by_role.items())),
                inline=False,
            )
        if by_class:
            top = sorted(by_class.items(), key=lambda kv: (-kv[1], kv[0]))
            embed.add_field(
                name="Classes",
                value=" · ".join(f"{c}: {n}" for c, n in top),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot: "FtaBot") -> None:  # pragma: no cover - discord entrypoint
    await bot.add_cog(MembersCog(bot))
