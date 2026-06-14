"""Managed-prose token substitution, shared by the sticky pages and the
recruitment board.

The GM's copy (Rules & Info, Guides & Resources, the recruitment pitch) carries
live ``{tokens}`` like ``{member_count}`` so the facts never drift stale. This
module is the single home for that substitution: ``SafeDict`` keeps an unknown
token visible instead of crashing, ``safe_format`` never takes a render down, and
``live_tokens`` computes the current values from the roster. Kept dependency-light
(only ``metrics``) so both the page renderer and the board renderer can import it
without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import metrics

if TYPE_CHECKING:
    from .config import Config
    from .db import Database


class SafeDict(dict):
    """Leaves an unknown ``{token}`` visible instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format(text: str, tokens: SafeDict) -> str:
    """``format_map`` that never takes the render down. ``SafeDict`` keeps
    unknown ``{tokens}`` visible, but a stray ``{`` or a malformed spec in the
    GM's JSON (or an officer's ``/recruit message``) still raises, so fall back
    to the raw text and render it verbatim instead of going stale."""
    try:
        return text.format_map(tokens)
    except (ValueError, LookupError, AttributeError, TypeError):
        return text


async def live_tokens(db: "Database", config: "Config") -> dict[str, str]:
    """The live ``{token}`` values shared by the managed-prose surfaces
    (the Rules & Info / Guides & Resources sticky pages and the Recruitment board)."""
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
