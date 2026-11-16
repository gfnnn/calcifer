"""Front Door — how the welcome is going, for the welcome team.

A staff board for the private ``#front-door`` channel: joins by source, how
quickly newcomers are greeted and placed, who stayed, who left, what people
picked and which scouted leads walked in, over the last 7 and 30 days.

Everything is derived from the journey's append-only log by the pure
``journey.front_door`` view-model; this module only lays it out. Staff
surface, so the copy is plain. Needs ``JOURNEY_ENABLED`` (with it off, or
before the journey has ever run, the board posts nothing).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand, journey
from .base import Board, BoardPayload, register

FRONT_DOOR_TYPE = "front_door"

_LEADS_SHOWN = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rate(rate: journey.Rate) -> str:
    if rate.percent is None:
        return "nobody due yet"
    return f"`{rate.percent}%` ({rate.done} of {rate.of})"


def _counts(pairs) -> str:
    return " · ".join(
        f"{discord.utils.escape_markdown(name)} `{n}`" for name, n in pairs
    )


def _window_lines(window: journey.Window, show_picked: bool) -> list[str]:
    joined = f"Joined: `{window.joins}`"
    if window.by_source:
        joined += f" ({_counts(window.by_source)})"
    lines = [
        f"**Last {window.days} days**",
        joined,
        f"Greeted within a day: {_rate(window.greeted)}",
        f"Placed within two days: {_rate(window.placed)}",
        f"Left: `{window.leavers}`",
    ]
    if show_picked:
        lines.append(f"Picked: {_counts(window.picked) or 'nothing yet'}")
    if window.leads:
        names = [
            f"**{discord.utils.escape_markdown(n)}**" for n in window.leads[:_LEADS_SHOWN]
        ]
        extra = len(window.leads) - _LEADS_SHOWN
        tail = f" and {extra} more" if extra > 0 else ""
        lines.append(f"Scout leads who joined: {', '.join(names)}{tail}")
    return lines


class FrontDoorBoard(Board):
    board_type = FRONT_DOOR_TYPE

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        if not config.journey_enabled or not await db.journey_baseline_at():
            return None  # the journey is off, or has never run: nothing to report
        stats = journey.front_door(
            await db.journey_events(),
            _now(),
            labels=await journey.invite_labels(db, config),
            rank_roles=config.journey_rank_roles,
            interest_roles=config.journey_interest_roles,
        )
        show_picked = bool(config.journey_interest_roles)
        lines: list[str] = []
        for window in stats.windows:
            lines += [*_window_lines(window, show_picked), ""]
        lines.append(
            f"**Still here after 30 days**: {_rate(stats.still_here)}"
            + (" of those who joined 30 to 37 days ago" if stats.still_here.of else "")
        )

        embed = discord.Embed(
            title="🚪 Front door",
            description="\n".join(lines)[:4096],
            color=brand.GUILD_EMBER,
        )
        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} How the welcome is going."))
        embed.timestamp = datetime.now(timezone.utc)

        # The rendered lines are the material content: numbers and names only.
        fingerprint = hashlib.sha1(
            json.dumps(lines, ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(FrontDoorBoard())
