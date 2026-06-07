"""Opt-in feed alert subscriptions.

Members use /alerts subscribe to receive per-mention pings when a feed source
fires.  Officers use /alerts subscribers to audit who opted in for what.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ._guards import _is_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# Friendly names for the sources members can subscribe to.
_SOURCE_NAMES: dict[str, str] = {
    "raiderio_mplus": "M+ runs",
    "wowhead": "WoW news",
    "raiderio_realm_progress": "Realm standings",
    "wcl_prog": "Raid progress",
    "wowaudit_raids": "Raid reminders",
    "raiderio_affixes": "Weekly affixes",
}
_SOURCE_CHOICES = [
    app_commands.Choice(name=label, value=key)
    for key, label in _SOURCE_NAMES.items()
]


class AlertsCog(commands.Cog):
    alerts = app_commands.Group(
        name="alerts",
        description="Manage your feed alert subscriptions (opt-in pings).",
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @alerts.command(
        name="subscribe",
        description="Opt in to pings whenever a feed source posts an alert.",
    )
    @app_commands.describe(source="Which alerts do you want to receive?")
    @app_commands.choices(source=_SOURCE_CHOICES)
    async def subscribe(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
    ) -> None:
        await self.bot.db.add_subscription(str(interaction.user.id), source.value)
        await interaction.response.send_message(
            f"Done! You'll be pinged whenever **{source.name}** posts an alert.",
            ephemeral=True,
        )

    @alerts.command(
        name="unsubscribe",
        description="Stop receiving pings from a feed source.",
    )
    @app_commands.describe(source="Which alerts do you want to stop receiving?")
    @app_commands.choices(source=_SOURCE_CHOICES)
    async def unsubscribe(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
    ) -> None:
        removed = await self.bot.db.remove_subscription(
            str(interaction.user.id), source.value
        )
        if removed:
            await interaction.response.send_message(
                f"Removed. No more pings for **{source.name}** alerts.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"You weren't subscribed to **{source.name}** alerts.", ephemeral=True
            )

    @alerts.command(
        name="list",
        description="See your active alert subscriptions.",
    )
    async def list_subs(self, interaction: discord.Interaction) -> None:
        sources = await self.bot.db.user_subscriptions(str(interaction.user.id))
        if not sources:
            await interaction.response.send_message(
                "You have no active subscriptions. Use `/alerts subscribe` to opt in.",
                ephemeral=True,
            )
            return
        lines = [f"· **{_SOURCE_NAMES.get(s, s)}**" for s in sources]
        await interaction.response.send_message(
            "Your active subscriptions:\n" + "\n".join(lines), ephemeral=True
        )

    @alerts.command(
        name="subscribers",
        description="List everyone subscribed to a source (officer only).",
    )
    @app_commands.describe(source="Which source to audit")
    @app_commands.choices(source=_SOURCE_CHOICES)
    async def subscribers(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
    ) -> None:
        if not _is_admin(interaction, self.bot.config.admin_role_name):
            await interaction.response.send_message(
                f"You need the **{self.bot.config.admin_role_name}** role or "
                "*Manage Server* permission to see subscriber lists.",
                ephemeral=True,
            )
            return
        user_ids = await self.bot.db.source_subscribers(source.value)
        if not user_ids:
            await interaction.response.send_message(
                f"Nobody is subscribed to **{source.name}** yet.", ephemeral=True
            )
            return
        mentions = " ".join(f"<@{uid}>" for uid in user_ids)
        await interaction.response.send_message(
            f"**{source.name}** subscribers ({len(user_ids)}): {mentions}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
