"""Read-only member lookup commands available to everyone."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import brand, views
from ..models import DEFAULT_EMBED_COLOR
from ..ui import clip, me_card, member_embed, roster_embed
from ._guards import _is_admin, require_team_member

if TYPE_CHECKING:
    from ..bot import FtaBot


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
        # The in-game rank name (when GRM identity has been ingested) is shown in
        # place of the raw rank number.
        identity = await self.bot.db.get_identity(
            m.character_name, m.realm_slug, m.region
        )
        rank_name = identity.get("rank_name") if identity else None
        officer = _is_admin(interaction, self.cfg.admin_role_name)
        await interaction.response.send_message(
            embed=member_embed(m, include_private=officer, rank_name=rank_name)
        )

    @app_commands.command(
        name="roster", description="The team's sign-up roster for the next raid."
    )
    async def roster(self, interaction: discord.Interaction) -> None:
        data = await views.roster_board(self.bot.db, self.cfg)
        if not data["groups"]:
            await interaction.response.send_message(
                "No upcoming raid sign-ups yet. They appear once WoW Audit has "
                "the next raid scheduled and the roster has synced.",
                ephemeral=True,
            )
            return
        raid = data["raid"]
        title_bits = [b for b in (raid.get("instance"), raid.get("difficulty")) if b]
        title = "Team roster · " + (" ".join(title_bits) if title_bits else "next raid")
        embed = discord.Embed(title=clip(title, 256), color=DEFAULT_EMBED_COLOR)
        if raid.get("date"):
            when = raid["date"]
            if raid.get("time"):
                when += f" {raid['time']}"
            embed.description = f"Next raid: {when}"
        for bucket, rows in data["groups"].items():
            lines = []
            for r in rows:
                glyph = brand.icon((r.get("role") or "").lower())
                klass = r.get("class") or ""
                tail = f" · {klass}" if klass else ""
                lines.append(f"{glyph + ' ' if glyph else ''}**{r['name']}**{tail}")
            embed.add_field(
                name=f"{bucket} ({len(rows)})",
                value=clip("\n".join(lines), 1024),
                inline=False,
            )
        embed.set_footer(text=f"{data['count']} signed up.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="me", description="Your linked characters at a glance.")
    async def me(self, interaction: discord.Interaction) -> None:
        members = await self.bot.db.search_members(discord_id=str(interaction.user.id))
        team = [m for m in members if m.raid_team]
        if not team:
            await interaction.response.send_message(
                "No team characters are linked to you yet. `/me` is for the raid "
                "team roster, so ask a Flamekeeper to link your raiding mains.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=me_card(team, display_name=interaction.user.display_name),
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
                f"No characters are linked to {user.mention}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        embed = roster_embed(
            members,
            title=f"Characters linked to {user.display_name}",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="Show team roster statistics.")
    async def stats(self, interaction: discord.Interaction) -> None:
        if not await require_team_member(
            self.bot.db, interaction, action="see team stats"
        ):
            return
        # The gate guarantees the caller has a team character, so the roster is
        # never empty here.
        members = await self.bot.db.team_roster()
        total = len(members)

        by_role: dict[str, int] = {}
        by_class: dict[str, int] = {}
        scores = [m.mythic_plus_score for m in members if m.mythic_plus_score]
        for m in members:
            if m.role:
                by_role[m.role] = by_role.get(m.role, 0) + 1
            if m.class_name:
                by_class[m.class_name] = by_class.get(m.class_name, 0) + 1

        embed = discord.Embed(
            title=f"{self.cfg.guild_name} · team statistics",
            color=DEFAULT_EMBED_COLOR,
        )
        embed.add_field(name="Team members", value=str(total))
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
