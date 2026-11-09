"""Board contract + registry.

A :class:`Board` renders the current state of the DB into a :class:`BoardPayload`
(content and/or an embed) plus a ``fingerprint`` that captures *only the
meaningful content* — so the engine can skip an edit when nothing material
changed (a relative ``<t:…:R>`` "updated" stamp, for instance, is deliberately
kept out of the fingerprint so it doesn't force needless edits).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing discord/db at module load (keeps base pure)
    import discord

    from ..config import Config
    from ..db import Database


@dataclass
class BoardPayload:
    """What a board renders. ``fingerprint`` is the diff key (stable per state)."""

    fingerprint: str
    content: str | None = None
    embed: "discord.Embed | None" = None


class Board(abc.ABC):
    """A board renderer. Subclasses set ``board_type`` and implement ``render``."""

    board_type: str = "board"

    @abc.abstractmethod
    async def render(
        self,
        db: "Database",
        config: "Config",
        board_config: dict,
        emoji: dict[str, str] | None = None,
    ) -> "BoardPayload | None":
        """Build the payload from current data, or ``None`` to post nothing.

        ``emoji`` is the guild's custom-emoji ``name`` -> markup map (see
        ``boards.guild_emoji_map``); ``None`` (the default, e.g. in tests) makes
        a renderer fall back to its unicode glyphs.
        """
        raise NotImplementedError


# Concrete boards register here so the loop and the /board cog can find them by
# ``board_type``. Empty in Phase 0; populated as boards land.
REGISTRY: dict[str, Board] = {}

# Friendly display names for the registered board types, used by the /board
# cog. (Rules & Info and
# Guides & Resources are not boards — they are sticky pages, managed via /page.)
BOARD_NAMES: dict[str, str] = {
    "hot20": "Calcifer's Hot 20",
    "news_highlights": "News Highlights",
    "weekly_events": "Weekly Events",
    "roster_pulse": "Roster Pulse",
    "recruitment": "Recruitment",
}


def board_name(board_type: str) -> str:
    """The friendly name for a board type, falling back to the raw type."""
    return BOARD_NAMES.get(board_type, board_type)


def register(board: Board) -> Board:
    """Register a board instance under its ``board_type`` (idempotent)."""
    REGISTRY[board.board_type] = board
    return board
