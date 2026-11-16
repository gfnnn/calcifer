"""Officer commands for per-storefront invites (``/invites``).

One permanent invite per place the guild is listed (Raider.IO, Warcraft Logs,
...), so the journey can say where a new member came from. Each code is stored
with its label; welcome cards and the Front door board read the label through
``journey.invite_labels``.

Invites are only ever created here, never deleted: a link that has been handed
out has to keep working and stay traceable. Needs the Create Invite permission
(and Manage Server to show use counts in ``/invites list``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import journey
from ._guards import require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

_NO_PINGS = discord.AllowedMentions.none()


def invite_rows(
    labels: dict[str, str], uses: dict[str, int] | None
) -> list[tuple[str, str, int | None]]:
    """``(label, code, uses)`` for every known invite, sorted by label. Pure.
    ``uses`` is ``None`` (per row too) when the counts could not be read."""
    return sorted(
        ((label, code, uses.get(code) if uses is not None else None)
         for code, label in labels.items()),
        key=lambda row: (row[0].casefold(), row[1]),
    )


class InvitesCog(commands.Cog):
    invites = app_commands.Group(
        name="invites", description="Per-storefront invite links."
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def config(self):
        return self.bot.config

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_admin(
            interaction, self.config.admin_role_name, action="manage invites"
        )

    async def _channel(self):
        channel_id = self.config.invite_channel_id
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        return channel

    @invites.command(
        name="create",
        description="Make one permanent invite per storefront that does not have one yet.",
    )
    async def create(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        channel = await self._channel()
        if channel is None:
            await interaction.response.send_message(
                "No invite channel is set (`INVITE_CHANNEL_ID`).", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        have = {row["label"].casefold() for row in await self.bot.db.invites()}
        missing = [s for s in self.config.invite_storefronts if s.casefold() not in have]
        tracker = getattr(self.bot.get_cog("JourneyCog"), "invites", None)
        made: list[str] = []
        problem = ""
        for label in missing:
            try:
                # unique=True: a fresh code each time, never Discord re-using an
                # existing invite with the same settings (which would merge two
                # storefronts into one count). No expiry, no use limit.
                invite = await channel.create_invite(
                    max_age=0, max_uses=0, unique=True,
                    reason=f"Storefront invite: {label}",
                )
            except discord.Forbidden:
                problem = "\nI need the **Create Invite** permission in that channel."
                break
            await self.bot.db.add_invite(
                invite.code, label, channel_id=str(channel.id),
                created_by=str(interaction.user.id),
            )
            if tracker is not None:
                tracker.created(invite)  # so the very first join through it attributes
            made.append(f"**{label}**: `https://discord.gg/{invite.code}`")

        if made:
            text = "Created:\n" + "\n".join(made)
        elif not missing:
            text = "Every storefront already has its invite. See `/invites list`."
        else:
            text = "No invites were created."
        await interaction.followup.send(text + problem, ephemeral=True)

    @invites.command(name="list", description="Each invite's label, code and use count.")
    async def list_invites(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        uses: dict[str, int] | None = None
        note = ""
        guild = interaction.guild
        if guild is not None:
            try:
                uses = {inv.code: inv.uses or 0 for inv in await guild.invites()}
            except discord.Forbidden:
                note = "\n*Use counts need the Manage Server permission.*"
        labels = await journey.invite_labels(self.bot.db, self.config)
        lines = []
        for label, code, count in invite_rows(labels, uses):
            shown = "no longer exists" if uses is not None and count is None else (
                f"{count} uses" if count is not None else "uses unknown"
            )
            lines.append(f"**{label}** · `{code}` · {shown}")
        await interaction.response.send_message(
            "\n".join(lines) + note, ephemeral=True, allowed_mentions=_NO_PINGS
        )
