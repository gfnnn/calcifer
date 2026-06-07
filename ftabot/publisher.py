"""Render a FeedEvent into a branded Discord embed in Calcifer's voice."""

from __future__ import annotations

import random
from datetime import datetime

import discord

from . import brand, persona
from .feeds.base import FeedEvent
from .router import RouteDecision

# Named colours a rule's style may request, on top of severity.
_STYLE_COLORS = {
    "blue": persona.BLUE_FLAME,
    "amber": persona.AMBER,
    "red": persona.EMBER_RED,
    "ember": persona.GUILD_EMBER,
}


def _color(event: FeedEvent, style: dict) -> int:
    named = style.get("color")
    if named in _STYLE_COLORS:
        return _STYLE_COLORS[named]
    return persona.color_for(event.severity)


def render_embed(
    event: FeedEvent,
    decision: RouteDecision,
    *,
    tone: str = "light",
    rng: random.Random | None = None,
) -> discord.Embed:
    """Build the embed for a feed event, honouring the rule's style + persona."""
    style = decision.style or {}
    description_parts: list[str] = []

    if style.get("persona", True):
        # The flavour line is copy Calcifer authors, so it honours the house
        # style (never an em dash); the relayed title/summary are left untouched.
        line = brand.sanitize(persona.flavour(event.event_type, tone=tone, rng=rng))
        if line:
            # Calcifer's own garnish always obeys the house style.
            description_parts.append(f"*{brand.sanitize(line)}*")
    # Calcifer-authored copy (titles he writes, his own summaries) is held to
    # the no-em-dash house style; relayed external text (a Wowhead headline) is
    # left exactly as the source wrote it.
    title = brand.sanitize(event.title) if event.authored else event.title
    summary = (
        brand.sanitize(event.summary)
        if (event.authored and event.summary)
        else event.summary
    )
    if summary:
        description_parts.append(summary)

    embed = discord.Embed(
        title=(title or "")[:256],
        url=event.url or None,
        description="\n\n".join(description_parts) or None,
        color=_color(event, style),
    )
    if event.image_url:
        embed.set_thumbnail(url=event.image_url)
    if event.categories:
        embed.set_footer(text=f"{event.source} · {', '.join(event.categories[:3])}")
    else:
        embed.set_footer(text=event.source)
    if event.published_at:
        try:
            embed.timestamp = datetime.fromisoformat(event.published_at)
        except ValueError:
            pass
    return embed


def render_digest_embed(
    events: list,
    style: dict,
    *,
    tone: str = "light",
    rng: random.Random | None = None,
) -> discord.Embed:
    """Bundle multiple events into a single weekly-roundup embed."""
    if not events:
        return discord.Embed(title="Nothing queued this cycle.", color=persona.BLUE_FLAME)

    sources = {e.source for e in events}
    types = {e.event_type for e in events}

    if sources <= {"raiderio_mplus"} and types <= {"raiderio.mplus-log", "raiderio.mplus-run"}:
        title = f"🔑 Keys this week ({len(events)} run{'s' if len(events) != 1 else ''})"
    elif sources <= {"wowhead"}:
        title = f"📰 WoW news this week ({len(events)} item{'s' if len(events) != 1 else ''})"
    else:
        title = f"📬 Weekly roundup ({len(events)} item{'s' if len(events) != 1 else ''})"

    color = _STYLE_COLORS.get(style.get("color", ""), persona.BLUE_FLAME)
    embed = discord.Embed(title=title, color=color)

    lines: list[str] = []
    for event in events[:20]:
        text = brand.sanitize(event.title) if event.authored else event.title
        lines.append(f"· [{text}]({event.url})" if event.url else f"· {text}")
    if len(events) > 20:
        lines.append(f"*…and {len(events) - 20} more.*")
    embed.description = "\n".join(lines) or None

    signoff = persona.flavour("digest", tone=tone, rng=rng)
    if signoff:
        embed.set_footer(text=brand.sanitize(signoff))

    return embed
