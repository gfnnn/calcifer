"""Living boards: bot-owned messages that re-render from the DB and edit
themselves in place, so a channel becomes a living page instead of a scroll.

The engine (``engine.refresh_board``) is pure of Discord: it talks to a small
``BoardMessenger`` protocol, so the render/diff/self-heal logic is fully
offline-testable. Concrete board renderers register themselves into ``REGISTRY``
(the first one, "Calcifer's Hot 20", lands in Phase 1).
"""

from . import (
    events,  # noqa: F401 - registers the Weekly Events board
    hot20,  # noqa: F401 - imported for its side effect: registers Hot 20
    news,  # noqa: F401 - registers the News Highlights board
    pulse,  # noqa: F401 - registers the Roster Pulse board
    recruitment,  # noqa: F401 - registers the Recruitment board
)
from .base import (
    BOARD_NAMES,
    REGISTRY,
    Board,
    BoardPayload,
    board_name,
    register,
)
from .engine import BoardMessenger, refresh_board
from .messenger import BotBoardMessenger

__all__ = [
    "REGISTRY",
    "BOARD_NAMES",
    "Board",
    "BoardPayload",
    "board_name",
    "register",
    "BoardMessenger",
    "BotBoardMessenger",
    "refresh_board",
]
