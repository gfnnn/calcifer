"""The Armoury: the guild's best-geared characters, as a living board.

The gear twin of the Hot 20. It ranks by equipped item level from the stored
roster (Raider.IO supplies it with no API key), shows rank movement since the
weekly reset, and does no network I/O of its own.

Only characters seen recently are ranked. That is not a nicety: item levels are
rescaled between expansions, so a character last seen a year ago still carries
a number from the old scale that would outrank everyone playing today.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand, metrics
from ..models import effective_role
from ._weekly import entity_key, weekly_baseline
from .base import Board, BoardPayload, register
from .hot20 import _movement

ARMOURY_TYPE = "armoury"
_TOP_N = 20
# How recently a character must have been seen to be ranked (see module doc).
FRESH_DAYS = 30


def _ilvl(value: float | None) -> str:
    return f"{value:.0f}" if value else "0"


class ArmouryBoard(Board):
    board_type = ARMOURY_TYPE

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        members = await db.all_members(order_by="item_level")
        geared = [
            m for m in members
            if (m.item_level or 0) > 0 and metrics.is_active(m, window_days=FRESH_DAYS)
        ]
        ranked = geared[:_TOP_N]
        if not ranked:
            return None  # nobody recently seen has gear data yet; post nothing

        ranking = {entity_key(m): i for i, m in enumerate(ranked, 1)}
        baseline = await weekly_baseline(db, config, ARMOURY_TYPE, ranking)

        lines: list[str] = []
        for i, member in enumerate(ranked, 1):
            move = _movement(baseline.get(entity_key(member)), i)
            role = effective_role(member.role, member.active_spec)
            glyph = brand.role_icon(role, emoji) or "•"
            klass = brand.class_label(member.class_name, emoji)
            suffix = f" {move}" if move else ""
            lines.append(
                f"`{i:>2}` {glyph} **{member.character_name}** "
                f"· {klass} · `{_ilvl(member.item_level)}`{suffix}"
            )

        leader = ranked[0]
        average = sum(m.item_level for m in geared) / len(geared)
        header = [
            brand.sanitize(
                f"🛡️ Best dressed: **{leader.character_name}** "
                f"at `{_ilvl(leader.item_level)}`."
            ),
            brand.sanitize(
                f"Guild average across {len(geared)} active characters: "
                f"`{_ilvl(average)}`."
            ),
        ]
        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed = discord.Embed(
            title="🛡️ The Armoury",
            description="\n".join(header) + "\n\n" + "\n".join(lines),
            color=brand.GUILD_EMBER,
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} Equipped item level."))
        embed.timestamp = datetime.now(timezone.utc)

        material = [
            (i, m.character_name, round(m.item_level or 0),
             _movement(baseline.get(entity_key(m)), i))
            for i, m in enumerate(ranked, 1)
        ] + [round(average), len(geared)]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(ArmouryBoard())
