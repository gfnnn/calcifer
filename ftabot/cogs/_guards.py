"""Shared permission gate for the officer-only command surface.

Lives in its own module (rather than in the admin cog) so every cog gates
through the same helper without importing from a sibling cog.
"""

from __future__ import annotations

import discord


def _is_admin(interaction: discord.Interaction, role_name: str) -> bool:
    """Allow Manage-Server permission holders or members of the admin role."""
    perms = interaction.permissions
    if perms.manage_guild or perms.administrator:
        return True
    member = interaction.user
    if isinstance(member, discord.Member):
        return any(r.name == role_name for r in member.roles)
    return False


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
