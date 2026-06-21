"""The bot-backed :class:`BoardMessenger`: posts/edits board messages.

Living boards re-render constantly, so they must **never ping** — every send and
edit forces ``allowed_mentions=none``. ``edit`` returns ``False`` when the
message has vanished, so the engine can self-heal by re-posting.
"""

from __future__ import annotations

import discord


def guild_emoji_map(bot) -> dict[str, str]:
    """Map custom-emoji ``name`` -> its ``<:name:id>`` markup from the bot's guilds.

    Renderers reference emoji by brand key and resolve through this so they can
    prefer the uploaded ``:roleheal:``/``:class_*:`` art (with a unicode fallback
    when a name isn't uploaded). Defensive: returns ``{}`` before the cache warms
    or off a bot with no emoji, so a render never depends on it being present.
    """
    return {emoji.name: str(emoji) for emoji in getattr(bot, "emojis", None) or []}


class BotBoardMessenger:
    """Implements the engine's ``BoardMessenger`` protocol over a live bot."""

    # Boards never notify anyone, on first post or any later edit.
    _NO_PINGS = discord.AllowedMentions.none()

    def __init__(self, bot):
        self.bot = bot

    async def _channel(self, channel_id: str):
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        return channel

    async def send(self, channel_id: str, payload) -> str:
        channel = await self._channel(channel_id)
        message = await channel.send(
            content=payload.content,
            embed=payload.embed,
            allowed_mentions=self._NO_PINGS,
        )
        return str(message.id)

    async def edit(self, channel_id: str, message_id: str, payload) -> bool:
        channel = await self._channel(channel_id)
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return False  # self-heal: the loop will re-create it
        await message.edit(
            content=payload.content,
            embed=payload.embed,
            allowed_mentions=self._NO_PINGS,
        )
        return True

    async def delete(self, channel_id: str, message_id: str) -> bool:
        """Delete an owned message (used when a sticky page moves channel).

        Returns False if it was already gone, mirroring ``edit``.
        """
        channel = await self._channel(channel_id)
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return False
        await message.delete()
        return True
