"""The onboarding journey's Discord side: listeners, welcome cards, buttons.

Loaded only when ``JOURNEY_ENABLED`` is on (it needs the Server Members
intent). Everything here is a thin adapter: it turns a Discord event or a
button click into plain values and hands them to ``ftabot/journey.py``, which
owns the rules and the database writes, and to ``journey_cards.py``, which
owns the wording.

What it sends is staff-only and goes through the posting gate
(``effective_front_door_channel``), always with mentions disabled. It never
DMs anyone and never changes a role without a staff click on a button.

Member names and ids are never logged at INFO (the log is not the database).
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .. import journey
from ..journey_cards import GREET, PLACE, ButtonSpec, CardPayload, render_card
from ..runtime_settings import effective_front_door_channel
from . import _persistent
from ._guards import require_staff

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


NS = "journey"
_NO_PINGS = discord.AllowedMentions.none()


class RoleMissing(Exception):
    """The server has no role with the configured name."""


def to_view(buttons: list[ButtonSpec]) -> discord.ui.View | None:
    """Button specs as a restart-proof Discord view (``None`` for no buttons)."""
    if not buttons:
        return None
    return _persistent.view([
        _persistent.button(
            NS, spec.action, spec.target, *([spec.arg] if spec.arg else []),
            label=spec.label,
            style=(discord.ButtonStyle.success if spec.primary
                   else discord.ButtonStyle.secondary),
            disabled=spec.disabled,
        )
        for spec in buttons
    ])


class FrontDoorMessenger:
    """Sends and edits the journey's staff messages. Never pings."""

    def __init__(self, bot):
        self.bot = bot

    async def _channel(self, channel_id: str):
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        return channel

    async def send(self, channel_id: str, embed, view=None) -> str:
        channel = await self._channel(channel_id)
        kwargs = {"view": view} if view is not None else {}
        message = await channel.send(embed=embed, allowed_mentions=_NO_PINGS, **kwargs)
        return str(message.id)

    async def edit(self, channel_id: str, message_id: str, embed, view=None) -> bool:
        """Edit in place; False if the message has been deleted."""
        channel = await self._channel(channel_id)
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return False
        await message.edit(embed=embed, view=view, allowed_mentions=_NO_PINGS)
        return True


