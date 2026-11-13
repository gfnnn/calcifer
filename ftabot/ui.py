"""Helpers for rendering Member data into Discord embeds and lines."""

from __future__ import annotations

import discord

from . import brand
from .models import MAX_LEVEL, Member, class_color, effective_role

# Pretty names for the current tier's raid slugs (Raider.IO keys raid_progression
# by slug). Anything not listed falls back to a title-cased slug, so a new raid
# still renders sensibly without a code change.
RAID_DISPLAY_NAMES: dict[str, str] = {
    "the-voidspire": "The Voidspire",
    "voidspire": "The Voidspire",
    "dreamrift": "Dreamrift",
    "the-dreamrift": "Dreamrift",
    "march-of-quel-dorei": "March of Quel'Dorei",
    "march-of-queldorei": "March of Quel'Dorei",
}

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


def _raid_progress_lines(
    progression: dict, names: dict[str, str] | None = None
) -> list[str]:
    """One line per raid from a Raider.IO ``raid_progression`` dict.

    The sync stores the dict with the open raids first and the raids' real
    names alongside (``names``, from Raider.IO's static data), so the season's
    raids render on top under their proper titles. Each line prefers the API's
    own ``summary`` and falls back to an N/H/M breakdown.
    """
    names = names or {}
    lines: list[str] = []
    for slug, raid in progression.items():
        if not isinstance(raid, dict):
            continue
        title = (
            names.get(slug)
            or RAID_DISPLAY_NAMES.get(slug)
            or slug.replace("-", " ").title()
        )
        summary = raid.get("summary")
        if summary:
            lines.append(f"**{title}**: {summary}")
            continue
        total = raid.get("total_bosses", 0)
        n = raid.get("normal_bosses_killed", 0)
        h = raid.get("heroic_bosses_killed", 0)
        m = raid.get("mythic_bosses_killed", 0)
        lines.append(f"**{title}**: {n}/{total} N, {h}/{total} H, {m}/{total} M")
    return lines


def member_embed(
    member: Member,
    *,
    include_private: bool = False,
    rank_name: str | None = None,
    max_level: int = MAX_LEVEL,
    emoji: dict[str, str] | None = None,
) -> discord.Embed:
    """A detailed card for a single character.

    ``include_private`` reveals officer-owned fields (notes); leave it off for
    surfaces any guild member can reach so manual notes don't leak publicly.
    ``rank_name`` is the guild's in-game rank name (from GRM identity) shown in
    place of the raw rank number when known.
    """
    spec_class = " ".join(
        p for p in (member.active_spec, member.class_name) if p
    ) or "Unknown class"
    # The class crest carries the class colour into the description (the accent
    # bar already uses class_color); it's "" when no crest art is supplied.
    crest = brand.class_icon(member.class_name, emoji)
    embed = discord.Embed(
        title=clip(member.display(), TITLE_LIMIT),
        description=clip(f"{crest} {spec_class}".strip(), DESC_LIMIT),
        color=class_color(member.class_name),
        url=member.profile_url or None,
    )
    if member.thumbnail_url:
        embed.set_thumbnail(url=member.thumbnail_url)

    # Role as a glyph rather than the word (custom :role*: emoji when uploaded,
    # else the unicode shield/heart/swords; falls back to the word if neither).
    # effective_role recovers the icon when the stored role is missing/garbled.
    role = effective_role(member.role, member.active_spec)
    role_glyph = brand.role_icon(role, emoji) or role
    _field(embed, "Role", role_glyph or "–")
    _field(embed, "Item Level", _fmt(member.item_level))
    # Level and M+ score share one slot: a sub-cap character is still levelling
    # (and has no current-season M+ score), so show the level; at the cap show
    # the M+ score instead, and only when they actually have one.
    if member.level is not None and member.level < max_level:
        _field(embed, "Level", _fmt(member.level))
    elif member.mythic_plus_score:
        _field(embed, "M+ Score", _fmt(member.mythic_plus_score))

    rank_display = rank_name or (
        _fmt(member.guild_rank) if member.guild_rank is not None else None
    )
    if rank_display:
        _field(embed, "Guild Rank", rank_display)
    if member.best_parse_avg is not None:
        _field(embed, "Best Parse Avg", _fmt(member.best_parse_avg))

    progression = (member.details or {}).get("raid_progression")
    raid_names = (member.details or {}).get("raid_names")
    raid_lines = (
        _raid_progress_lines(
            progression, raid_names if isinstance(raid_names, dict) else None
        )
        if isinstance(progression, dict)
        else []
    )
    if raid_lines:
        _field(embed, "Raid Progress", "\n".join(raid_lines), inline=False)
    elif member.raid_progression:
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


def member_line(
    member: Member, *, index: int | None = None, emoji: dict[str, str] | None = None
) -> str:
    """A compact one-line summary for roster/search listings."""
    prefix = f"`{index:>2}.` " if index is not None else ""
    score = _fmt(member.mythic_plus_score)
    ilvl = _fmt(member.item_level)
    spec = member.active_spec or ""
    # class_label prefixes the class with its colour crest when art is supplied,
    # else the bare name ("?" when unknown).
    descriptor = f"{spec} {brand.class_label(member.class_name, emoji)}".strip()
    return f"{prefix}**{member.character_name}** · {descriptor} · ilvl {ilvl} · M+ {score}"


def me_card(
    members: list[Member], *, display_name: str, emoji: dict[str, str] | None = None
) -> discord.Embed:
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
        klass = brand.class_label(m.class_name, emoji) if m.class_name else None
        spec_class = " ".join(p for p in (m.active_spec, klass) if p) or "Unknown"
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
    members: list[Member],
    *,
    title: str,
    color: int,
    per_page: int = 25,
    emoji: dict[str, str] | None = None,
) -> discord.Embed:
    """A listing embed; truncates to ``per_page`` with a count footer."""
    embed = discord.Embed(title=clip(title, TITLE_LIMIT), color=color)
    shown = members[:per_page]
    if not shown:
        embed.description = "No matching members."
        return embed
    lines = [member_line(m, index=i + 1, emoji=emoji) for i, m in enumerate(shown)]
    embed.description = clip("\n".join(lines), DESC_LIMIT)
    if len(members) > per_page:
        embed.set_footer(text=f"Showing {per_page} of {len(members)} members.")
    else:
        embed.set_footer(text=f"{len(members)} member(s).")
    return embed
