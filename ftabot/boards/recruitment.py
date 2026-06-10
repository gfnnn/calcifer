"""Recruitment — "We're Looking", the guild's open front door.

Replaces a hand-edited recruitment post that rots the moment a slot fills. The
open slots, trial rules and pitch override are officer-configured via the
``/recruit`` commands (stored in the ``recruitment_config`` table); the cultural
prose (what we value, about us, the call to action) is GM-editable in
``ftabot/content/recruitment.json`` with the same live ``{tokens}`` the Rules
board substitutes. Culture first, then the ask: values render above the slot
requirements, per the sticky-post spec.
"""

from __future__ import annotations

import hashlib
import json

import discord

from .. import brand, content
from .base import Board, BoardPayload, register
from .rules import SafeDict, live_tokens

RECRUITMENT_TYPE = "recruitment"

# Render order for the role slots (matches the icon keys in brand.ICONS).
ROLE_ORDER = ("tank", "healer", "dps")


def _slot_line(role: str, slot: dict) -> str | None:
    """One open-slot row, or ``None`` when the role has no openings."""
    try:
        open_n = int(slot.get("open") or 0)
    except (TypeError, ValueError):
        open_n = 0
    if open_n <= 0:
        return None
    bits = [f"{open_n} slot{'s' if open_n != 1 else ''} open"]
    ilvl = slot.get("ilvl")
    if ilvl:
        bits.append(f"ilvl {int(ilvl)}+")
    note = brand.sanitize(str(slot.get("note") or "")).strip()
    if note:
        bits.append(note)
    return f"{brand.icon(role)} **{role.upper()}**: {' · '.join(bits)}"


class RecruitmentBoard(Board):
    board_type = RECRUITMENT_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        data = board_config.get("content") or content.load("recruitment")
        state = await db.recruitment_state()
        tokens = SafeDict(await live_tokens(db, config))
        voice = brand.Voice(tone=config.persona_tone)

        slots = state.get("slots") or {}
        slot_lines = [
            line for role in ROLE_ORDER if (line := _slot_line(role, slots.get(role) or {}))
        ]

        about = brand.sanitize(
            str(state.get("message") or data.get("about") or "").format_map(tokens)
        ).strip()
        about_heading = brand.sanitize(str(data.get("about_heading") or "About us"))

        blocks: list[str] = []
        if slot_lines:
            blocks.append("\n".join(slot_lines))

            values = [
                v for raw in (data.get("values") or []) if (v := brand.sanitize(str(raw)).strip())
            ]
            if values:
                heading = brand.sanitize(str(data.get("values_heading") or "What we value"))
                blocks.append(f"**{heading}**\n" + "\n".join(values))

            # Officer-set trial rules win; the bundled expectations are the default.
            rules = [
                r
                for raw in (state.get("rules") or data.get("expectations") or [])
                if (r := brand.sanitize(str(raw)).strip())
            ]
            if rules:
                heading = brand.sanitize(str(data.get("expectations_heading") or "Trial expectations"))
                blocks.append(f"**{heading}**\n" + "\n".join(f"→ {r}" for r in rules))

            if about:
                blocks.append(f"**{about_heading}**\n{about}")

            cta = brand.sanitize(str(data.get("cta") or "").format_map(tokens)).strip()
            if cta:
                blocks.append(cta)
        else:
            # No openings: stay warm, keep the pitch visible.
            empty = brand.sanitize(str(data.get("empty") or "").format_map(tokens)).strip()
            blocks.append(empty or "The guild's full for now. Always happy to hear from you.")
            if about:
                blocks.append(f"**{about_heading}**\n{about}")

        # Flavour is garnish only: excluded from the fingerprint so a random
        # line never forces a re-edit of an otherwise unchanged board.
        flavour = voice.flavour("recruiting" if slot_lines else "empty_recruiting")
        if flavour:
            blocks.append(f"*{flavour}*")

        title = brand.sanitize(str(data.get("title") or "We're Looking"))
        signoff = voice.line("board_signoff") or "Kept warm by Calcifer."
        embed = discord.Embed(
            title=f"{brand.icon('recruit')} {title}",
            description="\n\n".join(blocks),
            color=brand.GUILD_EMBER,
        )
        embed.set_footer(text=brand.sanitize(signoff))

        material = [b for b in blocks if not flavour or b != f"*{flavour}*"]
        fingerprint = hashlib.sha1(
            json.dumps([title, material], ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(RecruitmentBoard())
