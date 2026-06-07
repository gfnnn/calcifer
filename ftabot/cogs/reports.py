"""GM, Raid Leader and Loot commands (read-only, available to everyone)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import views
from ..models import DEFAULT_EMBED_COLOR

if TYPE_CHECKING:
    from ..bot import FtaBot


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.0f}%"


class ReportsCog(commands.Cog):
    def __init__(self, bot: "FtaBot"):
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.config

    @app_commands.command(name="gmreport", description="Guild health overview for the GM.")
    async def gmreport(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        r = await views.gm_report(self.bot.db, self.cfg)
        h = r["health"]
        score = h["score"]
        embed = discord.Embed(
            title=f"{self.cfg.guild_name} · guild health",
            description=(
                f"**Health score: {score:.0f}/100**" if score is not None else "No data yet."
            ),
            color=DEFAULT_EMBED_COLOR,
        )
        c = r["counts"]
        embed.add_field(name="Members", value=str(c["total"]))
        embed.add_field(name="Active", value=str(c["active"]))
        embed.add_field(name="Inactive", value=str(c["inactive"]))
        embed.add_field(name="Prep ready", value=_pct(_x100(r["prep_rate"])))
        embed.add_field(name="Vault engaged", value=_pct(_x100(r["vault_rate"])))
        embed.add_field(name="Progression", value=r["progression"] or "–", inline=False)

        if h["components"]:
            embed.add_field(
                name="Health components",
                value="\n".join(
                    f"{k.title()}: {v*100:.0f}%" for k, v in h["components"].items()
                ),
                inline=False,
            )

        actives = sum(1 for o in r["officers"] if o["active"])
        embed.add_field(
            name=f"Officers ({actives}/{len(r['officers'])} active)",
            value="\n".join(
                f"{'🟢' if o['active'] else '🔴'} **{o['name']}** "
                f"({o['officer_role'] or 'officer'}) · att {_pct(o['attendance_pct'])}"
                for o in r["officers"][:10]
            )
            or "None detected.",
            inline=False,
        )
        if r["missing_buffs"]:
            embed.add_field(
                name="⚠️ Missing raid buffs",
                value=", ".join(b.split(" (")[0] for b in r["missing_buffs"]),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="raidcheck", description="Raider readiness board.")
    @app_commands.describe(team="Optional raid team to filter by")
    async def raidcheck(
        self, interaction: discord.Interaction, team: str | None = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        r = await views.raid_board(self.bot.db, self.cfg, team=team)
        if not r["rows"]:
            await interaction.followup.send("No raiders tracked yet.")
            return
        lines = []
        for i, row in enumerate(r["rows"][:20], start=1):
            flags = " ".join(row["flags"][:3])
            lines.append(
                f"`{i:>2}.` **{row['name']}** {row['readiness']:.0f} "
                f"· ilvl {_fmt(row['item_level'])} · att {_pct(row['attendance_pct'])} {flags}"
            )
        embed = discord.Embed(
            title=f"Readiness board{f' · {team}' if team else ''}",
            description="\n".join(lines),
            color=DEFAULT_EMBED_COLOR,
        )
        if r["missing_buffs"]:
            embed.set_footer(
                text="Missing buffs: "
                + ", ".join(b.split(" (")[0] for b in r["missing_buffs"])
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="loot", description="Loot history for a player, or fairness overview.")
    @app_commands.describe(player="Character name (omit for the fairness overview)")
    async def loot(
        self, interaction: discord.Interaction, player: str | None = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        if player:
            entries = await self.bot.db.get_loot(character=player, limit=15)
            if not entries:
                await interaction.followup.send(
                    f"No loot recorded for **{player}**.", ephemeral=True
                )
                return
            embed = discord.Embed(
                title=f"Loot · {player}", color=DEFAULT_EMBED_COLOR,
                description="\n".join(
                    f"`{(e.awarded_at or '')[:10]}` {e.item_name or e.item_id} "
                    f"({e.response or '–'}) · {e.boss or '–'}"
                    for e in entries
                ),
            )
            await interaction.followup.send(embed=embed)
            return

        r = await views.loot_overview(self.bot.db, self.cfg)
        top = [f for f in r["fairness"] if f["items_won"]][:15]
        embed = discord.Embed(
            title=f"Loot fairness · {r['total_awards']} awards",
            color=DEFAULT_EMBED_COLOR,
            description="\n".join(
                f"**{f['name']}** {f['items_won']} item(s) · {_fmt(f['items_per_raid'])}/raid "
                f"· att {_pct(f['attendance_pct'])}"
                for f in top
            )
            or "No loot recorded yet.",
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="wishlist", description="Show a player's BiS wishlist progress.")
    @app_commands.describe(player="Character name")
    async def wishlist(self, interaction: discord.Interaction, player: str) -> None:
        await interaction.response.defer(thinking=True)
        items = await self.bot.db.get_wishlist(player)
        if not items:
            await interaction.followup.send(
                f"No wishlist for **{player}**.", ephemeral=True
            )
            return
        got, total, pct = await self.bot.db.wishlist_progress(player)
        lines = [
            f"{'✅' if w.obtained else '⬜'} {w.item_name or w.item_id}"
            f"{f' (+{w.upgrade_pct:.1f}%)' if w.upgrade_pct else ''}"
            f"{' ⭐BiS' if w.is_bis else ''} · {w.boss or '–'}"
            for w in items[:20]
        ]
        embed = discord.Embed(
            title=f"Wishlist · {player} ({got}/{total}, {pct:.0f}%)",
            description="\n".join(lines),
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="lootpriority", description="Advisory loot-council priority for an item.")
    @app_commands.describe(item="Item name (matches wishlists)")
    async def lootpriority(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer(thinking=True)
        r = await views.loot_priority(self.bot.db, self.cfg, item_name=item)
        if not r["ranked"]:
            await interaction.followup.send(
                f"Nobody has **{item}** on their wishlist.", ephemeral=True
            )
            return
        lines = []
        for i, c in enumerate(r["ranked"][:12], start=1):
            tags = []
            if c["is_bis"]:
                tags.append("⭐BiS")
            if c["upgrade_pct"]:
                tags.append(f"+{c['upgrade_pct']:.1f}%")
            tags.append(f"att {_pct(c['attendance_pct'])}")
            if c["recent_wins"]:
                tags.append(f"{c['recent_wins']} recent win(s)")
            lines.append(
                f"`{i:>2}.` **{c['character']}** · {c['priority']:.2f} · " + " · ".join(tags)
            )
        embed = discord.Embed(
            title=f"Loot priority · {item}",
            description="\n".join(lines),
            color=DEFAULT_EMBED_COLOR,
        )
        embed.set_footer(text="Advisory only. The loot council decides.")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="trends", description="Guild trends over recent snapshots.")
    async def trends(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        t = await views.trends(self.bot.db, self.cfg)
        if t["count"] < 2:
            await interaction.followup.send(
                "Not enough history yet. Trends appear after a few syncs.",
                ephemeral=True,
            )
            return
        labels = {
            "health_score": "Health",
            "member_count": "Members",
            "active_count": "Active",
            "avg_item_level": "Avg ilvl",
            "avg_mythic_plus": "Avg M+",
        }
        lines = []
        for field, label in labels.items():
            known = [v for v in t["series"][field] if v is not None]
            if not known:
                continue
            delta = t["deltas"].get(field)
            arrow = ""
            if delta is not None and round(delta, 1) != 0:
                arrow = f" ({'▲' if delta >= 0 else '▼'}{abs(delta):.0f})"
            lines.append(f"**{label}**: {known[-1]:.0f}{arrow}")
        embed = discord.Embed(
            title=f"Guild trends · last {t['count']} snapshots",
            description="\n".join(lines),
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed)


def _fmt(value: object) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return str(value)


def _x100(value: float | None) -> float | None:
    return None if value is None else value * 100


async def setup(bot: "FtaBot") -> None:  # pragma: no cover - discord entrypoint
    await bot.add_cog(ReportsCog(bot))
