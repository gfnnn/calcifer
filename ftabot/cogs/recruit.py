"""Recruitment scout command (officers): find roster upgrades, like-for-like."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import recruit, scout
from ..models import class_color
from ..scout import ScoutUnavailable
from .admin import _is_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# A blue-flame accent for Calcifer's scout output (distinct from guild-data embeds).
SCOUT_COLOR = 0x2EA3FF


class RecruitCog(commands.Cog):
    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.config

    @app_commands.command(
        name="scout",
        description="Find recruitment upgrades for our weak roster slots (officers).",
    )
    @app_commands.describe(
        wow_class="Optional: scout one class (e.g. Paladin) instead of auto-detected weak slots"
    )
    async def scout_cmd(
        self, interaction: discord.Interaction, wow_class: str | None = None
    ) -> None:
        if not _is_admin(interaction, self.cfg.admin_role_name):
            await interaction.response.send_message(
                f"You need the **{self.cfg.admin_role_name}** role or *Manage Server* "
                "permission to scout recruits.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            reports = await scout.run_scout(
                self.bot.db, self.cfg, only_class=wow_class
            )
        except ScoutUnavailable as exc:
            await interaction.followup.send(f"🔥 Can't scout right now: {exc}", ephemeral=True)
            return
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the command
            await interaction.followup.send(f"Scout failed: {exc}", ephemeral=True)
            return

        if not reports:
            await interaction.followup.send(
                "No weak slots to shop for. The roster looks solid. 🔥"
                if not wow_class
                else f"Nothing rostered or no data for **{wow_class}**.",
                ephemeral=True,
            )
            return

        embeds = [e for r in reports if (e := _slot_embed(r)) is not None]
        if not embeds:
            await interaction.followup.send(
                "Scouted the market but found no clear upgrades over our current "
                "players. 🔥",
                ephemeral=True,
            )
            return
        # Discord caps a message at 10 embeds; we send at most a few slots.
        await interaction.followup.send(embeds=embeds[:10])


def _slot_embed(report: scout.SlotReport) -> discord.Embed | None:
    slot = report.slot
    if not report.matches:
        return None
    embed = discord.Embed(
        title=f"🔎 {slot.class_name} · upgrade candidates",
        description=(
            f"Current: **{slot.incumbent}** "
            f"(team standing {slot.incumbent_strength * 100:.0f}%). "
            f"Cold leads: strong players who *exist*, not applicants. "
            f"Check availability before reaching out."
        ),
        color=class_color(slot.class_name) or SCOUT_COLOR,
    )
    for match in report.matches:
        embed.add_field(name=_match_title(match), value=_match_body(match), inline=False)
    embed.set_footer(text="Calcifer's scout · ranked by track record, not just peak")
    return embed


def _match_title(match: recruit.ScoutMatch) -> str:
    c = match.candidate
    guild = f" · ⟨{c.guild_name}⟩" if c.guild_name else " · unguilded"
    lfg = " 🔍" if c.lfg_active else ""
    return f"{c.name}-{c.realm.title()} · score {match.score:.0f}{guild}{lfg}"


def _match_body(match: recruit.ScoutMatch) -> str:
    c = match.candidate
    lines: list[str] = []
    phrases = [
        recruit.trajectory_phrase("Parse", c.parse_seasons),
        recruit.trajectory_phrase("M+", c.mplus_seasons),
    ]
    lines.extend(p for p in phrases if p)
    poach_pct = match.poachability * 100
    poach_label = "high" if poach_pct >= 70 else "medium" if poach_pct >= 40 else "low"
    bits = [f"ilvl {c.item_level:.0f}" if c.item_level else "ilvl ?"]
    bits.append(f"upgrade +{match.upgrade_delta * 100:.0f}%")
    bits.append(f"land-odds {poach_label} ({poach_pct:.0f}%)")
    lines.append(" · ".join(bits))
    if match.flags:
        lines.append(" ".join(match.flags))
    return "\n".join(lines) or "–"


async def setup(bot: "FtaBot") -> None:  # pragma: no cover - discord entrypoint
    await bot.add_cog(RecruitCog(bot))
