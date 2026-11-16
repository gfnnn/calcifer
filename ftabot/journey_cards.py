"""The welcome card: a pure renderer over the journey's :class:`Status`.

``render_card`` turns a status (plus an optional roster match) into an embed
and a list of button specs. No Discord I/O and no database: the cog turns the
specs into real buttons and does the posting, so this is tested like a board
renderer. Staff surface, so the copy is plain and short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from . import brand
from .journey import RosterMatch, Status, invite_label

if TYPE_CHECKING:
    from .config import Config

GREET = "greet"
PLACE = "place"
LIST_PLACE = "lplace"
CHECK_IN = "checkin"

# Discord allows 5 rows of 5 buttons on one message.
_ROWS, _PER_ROW = 5, 5
CHECK_IN = "checkin"

_DISCORD_EPOCH_MS = 1420070400000


@dataclass(frozen=True)
class ButtonSpec:
    """One button on a journey surface, before it becomes a Discord component."""

    action: str
    target: str
    label: str
    arg: str | None = None
    disabled: bool = False
    primary: bool = False
    row: int | None = None


@dataclass
class CardPayload:
    embed: discord.Embed
    buttons: list[ButtonSpec] = field(default_factory=list)


def _unix(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


def stamp(iso: str | None, style: str = "R") -> str:
    """An ISO time as a Discord timestamp ("" if unknown)."""
    unix = _unix(iso)
    return f"<t:{unix}:{style}>" if unix is not None else ""


def account_created_unix(discord_id: str) -> int | None:
    """When a Discord account was made, read from its id (a snowflake)."""
    try:
        return ((int(discord_id) >> 22) + _DISCORD_EPOCH_MS) // 1000
    except (TypeError, ValueError):
        return None


def match_line(match: RosterMatch | None) -> str:
    """The roster match as one line: character, in-game rank, confidence."""
    if match is None:
        return "no match"
    parts = [f"**{discord.utils.escape_markdown(match.member.character_name)}**"]
    if match.rank_name:
        parts.append(discord.utils.escape_markdown(match.rank_name))
    parts.append(match.confidence)
    return " · ".join(parts)


def _who(actor_id: str | None) -> str:
    return f"<@{actor_id}>" if actor_id else "someone, in Discord"


def status_lines(status: Status) -> list[str]:
    """Who did what and when, in the order it matters to staff."""
    lines = []
    if status.greeted:
        lines.append(f"Said hi: {_who(status.greeted_by)} {stamp(status.greeted_at)}")
    else:
        lines.append("Not greeted yet")
    if status.placed:
        lines.append(
            f"Placed: **{status.placed_role}** by {_who(status.placed_by)} "
            f"{stamp(status.placed_at)}"
        )
    else:
        lines.append("Not placed yet")
    if status.checked_in_at:
        lines.append(
            f"Checked in: {_who(status.checked_in_by)} {stamp(status.checked_in_at)}"
        )
    if not status.present:
        tail = "" if status.handled else " before anyone got to them"
        lines.append(f"**Left** {stamp(status.left_at)}{tail}")
    return [line.rstrip() for line in lines]


def placement_buttons(status: Status, config: "Config") -> list[ButtonSpec]:
    """One "Set <role>" button per placement role; the one already held is
    greyed out, and all of them once the person has gone."""
    held = (status.placed_role or "").casefold()
    return [
        ButtonSpec(
            PLACE, status.discord_id, f"Set {role}", arg=role,
            disabled=not status.present or role.casefold() == held,
        )
        for role in config.journey_placement_roles
    ]


def render_card(
    status: Status,
    config: "Config",
    match: RosterMatch | None = None,
    picked: tuple[str, ...] | None = None,
) -> CardPayload:
    """The welcome card for one person. ``picked`` overrides the interest roles
    read from the log (the cog passes the member's live roles when it has them)."""
    name = discord.utils.escape_markdown(status.display_name or "unknown")
    lines = [f"<@{status.discord_id}> · **{name}**"]
    created = account_created_unix(status.discord_id)
    if created is not None:
        lines.append(f"Account created <t:{created}:R>")
    if status.joined_at:
        lines.append(f"Joined {stamp(status.joined_at, 'F')} ({stamp(status.joined_at)})")

    embed = discord.Embed(
        title="👋 Back again" if status.rejoin else "👋 New member",
        description="\n".join(lines),
        color=brand.GUILD_EMBER,
    )
    embed.add_field(name="Came via", value=invite_label(status.came_via, config.invite_labels))
    chosen = status.picked if picked is None else picked
    embed.add_field(name="Picked", value=", ".join(chosen) if chosen else "nothing yet")
    embed.add_field(name="Roster match", value=match_line(match))
    if status.rejoin and status.previous_joined_at:
        embed.add_field(
            name="Previous join", value=stamp(status.previous_joined_at, "D") or "unknown"
        )
    embed.add_field(name="Status", value="\n".join(status_lines(status)), inline=False)
    embed.set_footer(text=brand.sanitize("Calcifer keeps the notes. You do the welcoming."))

    buttons = [
        ButtonSpec(
            GREET, status.discord_id, "Said hi", primary=True,
            disabled=status.greeted or not status.present,
        ),
        *placement_buttons(status, config),
    ]
    return CardPayload(embed=embed, buttons=buttons)


# -- /journey unplaced -----------------------------------------------------------


@dataclass(frozen=True)
class UnplacedEntry:
    """One member waiting for a rank, as the list shows them."""

    discord_id: str
    display_name: str | None
    joined_at: str | None
    match: RosterMatch | None = None


def unplaced_page_size(config: "Config") -> int:
    """How many members fit on one page: ten, or fewer if there are so many
    placement roles that their buttons would not fit on one message."""
    per_member = max(1, len(config.journey_placement_roles))
    return max(1, min(10, _ROWS * (_PER_ROW // per_member)))


def page_count(total: int, size: int) -> int:
    return max(1, -(-total // size))


def render_unplaced(
    entries: list[UnplacedEntry], config: "Config", *, page: int, total: int
) -> CardPayload:
    """One page of the unplaced list. ``entries`` is that page's slice;
    ``page`` is 1-based (already clamped) and ``total`` counts everyone waiting.
    Each member gets a numbered line and matching numbered "Set" buttons."""
    size = unplaced_page_size(config)
    pages = page_count(total, size)
    embed = discord.Embed(title="🚪 Unplaced members", color=brand.GUILD_EMBER)
    if not entries:
        embed.description = "Everyone here has a rank. Nothing to do."
        return CardPayload(embed=embed)

    roles = config.journey_placement_roles
    members_per_row = max(1, _PER_ROW // max(1, len(roles)))
    lines, buttons = [], []
    for index, entry in enumerate(entries):
        number = index + 1
        name = discord.utils.escape_markdown(entry.display_name or "unknown")
        joined = f" · joined {stamp(entry.joined_at)}" if entry.joined_at else ""
        lines.append(
            f"`{number:>2}` <@{entry.discord_id}> · **{name}**{joined}"
            f" · {match_line(entry.match)}"
        )
        for role in roles:
            buttons.append(ButtonSpec(
                LIST_PLACE, entry.discord_id, f"{number} · {role}",
                arg=f"{role}:{page}", row=index // members_per_row,
            ))
    embed.description = "\n".join(lines)
    more = f" Next: /journey unplaced page:{page + 1}" if page < pages else ""
    embed.set_footer(text=brand.sanitize(
        f"Page {page} of {pages} · {total} without a rank, longest wait first.{more}"
    ))
    return CardPayload(embed=embed, buttons=buttons)


# -- the daily nudge -------------------------------------------------------------

NUDGE_CAP = 10
_NAME_MAX = 24


def _nudge_line(status: Status, number: int | None = None) -> str:
    name = status.display_name or "unknown"
    if len(name) > _NAME_MAX:
        name = name[: _NAME_MAX - 1] + "…"
    lead = f"`{number:>2}` " if number is not None else "• "
    return (
        f"{lead}<@{status.discord_id}> · **{discord.utils.escape_markdown(name)}**"
        f" · joined {stamp(status.joined_at)}"
    )


def _nudge_section(
    heading: str, people: tuple[Status, ...], *, numbered: bool = False
) -> list[str]:
    if not people:
        return []
    shown = people[:NUDGE_CAP]
    lines = [f"**{heading}** ({len(people)})"]
    lines += [
        _nudge_line(s, i + 1 if numbered else None) for i, s in enumerate(shown)
    ]
    if len(people) > NUDGE_CAP:
        lines.append(f"*and {len(people) - NUDGE_CAP} more*")
    return [*lines, ""]


def render_nudge(lists) -> CardPayload | None:
    """The day's nudge, or ``None`` on a quiet day (nothing is posted).

    Three short lists, each capped at :data:`NUDGE_CAP` with an "and N more"
    tail. The check-in list is numbered and each number has a "Checked in"
    button; greeting and placing are done on the cards or ``/journey unplaced``.
    """
    if lists.quiet:
        return None
    lines = [
        *_nudge_section("Not greeted after a day", lists.not_greeted),
        *_nudge_section("Not placed after two days", lists.not_placed),
        *_nudge_section("Day-7 check-in due", lists.check_in, numbered=True),
    ]
    embed = discord.Embed(
        title="🔔 Front door: today's nudge",
        description="\n".join(lines).rstrip(),
        color=brand.GUILD_EMBER,
    )
    embed.set_footer(text=brand.sanitize(
        "Greet and place from the welcome cards or /journey unplaced."
    ))
    buttons = [
        ButtonSpec(CHECK_IN, s.discord_id, f"Checked in · {i + 1}")
        for i, s in enumerate(lists.check_in[:NUDGE_CAP])
    ]
    return CardPayload(embed=embed, buttons=buttons)
