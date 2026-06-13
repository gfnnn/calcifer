"""Officer commands to manage Calcifer's feed routing live (Phase 5).

A rule is ``match → channel → style``; these commands edit the ``feed_rules``
table the router reads, so routing changes take effect without a redeploy.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import publisher, router
from ..feeds.base import FeedEvent
from ._guards import require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

SOURCE_CHOICES = [
    app_commands.Choice(name="Wowhead news", value="wowhead"),
    app_commands.Choice(name="Raider.IO Mythic+", value="raiderio_mplus"),
    app_commands.Choice(name="Raid-Helper events", value="raidhelper"),
    app_commands.Choice(name="WoW Audit raid reminders", value="wowaudit_raids"),
    app_commands.Choice(name="Warcraft Logs live prog", value="wcl_prog"),
]

COLOR_CHOICES = [
    app_commands.Choice(name="Blue flame (info)", value="blue"),
    app_commands.Choice(name="Amber (warning)", value="amber"),
    app_commands.Choice(name="Ember red (critical)", value="red"),
    app_commands.Choice(name="Guild ember", value="ember"),
]

# A representative event per source, used by /feed test.
_SAMPLES: dict[str, tuple[str, str, str, list[str], str]] = {
    # source: (event_type, title, summary, categories, severity)
    "wowhead": ("wowhead.patch-notes", "Sample: Patch 11.2 Hotfixes Posted",
                "Class changes, new raid, and more.", ["News"], "info"),
    "raiderio_mplus": ("raiderio.mplus-run", "🔑 Sample: Emberlyn timed a +20 City ⭐⭐",
                       "A shiny test key.", ["Mythic+"], "info"),
    "raidhelper": ("raidhelper.created", "📅 Sample: New event: Heroic Night",
                   "🕑 Tonight · 18 signed up", ["Events"], "info"),
    "wowaudit_raids": ("wowaudit.raid-reminder", "🔥 Sample: Raid reminder: Manaforge Heroic",
                       "Starts soon · 20/22 signed up", ["Raid"], "info"),
    "wcl_prog": ("wcl.best-pull", "🔥 Sample: Progress on Final Boss (Mythic) · best pull 4.0%",
                 "So close I can feel the heat.", ["Progression"], "warning"),
}


def _sample_event(source: str) -> FeedEvent:
    event_type, title, summary, categories, severity = _SAMPLES.get(
        source, ("test", f"Sample: {source}", "A test alert.", [], "info")
    )
    return FeedEvent(
        source=source,
        event_type=event_type,
        external_id=f"sample-{source}-{random.randint(1, 1_000_000)}",
        title=title,
        summary=summary,
        categories=categories,
        severity=severity,
    )


class FeedsCog(commands.Cog):
    feed = app_commands.Group(
        name="feed", description="Manage Calcifer's feed routing (officers)."
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_admin(
            interaction, self.bot.config.admin_role_name, action="manage feeds"
        )

    @feed.command(name="route", description="Route a feed (optionally filtered) to a channel.")
    @app_commands.describe(
        source="Which feed",
        channel="Destination channel",
        event_type="Optional exact event type (e.g. wowhead.patch-notes)",
        contains="Optional comma-separated keywords the title/summary must contain",
        color="Optional embed colour",
        ping="Optional role to ping with the alert",
    )
    @app_commands.choices(source=SOURCE_CHOICES, color=COLOR_CHOICES)
    async def route(
        self,
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        channel: discord.TextChannel,
        event_type: str | None = None,
        contains: str | None = None,
        color: app_commands.Choice[str] | None = None,
        ping: discord.Role | None = None,
    ) -> None:
        if not await self._guard(interaction):
            return
        match: dict = {}
        if contains:
            match["contains"] = [k.strip() for k in contains.split(",") if k.strip()]
        style: dict = {"persona": True}
        if color:
            style["color"] = color.value
        if ping:
            style["ping"] = ping.mention
        rule_id = await self.bot.db.add_feed_rule(
            source.value,
            str(channel.id),
            event_type=event_type,
            match=match,
            style=style,
            updated_by=str(interaction.user.id),
        )
        bits = [f"✅ Rule **#{rule_id}**: `{source.value}`"]
        if event_type:
            bits.append(f"({event_type})")
        bits.append(f"→ {channel.mention}")
        if match.get("contains"):
            bits.append("· keywords: " + ", ".join(match["contains"]))
        if ping:
            bits.append(f"· ping {ping.mention}")
        await interaction.response.send_message(
            " ".join(bits) + ".",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @feed.command(name="list", description="Show active routing rules and muted feeds.")
    async def list_rules(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        rules = await self.bot.db.all_feed_rules()
        muted = await self.bot.db.muted_sources()
        lines: list[str] = []
        for r in rules:
            chan = f"<#{r['channel_id']}>"
            filt = []
            if r["event_type"]:
                filt.append(r["event_type"])
            if r["match"].get("contains"):
                filt.append("kw:" + "/".join(r["match"]["contains"]))
            suffix = f" [{', '.join(filt)}]" if filt else ""
            state = "" if r["enabled"] else " (disabled)"
            lines.append(f"`#{r['id']}` **{r['source']}**{suffix} → {chan}{state}")
        body = "\n".join(lines) or "_No rules yet. Everything goes to the default channel._"
        if muted:
            body += f"\n\n🔇 Muted: {', '.join(sorted(muted))}"
        default = self.bot.config.feed_default_channel_id
        if default:
            body += f"\n\nDefault channel: <#{default}>"
        embed = discord.Embed(
            title="Calcifer · feed routing", description=body, color=publisher.persona.BLUE_FLAME
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @feed.command(name="remove", description="Delete a routing rule by id.")
    @app_commands.describe(rule_id="The rule id from /feed list")
    async def remove(self, interaction: discord.Interaction, rule_id: int) -> None:
        if not await self._guard(interaction):
            return
        ok = await self.bot.db.delete_feed_rule(rule_id)
        await interaction.response.send_message(
            f"🗑️ Removed rule **#{rule_id}**." if ok else f"No rule **#{rule_id}**.",
            ephemeral=True,
        )

    @feed.command(name="disable", description="Pause a routing rule without deleting it.")
    @app_commands.describe(rule_id="The rule id from /feed list")
    async def disable(self, interaction: discord.Interaction, rule_id: int) -> None:
        if not await self._guard(interaction):
            return
        ok = await self.bot.db.set_feed_rule_enabled(rule_id, False)
        await interaction.response.send_message(
            f"⏸️ Disabled rule **#{rule_id}**. It stays in `/feed list` but won't route."
            if ok else f"No rule **#{rule_id}**.",
            ephemeral=True,
        )

    @feed.command(name="enable", description="Re-activate a disabled routing rule.")
    @app_commands.describe(rule_id="The rule id from /feed list")
    async def enable(self, interaction: discord.Interaction, rule_id: int) -> None:
        if not await self._guard(interaction):
            return
        ok = await self.bot.db.set_feed_rule_enabled(rule_id, True)
        await interaction.response.send_message(
            f"▶️ Enabled rule **#{rule_id}**." if ok else f"No rule **#{rule_id}**.",
            ephemeral=True,
        )

    @feed.command(name="mute", description="Temporarily silence a feed source.")
    @app_commands.choices(source=SOURCE_CHOICES)
    async def mute(self, interaction: discord.Interaction, source: app_commands.Choice[str]) -> None:
        if not await self._guard(interaction):
            return
        await self.bot.db.mute_source(source.value)
        await interaction.response.send_message(f"🔇 Muted **{source.value}**.", ephemeral=True)

    @feed.command(name="unmute", description="Un-silence a feed source.")
    @app_commands.choices(source=SOURCE_CHOICES)
    async def unmute(self, interaction: discord.Interaction, source: app_commands.Choice[str]) -> None:
        if not await self._guard(interaction):
            return
        ok = await self.bot.db.unmute_source(source.value)
        await interaction.response.send_message(
            f"🔔 Unmuted **{source.value}**." if ok else f"**{source.value}** wasn't muted.",
            ephemeral=True,
        )

    @feed.command(name="test", description="Post a sample alert to verify routing + styling.")
    @app_commands.choices(source=SOURCE_CHOICES)
    async def test(self, interaction: discord.Interaction, source: app_commands.Choice[str]) -> None:
        if not await self._guard(interaction):
            return
        event = _sample_event(source.value)
        decision = router.resolve(
            event,
            await self.bot.db.feed_rules(),
            default_channel_id=self.bot.config.feed_default_channel_id,
        )
        if decision is None:
            await interaction.response.send_message(
                "No matching rule and no default channel set, nowhere to post. "
                "Add a rule with `/feed route` or set `FEED_DEFAULT_CHANNEL_ID`.",
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(decision.channel_id)) if interaction.guild else None
        if channel is None:
            channel = await self.bot.fetch_channel(int(decision.channel_id))
        embed = publisher.render_embed(event, decision, tone=self.bot.config.persona_tone)
        content = decision.style.get("ping")
        await channel.send(content=content, embed=embed)
        await interaction.response.send_message(
            f"🔥 Posted a **{source.value}** sample to {channel.mention}.", ephemeral=True
        )


async def setup(bot: "FtaBot") -> None:  # pragma: no cover - discord entrypoint
    await bot.add_cog(FeedsCog(bot))
