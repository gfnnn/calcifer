"""Roster Pulse — the guild's vital signs as a quiet, always-current board.

`/stats` made ambient (design-system §4a): member count, how many are active,
average item level and Mythic+, the role split, current progression, and the
composite health score, rendered from the stored roster with no network I/O of
its own. Calcifer's own copy obeys the no-em-dash house rule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand, metrics
from .base import Board, BoardPayload, register

PULSE_TYPE = "roster_pulse"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "–"


class RosterPulseBoard(Board):
    board_type = PULSE_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        members = await db.all_members()
        if not members:
            return None

        total = len(members)
        rate = metrics.activity_rate(members, window_days=config.activity_window_days)
        active = round(rate * total) if rate is not None else 0
        active_pct = round(rate * 100) if rate is not None else 0
        avg_ilvl = _avg([m.item_level for m in members if m.item_level is not None])
        avg_mplus = _avg(
            [m.mythic_plus_score for m in members if m.mythic_plus_score is not None]
        )
        roles = metrics.role_counts(members)
        progression, _ = metrics.guild_progression(members)
        health = metrics.guild_health(
            members, window_days=config.activity_window_days
        ).score

        role_line = " · ".join(
            f"{brand.icon(r.lower()) or r.title()} {roles[r]}"
            for r in ("TANK", "HEALER", "DPS")
            if roles.get(r)
        )

        embed = discord.Embed(title="🔥 Roster pulse", color=brand.GUILD_EMBER)
        embed.add_field(name="Members", value=str(total))
        embed.add_field(name="Active", value=f"{active} ({active_pct}%)")
        embed.add_field(name="Health", value=f"{_fmt(health)}/100")
        embed.add_field(name="Avg ilvl", value=_fmt(avg_ilvl))
        embed.add_field(name="Avg M+", value=_fmt(avg_mplus))
        if role_line:
            embed.add_field(name="Roles", value=role_line, inline=False)
        if progression:
            embed.add_field(name="Progression", value=brand.sanitize(progression),
                            inline=False)

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} The guild's vital signs."))
        embed.timestamp = datetime.now(timezone.utc)

        # Fingerprint the material values (rounded), so the board edits when the
        # guild actually moves, not on every refresh tick.
        material = [
            total, active, active_pct,
            round(avg_ilvl) if avg_ilvl is not None else None,
            round(avg_mplus) if avg_mplus is not None else None,
            round(health) if health is not None else None,
            {r: roles.get(r) for r in ("TANK", "HEALER", "DPS")},
            progression,
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(RosterPulseBoard())
