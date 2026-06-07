"""Helpers for rendering Member data into Discord embeds and lines."""

from __future__ import annotations

import discord

from . import brand
from .models import Member, class_color

# Discord embed limits — interpolated roster/officer data (notes, raid strings,
# user-supplied search terms) must be clamped so a long value never raises an
# HTTP 400 that drops the whole reply.
TITLE_LIMIT = 256
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
DESC_LIMIT = 4096


def clip(text: object, limit: int) -> str:
    """Truncate text to a Discord limit, appending an ellipsis when it cuts."""
    s = str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _field(embed: discord.Embed, name: object, value: object, *, inline: bool = True) -> None:
    """add_field that clamps both name and value to their Discord limits."""
    embed.add_field(
        name=clip(name, FIELD_NAME_LIMIT),
        value=clip(value, FIELD_VALUE_LIMIT),
        inline=inline,
    )


def _fmt(value: object, suffix: str = "") -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        value = f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return f"{value}{suffix}"


def member_embed(member: Member, *, include_private: bool = False) -> discord.Embed:
    """A detailed card for a single character.

    ``include_private`` reveals officer-owned fields (notes); leave it off for
    surfaces any guild member can reach so manual notes don't leak publicly.
    """
    spec_class = " ".join(
        p for p in (member.active_spec, member.class_name) if p
    ) or "Unknown class"
    embed = discord.Embed(
        title=clip(member.display(), TITLE_LIMIT),
        description=clip(spec_class, DESC_LIMIT),
        color=class_color(member.class_name),
        url=member.profile_url or None,
    )
    if member.thumbnail_url:
        embed.set_thumbnail(url=member.thumbnail_url)

    _field(embed, "Role", _fmt(member.role))
    _field(embed, "Item Level", _fmt(member.item_level))
    _field(embed, "M+ Score", _fmt(member.mythic_plus_score))

    if member.guild_rank is not None:
        _field(embed, "Guild Rank", _fmt(member.guild_rank))
    if member.race:
        _field(embed, "Race", member.race)
    if member.level is not None:
        _field(embed, "Level", _fmt(member.level))
    if member.best_parse_avg is not None:
        _field(embed, "Best Parse Avg", _fmt(member.best_parse_avg))
    if member.raid_progression:
        _field(embed, "Raid", member.raid_progression, inline=False)

    if member.discord_id:
        _field(embed, "Discord", f"<@{member.discord_id}>", inline=False)
    if include_private and member.notes:
        _field(embed, "Notes", member.notes, inline=False)

    footer = "Main" if member.is_main else "Alt"
    if member.last_synced:
        footer += f" • synced {member.last_synced}"
    embed.set_footer(text=footer)
    return embed


def member_line(member: Member, *, index: int | None = None) -> str:
    """A compact one-line summary for roster/search listings."""
    prefix = f"`{index:>2}.` " if index is not None else ""
    score = _fmt(member.mythic_plus_score)
    ilvl = _fmt(member.item_level)
    spec = member.active_spec or ""
    klass = member.class_name or "?"
    descriptor = f"{spec} {klass}".strip()
    return f"{prefix}**{member.character_name}** · {descriptor} · ilvl {ilvl} · M+ {score}"


def me_card(members: list[Member], *, display_name: str) -> discord.Embed:
    """A personal card: a member's linked characters' ilvl / M+ / vault / wishlist.

    Mains lead, then alts by Mythic+ score. Pure and offline-testable; the cog
    just hands it the characters linked to the invoking Discord user.
    """
    ordered = sorted(
        members, key=lambda m: (not m.is_main, -(m.mythic_plus_score or 0))
    )
    lead = ordered[0]
    embed = discord.Embed(
        title=clip(brand.sanitize(f"🔥 {display_name}'s characters"), TITLE_LIMIT),
        color=class_color(lead.class_name) or brand.GUILD_EMBER,
    )
    for m in ordered[:10]:  # Discord caps fields; nobody needs more on a card
        spec_class = " ".join(p for p in (m.active_spec, m.class_name) if p) or "Unknown"
        bits = [spec_class, f"ilvl {_fmt(m.item_level)}", f"M+ {_fmt(m.mythic_plus_score)}"]
        vault = f"{m.vault_slots_filled}/9" if m.vault_slots_filled is not None else "–"
        bits.append(f"vault {vault}")
        if m.wishlist_pct is not None:
            bits.append(f"wishlist {m.wishlist_pct:.0f}%")
        tag = "main" if m.is_main else "alt"
        _field(embed, f"{m.character_name} · {tag}", " · ".join(bits), inline=False)
    embed.set_footer(text="Linked by your officers. Mind the sparks.")
    return embed


def roster_embed(
    members: list[Member], *, title: str, color: int, per_page: int = 25
) -> discord.Embed:
    """A listing embed; truncates to ``per_page`` with a count footer."""
    embed = discord.Embed(title=clip(title, TITLE_LIMIT), color=color)
    shown = members[:per_page]
    if not shown:
        embed.description = "No matching members."
        return embed
    lines = [member_line(m, index=i + 1) for i, m in enumerate(shown)]
    embed.description = clip("\n".join(lines), DESC_LIMIT)
    if len(members) > per_page:
        embed.set_footer(text=f"Showing {per_page} of {len(members)} members.")
    else:
        embed.set_footer(text=f"{len(members)} member(s).")
    return embed
