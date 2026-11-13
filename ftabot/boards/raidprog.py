"""Raid Progress: where the guild stands in the current tier, as a living board.

Renders from the ``guild_raids`` table the sync fills from Raider.IO (the guild
profile's kill counts and standings, labelled against the raid catalog), so it
needs no API key and does no network I/O of its own. Each raid that is open
right now gets a bar per difficulty and the guild's realm/region/world standing
on the hardest difficulty it has a kill on; earlier raids of the expansion
collapse into one line. Calcifer's own copy obeys the no-em-dash house rule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand
from ..raids import DIFFICULTIES
from .base import Board, BoardPayload, register

RAIDPROG_TYPE = "raid_progress"

# One bar segment per boss, capped so a very long raid still fits on a phone.
_MAX_SEGMENTS = 12
_DIFFICULTY_NAMES = {"normal": "Normal", "heroic": "Heroic", "mythic": "Mythic"}


def _bar(killed: int, total: int) -> str:
    """A filled/empty segment bar, e.g. ``▰▰▰▱▱▱▱▱`` for 3/8."""
    if total <= 0:
        return ""
    segments = min(total, _MAX_SEGMENTS)
    filled = round(segments * min(killed, total) / total)
    return "▰" * filled + "▱" * (segments - filled)


def _best(raid: dict) -> str | None:
    """The hardest difficulty this raid has a kill on (``mythic``/...)."""
    for key, _letter in reversed(DIFFICULTIES):
        if raid.get(f"{key}_killed"):
            return key
    return None


def _standing(raid: dict, region: str) -> str | None:
    """The guild's standing on its best difficulty, e.g. ``Heroic standing:
    realm #17 · EU #2,398 · world #6,313``. ``None`` when unranked."""
    best = _best(raid)
    ranks = (raid.get("ranks") or {}).get(best) if best else None
    if not isinstance(ranks, dict):
        return None
    parts = [
        f"{label} #{int(ranks[key]):,}"
        for key, label in (("realm", "realm"), ("region", region.upper()), ("world", "world"))
        if isinstance(ranks.get(key), (int, float)) and ranks[key] > 0
    ]
    if not parts:
        return None
    return f"{_DIFFICULTY_NAMES[best]} standing: {' · '.join(parts)}"


def _raid_block(raid: dict, region: str) -> str:
    total = raid["total_bosses"]
    lines = [f"**{raid['name']}**"]
    for key, letter in DIFFICULTIES:
        killed = raid[f"{key}_killed"]
        lines.append(f"`{letter}` {_bar(killed, total)} `{killed}/{total}`")
    standing = _standing(raid, region)
    if standing:
        lines.append(f"*{standing}*")
    return "\n".join(lines)


def _headline(raid: dict, guild_name: str) -> str:
    """The one-line state of the headline raid, in Calcifer's voice."""
    best = _best(raid)
    if best is None:
        return f"🏰 **{raid['name']}** awaits. No bosses down yet."
    killed, total = raid[f"{best}_killed"], raid["total_bosses"]
    difficulty = _DIFFICULTY_NAMES[best]
    if total and killed >= total:
        return f"🏆 **{raid['name']}** is cleared on {difficulty} ({killed}/{total})."
    return (
        f"🔥 {guild_name} are **{killed}/{total} {difficulty}** "
        f"in **{raid['name']}**."
    )


class RaidProgressBoard(Board):
    board_type = RAIDPROG_TYPE

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        raids = await db.guild_raids()
        if not raids:
            return None  # no sync has stored progress yet; post nothing

        current = [r for r in raids if r["is_current"]] or raids
        earlier = [r for r in raids if r not in current]

        parts = [brand.sanitize(_headline(current[0], config.guild_name))]
        parts.extend(_raid_block(raid, config.guild_region) for raid in current)
        if earlier:
            recap = " · ".join(
                f"{r['name']} {r['summary']}" for r in earlier if r.get("summary")
            )
            if recap:
                parts.append(f"*Earlier this expansion: {recap}*")

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed = discord.Embed(
            title="🏰 Raid progress",
            description="\n\n".join(parts)[:4096],
            color=brand.GUILD_EMBER,
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} Standings by Raider.IO."))
        embed.timestamp = datetime.now(timezone.utc)

        # Fingerprint the kill counts and standings only, so the board edits on
        # a kill or a rank change, never just to bump the timestamp.
        material = [
            (
                r["raid_slug"], r["is_current"], r["total_bosses"],
                r["normal_killed"], r["heroic_killed"], r["mythic_killed"],
                r.get("summary"), r.get("ranks"),
            )
            for r in raids
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(RaidProgressBoard())
