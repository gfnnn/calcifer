"""Calcifer's Hot 20 — the guild's top 20 by Mythic+ score, as a living board.

Renders straight from the stored roster (the sync populates the scores), so it
does no network I/O of its own. Rank movement (▲/▼) is shown against a baseline
that rolls roughly weekly, so the arrows mean "since last reset". Bright, fast
voice to match the M+ domain (design-system §5c); never an em dash.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand
from .base import Board, BoardPayload, register

HOT20_TYPE = "hot20"
_TOP_N = 20
_ROLL_AFTER_DAYS = 7


def _entity_key(member) -> str:
    return f"{member.character_name}|{member.realm_slug}|{member.region}".lower()


def _age_days(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 1e9
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0


def _movement(prev_rank: int | None, cur_rank: int) -> str:
    """A rank-change marker with magnitude (▲7 / ▼3); NEW for entrants, blank
    when unchanged. Naturally bounded: anyone outside last week's board is NEW,
    not a giant number, so a re-entry never shows an absurd jump."""
    if prev_rank is None:
        return "NEW"
    delta = prev_rank - cur_rank
    if delta > 0:
        return f"▲{delta}"
    if delta < 0:
        return f"▼{-delta}"
    return ""


def _score(value) -> str:
    return f"{value:.0f}" if value else "0"


class Hot20Board(Board):
    board_type = HOT20_TYPE

    async def render(self, db, config, board_config) -> BoardPayload | None:
        members = await db.all_members(order_by="mythic_plus_score")
        ranked = [m for m in members if (m.mythic_plus_score or 0) > 0][:_TOP_N]
        if not ranked:
            return None  # nothing scored yet; post nothing

        baseline, captured_at = await db.board_baseline(HOT20_TYPE)
        ranking = {_entity_key(m): i for i, m in enumerate(ranked, 1)}

        # First ever render: seed the baseline now so the opening post is a
        # settled board (no "NEW" stamped against every single row). Genuine
        # newcomers show NEW in later weeks, once a baseline exists to compare to.
        seeded_now = False
        if not baseline:
            await db.roll_board_baseline(HOT20_TYPE, ranking)
            baseline = ranking
            seeded_now = True

        lines: list[str] = []
        for i, member in enumerate(ranked, 1):
            move = _movement(baseline.get(_entity_key(member)), i)
            glyph = brand.icon((member.role or "").lower()) or "•"
            klass = member.class_name or "?"
            suffix = f" {move}" if move else ""
            lines.append(
                f"`{i:>2}` {glyph} **{member.character_name}** "
                f"· {klass} · `{_score(member.mythic_plus_score)}`{suffix}"
            )

        leader = ranked[0]
        header = [
            brand.sanitize(
                f"🔥 Topping the embers: **{leader.character_name}** "
                f"at `{_score(leader.mythic_plus_score)}`."
            )
        ]
        # Celebrate the week's biggest climber to stoke the competition. Only a
        # real move (≥2 ranks) earns the callout, so it stays meaningful.
        climbs = [
            (baseline[_entity_key(m)] - i, m)
            for i, m in enumerate(ranked, 1)
            if _entity_key(m) in baseline and baseline[_entity_key(m)] - i >= 2
        ]
        if climbs:
            delta, mover = max(climbs, key=lambda pair: pair[0])
            header.append(
                brand.sanitize(
                    f"📈 Flying up the ranks: **{mover.character_name}** "
                    f"(▲{delta} this week)."
                )
            )
        signoff = brand.Voice(tone=config.persona_tone).line("board_signoff") or "Kept warm by Calcifer."

        embed = discord.Embed(
            title="🔥 Calcifer's Hot 20",
            description="\n".join(header) + "\n\n" + "\n".join(lines),
            color=brand.GUILD_EMBER,
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} The guild's fiercest keys."))
        embed.timestamp = datetime.now(timezone.utc)

        # Fingerprint only the meaningful content (rank/name/score/movement), so an
        # unchanged board is never re-edited just to bump the relative timestamp.
        material = [
            (i, m.character_name, round(m.mythic_plus_score or 0, 1),
             _movement(baseline.get(_entity_key(m)), i))
            for i, m in enumerate(ranked, 1)
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False).encode()
        ).hexdigest()

        # Weekly reset: roll the baseline forward about once a week, so the arrows
        # always mean "movement since the last reset" (skip if we just seeded it).
        if not seeded_now and _age_days(captured_at) >= _ROLL_AFTER_DAYS:
            await db.roll_board_baseline(HOT20_TYPE, ranking)

        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(Hot20Board())
