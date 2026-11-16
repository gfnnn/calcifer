"""Shared permission gates for the command surface.

Lives in its own module (rather than in the admin cog) so every cog gates
through the same helpers without importing from a sibling cog. Three tiers:

* :func:`require_admin` — officers (Manage-Server perm OR the admin role).
* :func:`require_gm_or_discord_admin` — the GM or a Discord admin only (the
  admin *role* does not qualify); used for the living boards.
* :func:`require_gm` — the GM alone.

plus :func:`require_team_member`, the team-chemistry-roster gate used by the
member-facing data commands, and :func:`require_staff`, the welcome-team gate
(Flamekeeper / Officer / GM) on the onboarding journey's buttons and commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ..config import Config
    from ..db import Database


def _is_admin(interaction: discord.Interaction, role_name: str) -> bool:
    """Allow Manage-Server permission holders or members of the admin role."""
    perms = interaction.permissions
    if perms.manage_guild or perms.administrator:
        return True
    member = interaction.user
    if isinstance(member, discord.Member):
        return any(r.name == role_name for r in member.roles)
    return False


def _is_discord_admin(interaction: discord.Interaction) -> bool:
    """True for a Discord admin (Manage-Server or Administrator), role aside."""
    perms = interaction.permissions
    return bool(perms.manage_guild or perms.administrator)


def _is_gm(interaction: discord.Interaction, gm_discord_id: int | None) -> bool:
    """True only for the configured GM Discord user."""
    return gm_discord_id is not None and (
        getattr(interaction.user, "id", None) == gm_discord_id
    )


async def require_admin(
    interaction: discord.Interaction,
    role_name: str,
    *,
    action: str = "use this command",
) -> bool:
    """True if the caller is an officer; otherwise reply (ephemeral) and False."""
    if _is_admin(interaction, role_name):
        return True
    await interaction.response.send_message(
        f"You need the **{role_name}** role or *Manage Server* permission "
        f"to {action}.",
        ephemeral=True,
    )
    return False


async def require_gm_or_discord_admin(
    interaction: discord.Interaction,
    gm_discord_id: int | None,
    *,
    action: str = "use this command",
) -> bool:
    """True for the GM or a Discord admin; otherwise reply (ephemeral), False."""
    if _is_gm(interaction, gm_discord_id) or _is_discord_admin(interaction):
        return True
    await interaction.response.send_message(
        f"Only the Guild Master or a Discord admin can {action}.",
        ephemeral=True,
    )
    return False


async def require_gm(
    interaction: discord.Interaction,
    gm_discord_id: int | None,
    *,
    action: str = "use this command",
) -> bool:
    """True only for the configured GM; otherwise reply (ephemeral) and False."""
    if _is_gm(interaction, gm_discord_id):
        return True
    await interaction.response.send_message(
        f"Only the Guild Master can {action}.",
        ephemeral=True,
    )
    return False


async def require_team_member(
    db: "Database",
    interaction: discord.Interaction,
    *,
    action: str = "use this command",
) -> bool:
    """True if the caller has a character on the team-chemistry roster.

    A caller "is on the team" when any character linked to their Discord id has
    an effective team (a manual ``raid_team`` or the synced WoW Audit team).
    Otherwise replies (ephemeral) and returns False.
    """
    linked = await db.search_members(discord_id=str(interaction.user.id))
    if any(m.effective_team for m in linked):
        return True
    await interaction.response.send_message(
        f"Only members of the team roster can {action}. "
        "Ask an officer if you should be on it.",
        ephemeral=True,
    )
    return False


def is_staff(interaction: discord.Interaction, config: "Config") -> bool:
    """True for the welcome team: a configured staff role, or the GM.

    Deliberately role-based only. Unlike :func:`_is_admin`, the Manage-Server
    permission does not qualify: the journey's buttons place people, and that
    belongs to the named staff roles.
    """
    if _is_gm(interaction, config.gm_discord_id):
        return True
    wanted = {name.casefold() for name in config.journey_staff_roles}
    roles = getattr(interaction.user, "roles", None) or ()
    return any(getattr(r, "name", "").casefold() in wanted for r in roles)


async def require_staff(
    interaction: discord.Interaction,
    config: "Config",
    *,
    action: str = "do that",
) -> bool:
    """True for the welcome team; otherwise reply (ephemeral) and False."""
    if is_staff(interaction, config):
        return True
    roles = ", ".join(config.journey_staff_roles)
    await interaction.response.send_message(
        f"Only the welcome team ({roles}) can {action}.", ephemeral=True
    )
    return False
