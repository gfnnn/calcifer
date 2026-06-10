"""Recruitment commands (officers): the scout, plus the /recruit group that
configures the Recruitment board (open slots, trial rules, pitch override)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import recruit, scout
from ..boards import REGISTRY
from ..boards.recruitment import RECRUITMENT_TYPE, ROLE_ORDER
from ..models import class_color
from ..scout import ScoutUnavailable
from ._guards import _is_admin, require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# A blue-flame accent for Calcifer's scout output (distinct from guild-data embeds).
SCOUT_COLOR = 0x2EA3FF

_ROLE_CHOICES = [app_commands.Choice(name=role.title(), value=role) for role in ROLE_ORDER]

# Every /recruit edit lands in the DB immediately; the board itself re-renders
# on the next board tick (or an officer's /board refresh).
_BOARD_NOTE = "The recruitment board updates on its next refresh."


class RecruitCog(commands.Cog):
    recruit_cfg = app_commands.Group(
        name="recruit",
        description="Configure the recruitment board (officers).",
    )
    recruit_rule = app_commands.Group(
        name="rule",
        description="Manage the trial rules on the recruitment board (officers).",
        parent=recruit_cfg,
    )

    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.config

    async def _guard(self, interaction: discord.Interaction) -> bool:
        return await require_admin(
            interaction, self.cfg.admin_role_name, action="configure recruitment"
        )

    async def _edit_state(self, mutate) -> dict:
        """Read-modify-write the officer-configured recruitment state."""
        state = await self.bot.db.recruitment_state()
        mutate(state)
        await self.bot.db.set_recruitment_state(state)
        return state

    @recruit_cfg.command(
        name="slots", description="Set how many slots are open for a role."
    )
    @app_commands.describe(role="Which role", count="Open slots (0 = role is full)")
    @app_commands.choices(role=_ROLE_CHOICES)
    async def slots(
        self,
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        count: app_commands.Range[int, 0, 20],
    ) -> None:
        if not await self._guard(interaction):
            return
        await self._edit_state(
            lambda s: s.setdefault("slots", {}).setdefault(role.value, {}).update(
                open=count
            )
        )
        what = f"**{role.value.upper()}** now has **{count}** open slot(s)." if count else (
            f"**{role.value.upper()}** is now full."
        )
        await interaction.response.send_message(
            f"{what} {_BOARD_NOTE}", ephemeral=True
        )

    @recruit_cfg.command(
        name="ilvl", description="Set the minimum item level shown for a role."
    )
    @app_commands.describe(role="Which role", value="Minimum ilvl (0 = hide the hint)")
    @app_commands.choices(role=_ROLE_CHOICES)
    async def ilvl(
        self,
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        value: app_commands.Range[int, 0, 1000],
    ) -> None:
        if not await self._guard(interaction):
            return
        await self._edit_state(
            lambda s: s.setdefault("slots", {}).setdefault(role.value, {}).update(
                ilvl=value or None
            )
        )
        what = (
            f"**{role.value.upper()}** now shows ilvl {value}+."
            if value
            else f"**{role.value.upper()}** no longer shows an ilvl hint."
        )
        await interaction.response.send_message(
            f"{what} {_BOARD_NOTE}", ephemeral=True
        )

    @recruit_cfg.command(
        name="note", description="Set the short note shown next to a role's slots."
    )
    @app_commands.describe(role="Which role", text="The note (leave empty to clear)")
    @app_commands.choices(role=_ROLE_CHOICES)
    async def note(
        self,
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        text: str | None = None,
    ) -> None:
        if not await self._guard(interaction):
            return
        await self._edit_state(
            lambda s: s.setdefault("slots", {}).setdefault(role.value, {}).update(
                note=(text or "").strip() or None
            )
        )
        what = (
            f"**{role.value.upper()}** note set."
            if text and text.strip()
            else f"**{role.value.upper()}** note cleared."
        )
        await interaction.response.send_message(
            f"{what} {_BOARD_NOTE}", ephemeral=True
        )

    @recruit_rule.command(name="add", description="Add a trial rule to the board.")
    @app_commands.describe(text="The rule, one short line")
    async def rule_add(self, interaction: discord.Interaction, text: str) -> None:
        if not await self._guard(interaction):
            return
        text = text.strip()
        if not text:
            await interaction.response.send_message("An empty rule? Bold.", ephemeral=True)
            return
        state = await self._edit_state(
            lambda s: s.setdefault("rules", []).append(text)
        )
        await interaction.response.send_message(
            f"Rule **#{len(state['rules'])}** added. {_BOARD_NOTE}", ephemeral=True
        )

    @recruit_rule.command(
        name="remove", description="Remove a trial rule by its number (see rule list)."
    )
    @app_commands.describe(number="The rule number from /recruit rule list")
    async def rule_remove(
        self, interaction: discord.Interaction, number: app_commands.Range[int, 1, 50]
    ) -> None:
        if not await self._guard(interaction):
            return
        state = await self.bot.db.recruitment_state()
        rules = state.get("rules") or []
        if number > len(rules):
            await interaction.response.send_message(
                f"No rule **#{number}**. There are {len(rules)} (see `/recruit rule list`).",
                ephemeral=True,
            )
            return
        removed = rules.pop(number - 1)
        await self.bot.db.set_recruitment_state(state)
        await interaction.response.send_message(
            f"Removed rule **#{number}**: {removed}\n{_BOARD_NOTE}", ephemeral=True
        )

    @recruit_rule.command(name="list", description="List the configured trial rules.")
    async def rule_list(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        state = await self.bot.db.recruitment_state()
        rules = state.get("rules") or []
        if not rules:
            await interaction.response.send_message(
                "No officer-set rules; the board shows the bundled defaults "
                "(`ftabot/content/recruitment.json`). Add one with `/recruit rule add`.",
                ephemeral=True,
            )
            return
        lines = [f"**#{i}** {rule}" for i, rule in enumerate(rules, start=1)]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @recruit_cfg.command(
        name="message", description="Override the board's pitch (empty to restore the default)."
    )
    @app_commands.describe(text="The custom pitch (leave empty to restore the template)")
    async def message(
        self, interaction: discord.Interaction, text: str | None = None
    ) -> None:
        if not await self._guard(interaction):
            return
        cleaned = (text or "").strip() or None
        await self._edit_state(lambda s: s.update(message=cleaned))
        what = "Pitch overridden." if cleaned else "Pitch restored to the template."
        await interaction.response.send_message(
            f"{what} {_BOARD_NOTE}", ephemeral=True
        )

    @recruit_cfg.command(name="show", description="Preview the recruitment board here.")
    async def show(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        board = REGISTRY[RECRUITMENT_TYPE]
        payload = await board.render(self.bot.db, self.cfg, {})
        if payload is None or payload.embed is None:
            await interaction.response.send_message(
                "Nothing to preview yet.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=payload.embed, ephemeral=True)

    @recruit_cfg.command(
        name="reset", description="Clear all officer-set recruitment config (slots, rules, pitch)."
    )
    async def reset(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.bot.db.set_recruitment_state({})
        await interaction.response.send_message(
            f"Recruitment config reset to defaults. {_BOARD_NOTE}", ephemeral=True
        )

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
