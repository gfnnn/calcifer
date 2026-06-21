"""Recruitment commands (officers): the scout, plus the /recruit group that
configures the Recruitment board (open slots, trial rules, pitch override)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import recruit, scout
from ..boards import REGISTRY, guild_emoji_map
from ..boards.recruitment import RECRUITMENT_TYPE, ROLE_ORDER
from ..models import class_color
from ..scout import ScoutUnavailable
from ._guards import _is_admin, require_admin

if TYPE_CHECKING:
    from ..bot import FtaBot

# A blue-flame accent for Calcifer's scout output (distinct from guild-data embeds).
SCOUT_COLOR = 0x2EA3FF

_ROLE_CHOICES = [app_commands.Choice(name=role.title(), value=role) for role in ROLE_ORDER]
_STATE_CHOICES = [
    app_commands.Choice(name=state.title(), value=state) for state in recruit.LEAD_STATES
]

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
    recruit_lead = app_commands.Group(
        name="lead",
        description="Track scouted recruitment leads through the funnel (officers).",
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
        # With no officer rules left, the board falls back to the bundled
        # defaults; say so, or the officer thinks the section just emptied.
        fallback = "" if rules else " The board now shows the bundled default expectations."
        await interaction.response.send_message(
            f"Removed rule **#{number}**: {removed}{fallback}\n{_BOARD_NOTE}",
            ephemeral=True,
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
        # Rendering scans the whole roster for the live tokens; defer so a
        # slow DB can't blow the 3-second interaction deadline.
        await interaction.response.defer(ephemeral=True)
        board = REGISTRY[RECRUITMENT_TYPE]
        payload = await board.render(
            self.bot.db, self.cfg, {}, guild_emoji_map(self.bot)
        )
        if payload is None or payload.embed is None:
            await interaction.followup.send("Nothing to preview yet.", ephemeral=True)
            return
        await interaction.followup.send(embed=payload.embed, ephemeral=True)

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

    # -- lead funnel ---------------------------------------------------------

    @recruit_lead.command(
        name="capture",
        description="Run the scout and save the candidates as trackable leads.",
    )
    @app_commands.describe(
        wow_class="Optional: scout one class instead of auto-detected weak slots"
    )
    async def lead_capture(
        self, interaction: discord.Interaction, wow_class: str | None = None
    ) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            reports = await scout.run_scout(self.bot.db, self.cfg, only_class=wow_class)
        except ScoutUnavailable as exc:
            await interaction.followup.send(f"🔥 Can't scout right now: {exc}", ephemeral=True)
            return
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the command
            await interaction.followup.send(f"Scout failed: {exc}", ephemeral=True)
            return

        leads = scout.lead_dicts(reports)
        for lead in leads:
            await self.bot.db.upsert_lead(lead)
        if not leads:
            await interaction.followup.send(
                "Scouted, but no upgrades worth tracking. The roster holds. 🔥",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Captured **{len(leads)}** lead(s). See them with `/recruit lead list`.",
            ephemeral=True,
        )

    @recruit_lead.command(name="list", description="List recruitment leads (new first).")
    @app_commands.describe(state="Optional: only leads in this funnel state")
    @app_commands.choices(state=_STATE_CHOICES)
    async def lead_list(
        self,
        interaction: discord.Interaction,
        state: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._guard(interaction):
            return
        rows = await self.bot.db.leads(state=state.value if state else None)
        if not rows:
            scope = f" in **{state.value}**" if state else ""
            await interaction.response.send_message(
                f"No leads{scope} yet. Run `/recruit lead capture` to scout some.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=_leads_embed(rows, state.value if state else None), ephemeral=True
        )

    @recruit_lead.command(name="set", description="Move a lead to a funnel state.")
    @app_commands.describe(lead_id="The lead number from /recruit lead list", state="New state")
    @app_commands.choices(state=_STATE_CHOICES)
    async def lead_set(
        self,
        interaction: discord.Interaction,
        lead_id: int,
        state: app_commands.Choice[str],
    ) -> None:
        if not await self._guard(interaction):
            return
        lead = await self.bot.db.get_lead(lead_id)
        if lead is None:
            await interaction.response.send_message(
                f"No lead `#{lead_id}`. See `/recruit lead list`.", ephemeral=True
            )
            return
        await self.bot.db.set_lead_state(lead_id, state.value)
        await interaction.response.send_message(
            f"**{lead['name']}-{lead['realm'].title()}** is now **{state.value}**.",
            ephemeral=True,
        )

    @recruit_lead.command(name="note", description="Set or clear an officer note on a lead.")
    @app_commands.describe(lead_id="The lead number from /recruit lead list", text="Note (empty to clear)")
    async def lead_note(
        self, interaction: discord.Interaction, lead_id: int, text: str | None = None
    ) -> None:
        if not await self._guard(interaction):
            return
        if await self.bot.db.set_lead_note(lead_id, (text or "").strip() or None):
            what = "Note set." if text and text.strip() else "Note cleared."
            await interaction.response.send_message(what, ephemeral=True)
        else:
            await interaction.response.send_message(
                f"No lead `#{lead_id}`. See `/recruit lead list`.", ephemeral=True
            )

    @recruit_lead.command(name="remove", description="Delete a lead from the funnel.")
    @app_commands.describe(lead_id="The lead number from /recruit lead list")
    async def lead_remove(self, interaction: discord.Interaction, lead_id: int) -> None:
        if not await self._guard(interaction):
            return
        if await self.bot.db.delete_lead(lead_id):
            await interaction.response.send_message(
                f"Removed lead `#{lead_id}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No lead `#{lead_id}` to remove.", ephemeral=True
            )


def _leads_embed(rows: list[dict], state: str | None) -> discord.Embed:
    title = f"🔎 Recruitment leads · {state}" if state else "🔎 Recruitment leads"
    lines: list[str] = []
    for r in rows[:25]:
        lfg = " 🔍LFG" if r["lfg"] else ""
        ilvl = f" · ilvl {r['item_level']:.0f}" if r["item_level"] else ""
        score = f" · score {r['score']:.0f}" if r["score"] is not None else ""
        guild = f" · ⟨{r['guild_name']}⟩" if r["guild_name"] else ""
        note = f"\n   ↳ {r['notes']}" if r["notes"] else ""
        lines.append(
            f"`#{r['id']}` **{r['name']}-{r['realm'].title()}** "
            f"({r['class_name'] or '?'}) · *{r['state']}*{score}{ilvl}{lfg}{guild}{note}"
        )
    embed = discord.Embed(
        title=title, description="\n".join(lines), color=SCOUT_COLOR
    )
    embed.set_footer(text="Cold leads, not applicants. Vet before reaching out.")
    return embed


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
