"""Guides & Resources — the guild's links, tidied into one managed post.

Replaces a channel that is a wall of raw links with a single board: a clean,
sectioned grid of masked links that Calcifer owns and keeps current. The content
is GM-editable in ``ftabot/content/guides.json`` (or overridden inline via the
board config); this renderer just lays it out, so it does no I/O of its own
beyond loading that bundled file.
"""

from __future__ import annotations

import hashlib
import json

import discord

from .. import brand, content
from .base import Board, BoardPayload, register

GUIDES_TYPE = "guides"


def _section_block(section: dict) -> str | None:
    name = brand.sanitize(str(section.get("name") or "")).strip()
    emoji = str(section.get("emoji") or "").strip()
    links = [
        f"[{brand.sanitize(str(link['label']))}]({link['url']})"
        for link in (section.get("links") or [])
        if isinstance(link, dict) and link.get("label") and link.get("url")
    ]
    if not links:
        return None
    head = f"{emoji} **{name}**".strip()
    return f"{head}\n{' · '.join(links)}"


class GuidesBoard(Board):
    board_type = GUIDES_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        data = board_config.get("content") or content.load("guides")
        sections = data.get("sections") or []
        blocks = [b for s in sections if (b := _section_block(s))]
        if not blocks:
            return None

        title = brand.sanitize(data.get("title") or "Guides & Resources")
        intro = brand.sanitize(data.get("intro") or "")
        body = (f"{intro}\n\n" if intro else "") + "\n\n".join(blocks)

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed = discord.Embed(
            title=f"📚 {title}", description=body, color=brand.GUILD_EMBER
        )
        embed.set_footer(text=brand.sanitize(signoff))

        # Static content, so the board only edits when the file actually changes.
        fingerprint = hashlib.sha1(
            json.dumps([title, intro, blocks], ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(GuidesBoard())
