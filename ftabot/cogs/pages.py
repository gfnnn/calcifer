"""Officer commands to place Calcifer's sticky pages (Rules & Info etc.).

`/page post` is deliberately simple: point a page at a channel. The first run
posts it there; pointing it at a different channel deletes the old copy and
re-posts in the new one; pointing it at the same channel does nothing (or
quietly refreshes the copy in place if it changed since).

The page's *text* lives in ``ftabot/content/page_<name>.json`` and normally
changes with a change request. For a fix that can't wait for a deploy,
``/page export`` hands the live copy back as a JSON file, ``/page import``
takes an edited one (validated, saved as an override) and ``/page reset`` drops
the override so the bundled file wins again.

Destinations still resolve through the posting guardrail: a page passes
``promoted=True`` (the channel is the officer's explicit choice), so ``off``
posts nothing, ``test`` sandboxes the page into the test channel, and ``live``
sends it where it was pointed.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..boards import BotBoardMessenger
from ..pages import (
    PAGES,
    PageCopyError,
    PageDef,
    load_page_doc,
    refresh_page,
    remove_page,
    validate_page_doc,
)
from ..posting import PostGate, PostingMode
from ..runtime_settings import effective_post_gate
from ._guards import require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# An imported page is a few KB of text; anything bigger is the wrong file.
_MAX_IMPORT_BYTES = 256 * 1024

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
            "posting is **off** (`/posting mode`)"
            if gate.mode is PostingMode.OFF
            else "no test channel is set (`/posting test-channel`)"
        )

    async def _refresh_if_placed(self, page: PageDef) -> str:
        """Re-render a placed page after its copy changed; a sentence for the reply."""
        row = await self.bot.db.get_sticky_page(page.name)
        if row is None:
            return "It isn't placed yet; `/page post` puts it in a channel."
        gate = await effective_post_gate(self.bot.db, self.bot.config)
        action = await refresh_page(
            self.bot.db, self.bot.config, page, row, BotBoardMessenger(self.bot), gate
        )
        if action == "blocked":
            return f"Not posted yet: {self._blocked_reason(gate)}."
        if action == "unchanged":
            return "The posted page already matched."
        return f"The posted page was updated ({action})."

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
                f"**{page.title}** has no copy to post yet. Add it to "
                f"`ftabot/content/{page.content_doc}.json` or `/page import` it.",
                ephemeral=True,
            )
            return

        destination = gate.resolve(str(channel.id), promoted=True)
        where = f"<#{destination}>" if destination else "nowhere"
        sandboxed = (
            " (sandboxed by the test posting mode)" if destination != str(channel.id) else ""
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
            override = await self.bot.db.get_content_doc(page.content_doc)
            copy = "imported copy" if override is not None else "bundled copy"
            if row is None:
                lines.append(f"**{page.title}** · not placed yet (`/page post`) · {copy}")
                continue
            posted = (
                f"posted ({len(row['message_ids'])} message(s)) in <#{row['posted_channel_id']}>"
                if row["message_ids"]
                else "not posted yet"
            )
            lines.append(
                f"**{page.title}** -> <#{row['channel_id']}> · {posted} · {copy}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @page.command(
        name="refresh",
        description="Re-render a page now.",
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

    # -- copy: export / import / reset ----------------------------------------

    @page.command(
        name="export",
        description="Download a page's current copy as a JSON file.",
    )
    @app_commands.describe(page_name="Which page to export")
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def export(
        self, interaction: discord.Interaction, page_name: app_commands.Choice[str]
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        if page is None:
            await interaction.response.send_message("Unknown page.", ephemeral=True)
            return
        override = await self.bot.db.get_content_doc(page.content_doc)
        doc = await load_page_doc(self.bot.db, page)
        body = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        source = (
            "an imported copy (it differs from the repo until you commit it)"
            if override is not None
            else "the bundled copy"
        )
        await interaction.response.send_message(
            f"**{page.title}** is using {source}. Edit this file and `/page import` "
            f"it, or commit it as `ftabot/content/{page.content_doc}.json`.",
            file=discord.File(
                io.BytesIO(body.encode("utf-8")), filename=f"{page.content_doc}.json"
            ),
            ephemeral=True,
        )

    @page.command(
        name="import",
        description="Replace a page's copy with an uploaded JSON file.",
    )
    @app_commands.describe(
        page_name="Which page to update", file="A JSON file from /page export"
    )
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def import_copy(
        self,
        interaction: discord.Interaction,
        page_name: app_commands.Choice[str],
        file: discord.Attachment,
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        if page is None:
            await interaction.response.send_message("Unknown page.", ephemeral=True)
            return
        if file.size > _MAX_IMPORT_BYTES:
            await interaction.response.send_message(
                "That file is too large to be a page.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            posts = validate_page_doc(json.loads((await file.read()).decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            reason = str(exc) if isinstance(exc, PageCopyError) else "it is not valid JSON."
            await interaction.followup.send(
                f"Nothing changed: {reason}", ephemeral=True
            )
            return
        await self.bot.db.set_content_doc(
            page.content_doc, {"title": page.title, "posts": posts}
        )
        await interaction.followup.send(
            f"Imported {len(posts)} message(s) for **{page.title}**. "
            f"{await self._refresh_if_placed(page)}\n"
            "This copy now overrides the repo file until `/page reset`.",
            ephemeral=True,
        )

    @page.command(
        name="reset",
        description="Drop an imported copy and go back to the bundled page.",
    )
    @app_commands.describe(page_name="Which page to reset")
    @app_commands.choices(page_name=_PAGE_CHOICES)
    @app_commands.rename(page_name="page")
    async def reset(
        self, interaction: discord.Interaction, page_name: app_commands.Choice[str]
    ) -> None:
        if not await self._guard(interaction):
            return
        page = PAGES.get(page_name.value)
        if page is None:
            await interaction.response.send_message("Unknown page.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if not await self.bot.db.delete_content_doc(page.content_doc):
            await interaction.followup.send(
                f"**{page.title}** is already using the bundled copy.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"**{page.title}** is back on the bundled copy. "
            f"{await self._refresh_if_placed(page)}",
            ephemeral=True,
        )
