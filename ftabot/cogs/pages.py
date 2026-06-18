"""Officer commands to place Calcifer's sticky pages (Rules & Info etc.).

`/page post` is deliberately simple: point a page at a channel. The first run
posts it there; pointing it at a different channel deletes the old copy and
re-posts in the new one; pointing it at the same channel does nothing (or
quietly refreshes the copy in place if a dashboard edit landed since). The
page's *text* is not edited here — that lives in the web dashboard's Content
section — this cog only decides where the page sits.

Destinations still resolve through the posting guardrail: a page passes
``promoted=True`` (the channel is the officer's explicit choice), so ``off``
posts nothing, ``test`` sandboxes the page into the test channel, and ``live``
sends it where it was pointed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..boards import BotBoardMessenger
from ..pages import PAGES, refresh_page, remove_page
from ..posting import PostGate, PostingMode
from ..runtime_settings import effective_post_gate
from ._guards import require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

_PAGE_CHOICES = [
    app_commands.Choice(name=page.title, value=page.name) for page in PAGES.values()
]


class PagesCog(commands.Cog):
    page = app_commands.Group(
        name="page", description="Place Calcifer's sticky pages (officers)."
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_admin(
            interaction, self.bot.config.admin_role_name, action="manage sticky pages"
        )

    def _blocked_reason(self, gate: PostGate) -> str:
        return (
            "posting is **off** (`POSTING_MODE=off`)"
            if gate.mode is PostingMode.OFF
            else "no test channel is confirmed (`TEST_CHANNEL_ID`)"
        )

    @page.command(
        name="post",
        description="Post a sticky page to a channel (moves it if it lives elsewhere).",
    )
    @app_commands.describe(
        page_name="Which page to place", channel="The channel it should live in"
    )
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def post(
        self,
        interaction: discord.Interaction,
        page_name: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        if page is None:
            await interaction.response.send_message("Unknown page.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        row = await self.bot.db.get_sticky_page(page.name)
        if row is None or row["channel_id"] != str(channel.id):
            await self.bot.db.upsert_sticky_page(page.name, str(channel.id))
            row = await self.bot.db.get_sticky_page(page.name)

        gate = await effective_post_gate(self.bot.db, self.bot.config)
        action = await refresh_page(
            self.bot.db, self.bot.config, page, row, BotBoardMessenger(self.bot), gate
        )

        if action == "blocked":
            await interaction.followup.send(
                f"Guardrail held: nothing posted because {self._blocked_reason(gate)}. "
                f"**{page.title}** is registered for {channel.mention} and will post "
                "once the guardrail opens.",
                ephemeral=True,
            )
            return
        if action == "skip":
            await interaction.followup.send(
                f"**{page.title}** has no copy to post yet. Add it in the dashboard's "
                "Content section first.",
                ephemeral=True,
            )
            return

        destination = gate.resolve(str(channel.id), promoted=True)
        where = f"<#{destination}>" if destination else "nowhere"
        sandboxed = (
            " (sandboxed by `POSTING_MODE=test`)" if destination != str(channel.id) else ""
        )
        replies = {
            "unchanged": f"**{page.title}** is already posted in {where} and the copy "
            "is current. Nothing to do.",
            "edited": f"**{page.title}** was already in {where}; refreshed the copy "
            "in place.",
            "created": f"Posted **{page.title}** in {where}{sandboxed}.",
            "moved": f"Moved **{page.title}** to {where}{sandboxed}; the old copy "
            "was removed.",
            "reposted": f"Re-posted **{page.title}** in {where}{sandboxed} (the old "
            "copy was stale or partly gone).",
        }
        await interaction.followup.send(
            replies.get(action, f"**{page.title}**: {action}."), ephemeral=True
        )

    @page.command(name="list", description="List the sticky pages and where they live.")
    async def list_pages(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        lines = []
        for page in PAGES.values():
            row = await self.bot.db.get_sticky_page(page.name)
            if row is None:
                lines.append(f"**{page.title}** · not placed yet (`/page post`)")
                continue
            posted = (
                f"posted ({len(row['message_ids'])} message(s)) in <#{row['posted_channel_id']}>"
                if row["message_ids"]
                else "not posted yet"
            )
            lines.append(f"**{page.title}** -> <#{row['channel_id']}> · {posted}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @page.command(
        name="refresh",
        description="Re-render a page now (e.g. after a dashboard copy edit).",
    )
    @app_commands.describe(page_name="Which page to refresh")
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def refresh(
        self, interaction: discord.Interaction, page_name: app_commands.Choice[str]
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        row = await self.bot.db.get_sticky_page(page.name) if page else None
        if page is None or row is None:
            await interaction.response.send_message(
                "That page isn't placed anywhere yet. Use `/page post` first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        gate = await effective_post_gate(self.bot.db, self.bot.config)
        action = await refresh_page(
            self.bot.db, self.bot.config, page, row, BotBoardMessenger(self.bot), gate
        )
        if action == "blocked":
            message = f"Guardrail held: {self._blocked_reason(gate)}."
        elif action == "unchanged":
            message = f"**{page.title}** is already current."
        else:
            message = f"**{page.title}**: {action}."
        await interaction.followup.send(message, ephemeral=True)

    @page.command(
        name="remove",
        description="Delete a page's posted messages and forget its placement.",
    )
    @app_commands.describe(page_name="Which page to remove")
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def remove(
        self, interaction: discord.Interaction, page_name: app_commands.Choice[str]
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        row = await self.bot.db.get_sticky_page(page.name) if page else None
        if page is None or row is None:
            await interaction.response.send_message(
                "That page isn't placed anywhere.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await remove_page(self.bot.db, row, BotBoardMessenger(self.bot))
        await interaction.followup.send(
            f"Removed **{page.title}**: deleted {deleted} message(s) and forgot "
            "its placement.",
            ephemeral=True,
        )
