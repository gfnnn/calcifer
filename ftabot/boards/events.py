"""Weekly Events — the week's Raid-Helper events as one living page.

Replaces a channel of scrolling Raid-Helper posts with a single board that edits
itself in place: each event shows its time (as a Discord stamp that localises to
every viewer), the sign-up count, and a jump-link back to the Raid-Helper post to
sign up. Sign-ups themselves stay in Raid-Helper; this is read-only.

Renders straight from the ``calendar_events`` table the webhook receiver keeps
current, so the board does no network I/O of its own. Event titles come from
Raid-Helper (relayed) and are shown verbatim; only Calcifer's own framing obeys
the no-em-dash house rule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand
from .base import Board, BoardPayload, register

EVENTS_TYPE = "weekly_events"
_DEFAULT_WITHIN_DAYS = 7
_DEFAULT_LIMIT = 10


def _clean(title: str | None) -> str:
    return " ".join((title or "Event").split()).replace("]", ")").strip() or "Event"


def _event_line(event: dict) -> str:
    title = _clean(event.get("title"))
    head = f"[{title}]({event['url']})" if event.get("url") else f"**{title}**"
    ts = event.get("start_epoch")
    when = f" · <t:{ts}:F> (<t:{ts}:R>)" if ts else ""
    n = event.get("signup_count")
    signups = f" · {n} signed up" if n is not None else ""
    tail = " · [Sign up ➜](" + event["url"] + ")" if event.get("url") else ""
    return f"📅 {head}{when}{signups}{tail}"


class WeeklyEventsBoard(Board):
    board_type = EVENTS_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        within = float(board_config.get("within_days", _DEFAULT_WITHIN_DAYS))
        limit = int(board_config.get("limit", _DEFAULT_LIMIT))
        now_epoch = int(datetime.now(timezone.utc).timestamp())

        events = await db.upcoming_events(
            now_epoch=now_epoch, within_days=within, limit=limit
        )
        if not events:
            return None  # nothing scheduled this week; post nothing

        lines = [_event_line(e) for e in events]
        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )

        embed = discord.Embed(
            title="📅 This week at the hearth",
            description="\n\n".join(lines),
            color=brand.GUILD_EMBER,  # our own community data
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} Sign-ups live in Raid-Helper."))
        embed.timestamp = datetime.now(timezone.utc)

        # Fingerprint the displayed set + the fields that should trigger an edit
        # (time + sign-up count), so the board updates as events fill but not just
        # to bump a relative timestamp.
        material = [
            (e["event_id"], e.get("start_epoch"), e.get("signup_count"))
            for e in events
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(WeeklyEventsBoard())