async def set_rank_role(member, role_name: str, config, *, reason: str) -> None:
    """Give ``member`` the rank role ``role_name``, swapping out any other
    placement role they hold. Every role touched must be on the allow-list
    (``journey.check_role_allowed``): anything else raises before Discord is
    asked, whatever the bot's role position would permit."""
    journey.check_role_allowed(role_name, config)
    wanted = role_name.casefold()
    role = next((r for r in member.guild.roles if r.name.casefold() == wanted), None)
    if role is None:
        raise RoleMissing(role_name)
    placement = {r.casefold() for r in config.journey_placement_roles}
    swap_out = [
        r for r in member.roles if r.name.casefold() in placement and r.id != role.id
    ]
    for old in swap_out:
        journey.check_role_allowed(old.name, config)
    if swap_out:
        await member.remove_roles(*swap_out, reason=reason)
    if all(r.id != role.id for r in member.roles):
        await member.add_roles(role, reason=reason)


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
        self.messenger = FrontDoorMessenger(bot)
        # The buttons are restart-proof: clicks are routed here by custom_id.
        _persistent.handler(NS, GREET)(self.handle_greet)
        _persistent.handler(NS, PLACE)(self.handle_place)

    @property
    def config(self):
        return self.bot.config

    # -- welcome cards ----------------------------------------------------------

    def _picked(self, member) -> tuple[str, ...] | None:
        """The interest roles a member holds right now (``None`` if they left)."""
        if member is None:
            return None
        wanted = {r.casefold() for r in self.config.journey_interest_roles}
        return tuple(r.name for r in member.roles if r.name.casefold() in wanted)

    async def build_card(self, discord_id: str, member=None) -> CardPayload | None:
        status = await journey.status_for(self.db, self.config, discord_id)
        if status is None:
            return None
        names = _names(member) if member is not None else [status.display_name]
        match = await journey.roster_match_for(self.db, names)
        return render_card(status, self.config, match, self._picked(member))

    async def post_card(self, member) -> bool:
        """Post the welcome card for a member who just arrived, wherever the
        posting gate says the front door currently is. False if nothing went out."""
        if not self.config.journey_cards_enabled:
            return False
        channel_id = await effective_front_door_channel(self.db, self.config)
        if channel_id is None:
            return False  # guardrail: off, or no confirmed destination
        payload = await self.build_card(str(member.id), member)
        if payload is None:
            return False
        message_id = await self.messenger.send(
            channel_id, payload.embed, to_view(payload.buttons)
        )
        await self.db.set_journey_card(str(member.id), channel_id, message_id)
        return True

    async def refresh_card(self, discord_id: str, member=None) -> bool:
        """Re-render a person's card in place (a click, a role change, a leave)."""
        card = await self.db.get_journey_card(discord_id)
        if card is None:
            return False
        payload = await self.build_card(discord_id, member)
        if payload is None:
            return False
        if await self.messenger.edit(
            card["channel_id"], card["message_id"], payload.embed,
            to_view(payload.buttons),
        ):
            return True
        await self.db.delete_journey_card(discord_id)  # the card was deleted
        return False

    async def _quietly(self, coro, what: str) -> None:
        """Run a card send/edit so a Discord hiccup never loses the journey
        record. Logs the kind of failure only, never who it was about."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 - recording matters more than the card
            log.warning("journey: %s failed (%s)", what, type(exc).__name__)

    # -- buttons ------------------------------------------------------------------

    @staticmethod
    async def _reply(interaction, text: str) -> None:
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=_NO_PINGS
        )

    def _member(self, interaction, discord_id: str):
        guild = getattr(interaction, "guild", None)
        return guild.get_member(int(discord_id)) if guild is not None else None

    async def _target(self, interaction, discord_id: str):
        """The (status, member) a click is about, after the staff guard. Replies
        and returns ``None`` when the click cannot go ahead."""
        if not await require_staff(
            interaction, self.config, action="use the welcome buttons"
        ):
            return None
        status = await journey.status_for(self.db, self.config, discord_id)
        if status is None:
            await self._reply(interaction, "I have no record of that member.")
            return None
        member = self._member(interaction, discord_id)
        if member is None or not status.present:
            await self._reply(interaction, "They have left the server.")
            await self._quietly(self.refresh_card(discord_id), "card refresh")
            return None
        return status, member

    async def _finish(self, interaction, discord_id: str, member, note: str) -> None:
        """Show the result of a click. On the card itself the card *is* the
        answer (it edits in place); from any other message, a private note plus
        a quiet update of the card."""
        card = await self.db.get_journey_card(discord_id)
        message = getattr(interaction, "message", None)
        on_card = bool(card and message and str(message.id) == card["message_id"])
        if on_card:
            payload = await self.build_card(discord_id, member)
            await interaction.response.edit_message(
                embed=payload.embed, view=to_view(payload.buttons),
                allowed_mentions=_NO_PINGS,
            )
            return
        await self._reply(interaction, note)
        await self._quietly(self.refresh_card(discord_id, member), "card refresh")

    async def handle_greet(self, interaction, discord_id: str) -> None:
        found = await self._target(interaction, discord_id)
        if found is None:
            return
        status, member = found
        if status.greeted:
            note = f"Already greeted by <@{status.greeted_by}>."
        else:
            await journey.record_greeted(
                self.db, self.config, discord_id, str(interaction.user.id)
            )
            note = f"Noted: you said hi to <@{discord_id}>."
        await self._finish(interaction, discord_id, member, note)

    async def handle_place(self, interaction, discord_id: str, role: str) -> None:
        found = await self._target(interaction, discord_id)
        if found is None:
            return
        _, member = found
        actor = str(interaction.user.id)
        try:
            await set_rank_role(
                member, role, self.config, reason=f"Placed by {actor} via Calcifer"
            )
        except journey.RoleNotAllowed:
            await self._reply(
                interaction,
                f"I am not allowed to set **{role}**. It is not on my role list.",
            )
            return
        except RoleMissing:
            await self._reply(interaction, f"This server has no role called **{role}**.")
            return
        except discord.Forbidden:
            await self._reply(
                interaction,
                f"Discord would not let me set **{role}**. I need Manage Roles, "
                "and my own role has to sit above it.",
            )
            return
        await journey.record_placed(self.db, self.config, discord_id, role, actor)
        await self._finish(
            interaction, discord_id, member, f"<@{discord_id}> is now **{role}**."
        )

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
        await self._quietly(self.post_card(member), "welcome card")

    @commands.Cog.listener()
    async def on_member_remove(self, member) -> None:
        if member.bot or not self._ours(member.guild):
            return
        if await journey.record_leave(self.db, str(member.id)):
            await self._quietly(self.refresh_card(str(member.id)), "card refresh")

    @commands.Cog.listener()
    async def on_member_update(self, before, after) -> None:
        if after.bot or not self._ours(after.guild):
            return
        old = {r.id: r.name for r in before.roles}
        new = {r.id: r.name for r in after.roles}
        added = [name for rid, name in new.items() if rid not in old]
        removed = [name for rid, name in old.items() if rid not in new]
        await journey.record_roles_changed(
            self.db, str(after.id), added=added, removed=removed
        )
        renamed = _display(before) != _display(after)
        if renamed:
            await self.db.set_person_name(str(after.id), _display(after))
        # A rank given by hand, an interest picked in onboarding or a new
        # nickname all change what the card should say.
        shown = {
            r.casefold()
            for r in (*self.config.journey_rank_roles, *self.config.journey_interest_roles)
        }
        if renamed or any(r.casefold() in shown for r in (*added, *removed)):
            await self._quietly(self.refresh_card(str(after.id), after), "card refresh")
