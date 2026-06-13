"""Discord bot entry point.

Run with:  python -m ftabot.bot
Requires DISCORD_TOKEN to be set (see .env.example).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .config import Config
from .db import Database

log = logging.getLogger("ftabot")


class FtaBot(commands.Bot):
    """The guild-database bot. Holds the shared Config and Database."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        # We only use slash commands + member mentions; no message content needed.
        super().__init__(command_prefix="!fta ", intents=intents)
        self.config = config
        self.db = Database(config.database_path)

    async def setup_hook(self) -> None:
        await self.db.connect()

        if not self.config.bot_commands_enabled:
            # Commands disabled (e.g. on production): don't load the command cogs
            # and push an empty tree so any previously registered commands are
            # removed from Discord. The bot still logs in and runs presence/feeds.
            if self.config.discord_guild_id:
                guild = discord.Object(id=self.config.discord_guild_id)
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.warning("Bot commands are disabled; cleared registered commands")
            return

        # Load cogs.
        from .cogs.admin import AdminCog
        from .cogs.alerts import AlertsCog
        from .cogs.boards import BoardsCog
        from .cogs.feeds import FeedsCog
        from .cogs.members import MembersCog
        from .cogs.pages import PagesCog
        from .cogs.recruit import RecruitCog
        from .cogs.reports import ReportsCog
        from .cogs.scores import ScoresCog

        await self.add_cog(MembersCog(self))
        await self.add_cog(AdminCog(self))
        await self.add_cog(ReportsCog(self))
        await self.add_cog(RecruitCog(self))
        await self.add_cog(FeedsCog(self))
        await self.add_cog(BoardsCog(self))
        await self.add_cog(PagesCog(self))
        await self.add_cog(ScoresCog(self))
        await self.add_cog(AlertsCog(self))

        # Sync slash commands. Scoping to a guild makes them appear instantly.
        if self.config.discord_guild_id:
            guild = discord.Object(id=self.config.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s", self.config.discord_guild_id)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally (may take up to 1h to appear)")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s members in DB)", self.user, await self.db.count())
        # Calcifer tends the hearth (the guild's Discord) in Harry's name.
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="the hearth of From the Ashes"
            )
        )

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()
    if not config.discord_token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    bot = FtaBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
