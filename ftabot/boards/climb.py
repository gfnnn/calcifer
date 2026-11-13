"""The Climb: who is levelling, and who just reached the cap, as a living board.

Renders from the stored roster's ``level``. Character level is the one piece of
this bot's public data that Raider.IO does not expose: it comes from the
Blizzard API (free client credentials, ``BLIZZARD_CLIENT_ID`` / ``_SECRET``), so
the board posts nothing until a sync has run with those set.

The weekly baseline holds each character's level at the last reset, which gives
both halves of the board: levels gained this week for those still climbing, and
the roll of honour for anyone who reached the cap since the reset.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand
from ..models import MAX_LEVEL
from ._weekly import entity_key, weekly_baseline
from .base import Board, BoardPayload, register

CLIMB_TYPE = "the_climb"
_TOP_N = 15
_FRESH_CAP_N = 10


def _gain(previous: int | None, level: int) -> str:
    """Levels gained since the baseline (``▲3``); NEW for a fresh face."""
    if previous is None:
        return "NEW"
    return f"▲{level - previous}" if level > previous else ""


class ClimbBoard(Board):
    board_type = CLIMB_TYPE

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        members = [m for m in await db.all_members() if m.level]
        if not members:
            return None  # no level data (needs the Blizzard API); post nothing

        levels = {entity_key(m): int(m.level) for m in members}
        baseline = await weekly_baseline(db, config, CLIMB_TYPE, levels)

        climbing = sorted(
            (m for m in members if m.level < MAX_LEVEL),
            key=lambda m: (-m.level, m.character_name.lower()),
        )
        # Reached the cap since the reset: at the cap now, below it at baseline.
        fresh = sorted(
            (
                m for m in members
                if m.level >= MAX_LEVEL
                and (baseline.get(entity_key(m)) or MAX_LEVEL) < MAX_LEVEL
            ),
            key=lambda m: m.character_name.lower(),
        )

        parts: list[str] = []
        if climbing:
            noun = "adventurer is" if len(climbing) == 1 else "adventurers are"
            parts.append(
                brand.sanitize(
                    f"🌱 **{len(climbing)}** {noun} on the road to {MAX_LEVEL}."
                )
            )
            rows = []
            for i, member in enumerate(climbing[:_TOP_N], 1):
                gain = _gain(baseline.get(entity_key(member)), member.level)
                klass = brand.class_label(member.class_name, emoji)
                suffix = f" {gain}" if gain else ""
                rows.append(
                    f"`{i:>2}` **{member.character_name}** "
                    f"· {klass} · `Lv {member.level}`{suffix}"
                )
            if len(climbing) > _TOP_N:
                rows.append(f"*…and {len(climbing) - _TOP_N} more on the way.*")
            parts.append("\n".join(rows))
        else:
            parts.append(
                brand.sanitize(
                    f"🌱 Everyone is at level {MAX_LEVEL}. "
                    "Roll an alt and I will keep the fire warm for it."
                )
            )
        if fresh:
            names = ", ".join(f"**{m.character_name}**" for m in fresh[:_FRESH_CAP_N])
            extra = len(fresh) - _FRESH_CAP_N
            if extra > 0:
                names += f" and {extra} more"
            parts.append(
                brand.sanitize(f"🎉 Fresh at {MAX_LEVEL} this week: {names}.")
            )

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed = discord.Embed(
            title="🌱 The Climb",
            description="\n\n".join(parts)[:4096],
            color=brand.GUILD_EMBER,
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} Levels gained since reset."))
        embed.timestamp = datetime.now(timezone.utc)

        material = [
            [(m.character_name, m.level, _gain(baseline.get(entity_key(m)), m.level))
             for m in climbing[:_TOP_N]],
            len(climbing),
            [m.character_name for m in fresh],
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(ClimbBoard())
