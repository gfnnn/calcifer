"""The onboarding journey's Discord side: the gateway listeners.

Loaded only when ``JOURNEY_ENABLED`` is on (it needs the Server Members
intent). Everything here is a thin adapter: it turns a Discord event into plain
values and hands them to ``ftabot/journey.py``, which owns the rules and the
database writes. Nothing is posted from this module.

Member names and ids are never logged at INFO (the log is not the database).
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import journey

log = logging.getLogger("ftabot.journey")


class InviteTracker:
    """Remembers each invite's use count so a join can be attributed.

    Discord does not say which invite a new member used; the only signal is
    which count went up. Reading counts needs Manage Server: without it every
    join is ``unknown``, and that is said once in the log, not on every join.
    """

    def __init__(self) -> None:
        self.uses: dict[str, int] | None = None
        self._warned = False
        self._lock = asyncio.Lock()

    async def _fetch(self, guild) -> dict[str, int] | None:
        try:
            counts = {inv.code: inv.uses or 0 for inv in await guild.invites()}
            if "VANITY_URL" in (getattr(guild, "features", None) or ()):
                vanity = await guild.vanity_invite()
                if vanity is not None:
                    counts[journey.VIA_VANITY] = vanity.uses or 0
            return counts
        except discord.Forbidden:
            if not self._warned:
                self._warned = True
                log.warning(
                    "journey: cannot read invites (Manage Server is missing), "
                    "so joins will be recorded as came via 'unknown'"
                )
            return None
        except discord.HTTPException as exc:
            log.debug("journey: invite fetch failed: %s", exc)
            return None

    async def refresh(self, guild) -> None:
        """Re-read every count (on ready, and after a reconnect)."""
        async with self._lock:
            self.uses = await self._fetch(guild)

    def created(self, invite) -> None:
        if self.uses is not None:
            self.uses[invite.code] = invite.uses or 0

    def deleted(self, invite) -> None:
        if self.uses is not None:
            self.uses.pop(invite.code, None)

    async def attribute(self, guild) -> str:
        """The invite the member who just joined used, or ``unknown``."""
        async with self._lock:
            after = await self._fetch(guild)
            via = journey.attribute_invite(self.uses, after)
            if after is not None:
                self.uses = after
            return via


def _names(member) -> list[str | None]:
    """A member's names, the one staff see first: nickname, global, username."""
    return [
        getattr(member, "nick", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
    ]


def _display(member) -> str | None:
    return getattr(member, "display_name", None) or getattr(member, "name", None)


class JourneyCog(commands.Cog):
    """Joins, leaves, role changes and invite bookkeeping for one server."""

    def __init__(self, bot):
        self.bot = bot
        self.invites = InviteTracker()

    @property
    def db(self):
        return self.bot.db

    def _ours(self, guild) -> bool:
        """Only the configured server counts (any server if none is configured)."""
        wanted = self.bot.config.discord_guild_id
        return guild is not None and (wanted is None or guild.id == wanted)

    def _home_guild(self):
        wanted = self.bot.config.discord_guild_id
        if wanted is not None:
            return self.bot.get_guild(wanted)
        guilds = list(self.bot.guilds)
        return guilds[0] if len(guilds) == 1 else None

    # -- startup ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        guild = self._home_guild()
        if guild is None:
            log.warning("journey: no home server found (set DISCORD_GUILD_ID)")
            return
        await self.invites.refresh(guild)
        present = [
            journey.Present(
                discord_id=str(m.id),
                display_name=_display(m),
                joined_at=journey.iso(m.joined_at) if m.joined_at else None,
            )
            for m in guild.members
            if not m.bot
        ]
        result = await journey.reconcile(self.db, present)
        log.info(
            "journey ready: %d baselined, %d missed join(s), %d missed leave(s)",
            result.baselined, result.missed_joins, result.missed_leaves,
        )

    # -- invites ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_invite_create(self, invite) -> None:
        if self._ours(invite.guild):
            self.invites.created(invite)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite) -> None:
        if self._ours(invite.guild):
            self.invites.deleted(invite)

    # -- members ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member) -> None:
        if member.bot or not self._ours(member.guild):
            return
        came_via = await self.invites.attribute(member.guild)
        await journey.record_join(
            self.db,
            discord_id=str(member.id),
            display_name=_display(member),
            joined_at=journey.iso(member.joined_at) if member.joined_at else None,
            came_via=came_via,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member) -> None:
        if member.bot or not self._ours(member.guild):
            return
        await journey.record_leave(self.db, str(member.id))

    @commands.Cog.listener()
    async def on_member_update(self, before, after) -> None:
        if after.bot or not self._ours(after.guild):
            return
        old = {r.id: r.name for r in before.roles}
        new = {r.id: r.name for r in after.roles}
        await journey.record_roles_changed(
            self.db,
            str(after.id),
            added=[name for rid, name in new.items() if rid not in old],
            removed=[name for rid, name in old.items() if rid not in new],
        )
        if _display(before) != _display(after):
            await self.db.set_person_name(str(after.id), _display(after))
