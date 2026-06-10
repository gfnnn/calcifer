"""Officer commands to create, place, and promote Calcifer's living boards.

A board is registered against a *production* channel but stays sandboxed (the
posting guardrail routes it to the test channel) until an officer reviews it and
``/board promote``s it. So the whole off -> test -> promote flow lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..boards import REGISTRY, BotBoardMessenger, refresh_board
from ..posting import PostGate, PostingMode
from ._guards import require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# Friendly names for the registered board types.
_BOARD_NAMES = {
    "hot20": "Calcifer's Hot 20",
    "news_highlights": "News Highlights",
    "weekly_events": "Weekly Events",
    "guides": "Guides & Resources",
    "rules_info": "Rules & Info",
    "roster_pulse": "Roster Pulse",
    "recruitment": "Recruitment",
}
_BOARD_CHOICES = [
    app_commands.Choice(name=_BOARD_NAMES.get(key, key), value=key) for key in REGISTRY
]


class BoardsCog(commands.Cog):
    board = app_commands.Group(
        name="board", description="Manage Calcifer's living boards (officers)."
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_admin(
            interaction, self.bot.config.admin_role_name, action="manage boards"
        )

    @board.command(name="create", description="Register a board in a channel (starts sandboxed).")
    @app_commands.describe(
        board_type="Which board to create",
        channel="Its eventual production channel",
    )
    @app_commands.choices(board_type=_BOARD_CHOICES)
    async def create(
        self,
        interaction: discord.Interaction,
        board_type: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        if not await self._guard(interaction):
            return
        if board_type.value not in REGISTRY:
            await interaction.response.send_message("Unknown board type.", ephemeral=True)
            return
        try:
            board_id = await self.bot.db.add_board(board_type.value, str(channel.id))
        except Exception:  # noqa: BLE001 - almost certainly the UNIQUE constraint
            await interaction.response.send_message(
                f"A **{board_type.name}** is already registered for {channel.mention}.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Created **{board_type.name}** (board `#{board_id}`) for {channel.mention}.\n"
            f"It is **un-promoted**, so it renders to the test channel until you run "
            f"`/board promote {board_id}`. Use `/board refresh {board_id}` to post it now.",
            ephemeral=True,
        )

    @board.command(name="list", description="List the registered boards and their status.")
    async def list_boards(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        boards = await self.bot.db.boards(enabled_only=False)
        if not boards:
            await interaction.response.send_message(
                "No boards yet. Create one with `/board create`.", ephemeral=True
            )
            return
        lines = []
        for b in boards:
            name = _BOARD_NAMES.get(b["board_type"], b["board_type"])
            state = "live ✅" if b["promoted"] else "sandboxed 🧪"
            if not b["enabled"]:
                state = "disabled ⏸"
            posted = "posted" if b["message_id"] else "not posted"
            lines.append(
                f"`#{b['id']}` **{name}** -> <#{b['channel_id']}> · {state} · {posted}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @board.command(name="refresh", description="Render a board now (respecting the posting guardrail).")
    @app_commands.describe(board_id="The board number from /board list")
    async def refresh(self, interaction: discord.Interaction, board_id: int) -> None:
        if not await self._guard(interaction):
            return
        row = await self.bot.db.get_board(board_id)
        if row is None:
            await interaction.response.send_message(
                f"No board `#{board_id}`. See `/board list`.", ephemeral=True
            )
            return
        board = REGISTRY.get(row["board_type"])
        if board is None:
            await interaction.response.send_message(
                f"Board `#{board_id}` has an unknown type (`{row['board_type']}`).",
                ephemeral=True,
            )
            return

        gate = PostGate.from_config(self.bot.config)
        await interaction.response.defer(ephemeral=True)
        action = await refresh_board(
            self.bot.db, self.bot.config, board, row, BotBoardMessenger(self.bot), gate
        )

        if action == "blocked":
            reason = (
                "posting is **off** (`POSTING_MODE=off`)"
                if gate.mode is PostingMode.OFF
                else "no test channel is confirmed (`TEST_CHANNEL_ID`)"
            )
            await interaction.followup.send(
                f"Guardrail held: nothing posted because {reason}.", ephemeral=True
            )
            return
        destination = gate.resolve(row["channel_id"], promoted=row["promoted"])
        where = f"<#{destination}>" if destination else "nowhere"
        if action == "unchanged":
            await interaction.followup.send(
                f"Board `#{board_id}` is already current in {where}.", ephemeral=True
            )
        elif action == "skip":
            await interaction.followup.send(
                f"Board `#{board_id}` had nothing to show yet.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"Board `#{board_id}` {action} in {where}.", ephemeral=True
            )

    @board.command(name="promote", description="Promote a board to its production channel.")
    @app_commands.describe(board_id="The board number from /board list")
    async def promote(self, interaction: discord.Interaction, board_id: int) -> None:
        if not await self._guard(interaction):
            return
        row = await self.bot.db.get_board(board_id)
        if row is None:
            await interaction.response.send_message(
                f"No board `#{board_id}`. See `/board list`.", ephemeral=True
            )
            return
        await self.bot.db.set_board_promoted(board_id, True)
        await interaction.response.send_message(
            f"Promoted board `#{board_id}` -> <#{row['channel_id']}>. "
            f"On the next refresh (or `/board refresh {board_id}`) it posts there, "
            "and members will see it. Needs `POSTING_MODE=live`.",
            ephemeral=True,
        )

    @board.command(name="demote", description="Send a board back to the test channel.")
    @app_commands.describe(board_id="The board number from /board list")
    async def demote(self, interaction: discord.Interaction, board_id: int) -> None:
        if not await self._guard(interaction):
            return
        if not await self.bot.db.set_board_promoted(board_id, False):
            await interaction.response.send_message(
                f"No board `#{board_id}`. See `/board list`.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Board `#{board_id}` is sandboxed again; it renders to the test channel.",
            ephemeral=True,
        )

    @board.command(name="remove", description="Unregister a board (does not delete its message).")
    @app_commands.describe(board_id="The board number from /board list")
    async def remove(self, interaction: discord.Interaction, board_id: int) -> None:
        if not await self._guard(interaction):
            return
        if await self.bot.db.delete_board(board_id):
            await interaction.response.send_message(
                f"Removed board `#{board_id}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No board `#{board_id}` to remove.", ephemeral=True
            )
