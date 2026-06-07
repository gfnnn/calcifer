"""Rules & Info — a managed post whose facts never drift stale.

The chronic problem with a hand-written rules/info post is that the live facts
(progression, recruiting status, member count) rot the moment they change. Here
the hand-written prose lives in ``ftabot/content/rules.json`` (GM-editable) with
``{tokens}`` for the live bits, and this board substitutes the current values
from the DB on every refresh. So the prose stays the GM's, but the facts are
always current.
"""

from __future__ import annotations

import hashlib
import json

import discord

from .. import brand, content, metrics
from .base import Board, BoardPayload, register

RULES_TYPE = "rules_info"


class _SafeDict(dict):
    """Leaves an unknown ``{token}`` visible instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


async def _live_tokens(db, config) -> dict[str, str]:
    members = await db.all_members()
    rate = metrics.activity_rate(members, window_days=config.activity_window_days)
    progression, _ = metrics.guild_progression(members)
    roles = [r.title() for r in config.recruit_roles_open]
    return {
        "member_count": str(len(members)),
        "active_pct": f"{round(rate * 100)}%" if rate is not None else "lots",
        "progression": progression or "early days yet",
        "recruiting": ", ".join(roles) if roles else "currently closed",
    }


class RulesInfoBoard(Board):
    board_type = RULES_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        data = board_config.get("content") or content.load("rules")
        sections = data.get("sections") or []
        if not sections:
            return None

        tokens = _SafeDict(await _live_tokens(db, config))
        blocks: list[str] = []
        material: list[str] = []  # the substituted text, for the fingerprint
        for s in sections:
            heading = brand.sanitize(str(s.get("heading") or "")).strip()
            body = brand.sanitize(str(s.get("body") or "").format_map(tokens))
            if not body:
                continue
            blocks.append(f"**{heading}**\n{body}" if heading else body)
            material.append(body)
        if not blocks:
            return None

        title = brand.sanitize(data.get("title") or "Rules & Info")
        intro = brand.sanitize(data.get("intro") or "")
        description = (f"{intro}\n\n" if intro else "") + "\n\n".join(blocks)

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )
        embed = discord.Embed(
            title=f"📜 {title}", description=description, color=brand.GUILD_EMBER
        )
        embed.set_footer(text=brand.sanitize(signoff))

        # Fingerprint the *substituted* text, so the board re-edits exactly when a
        # live fact (progression, member count, recruiting) changes, never just to
        # bump a timestamp.
        fingerprint = hashlib.sha1(
            json.dumps([title, intro, material], ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(RulesInfoBoard())
