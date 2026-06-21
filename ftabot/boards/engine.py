"""The board refresh engine: render → diff → edit-in-place, with self-heal.

Pure of Discord. It drives a :class:`BoardMessenger` (implemented over the bot
in ``serve.py``, faked in tests), so the lifecycle is fully offline-testable:

* **First run** — no stored message id → ``send`` and store id + fingerprint.
* **Unchanged** — stored fingerprint matches *and the effective channel is the
  same* → skip the edit entirely (diff-before-edit; saves rate budget, no
  "edited" flicker).
* **Changed** — fingerprint differs → ``edit`` in place, store the new hash.
* **Channel moved** — the gate now resolves to a different channel than the one
  the live message sits in (e.g. a ``POSTING_MODE`` test → live flip, or a
  promotion) → re-post in the new channel rather than editing the old copy or
  wrongly skipping as "unchanged".
* **Self-heal** — the stored message is gone (``edit`` returns ``False``) →
  re-create it and store the new id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import Config
    from ..db import Database
    from ..posting import PostGate
    from .base import Board, BoardPayload


class BoardMessenger(Protocol):
    """How the engine posts. ``edit`` returns False if the message is gone."""

    async def send(self, channel_id: str, payload: "BoardPayload") -> str: ...

    async def edit(
        self, channel_id: str, message_id: str, payload: "BoardPayload"
    ) -> bool: ...


async def refresh_board(
    db: "Database",
    config: "Config",
    board: "Board",
    row: dict,
    messenger: BoardMessenger,
    gate: "PostGate",
    emoji: dict[str, str] | None = None,
) -> str:
    """Render and reconcile one board. Returns the action taken (for logging).

    The destination passes through the posting ``gate`` first: a board only
    reaches a real channel once promoted, and nothing posts while the guardrail
    is off, so this is the choke point that keeps boards out of the community's
    view until confirmed.
    """
    channel_id = gate.resolve(row["channel_id"], promoted=row.get("promoted", False))
    if channel_id is None:
        return "blocked"  # guardrail: no confirmed destination, so post nothing

    payload = await board.render(db, config, row.get("config") or {}, emoji)
    if payload is None:
        return "skip"

    message_id = row.get("message_id")
    # Only edit in place when the live message is actually in the channel the gate
    # now resolves to. If the effective channel changed (a POSTING_MODE flip or a
    # promotion), the stored message lives elsewhere, so fall through and re-post.
    same_channel = row.get("posted_channel_id") == channel_id
    if message_id and same_channel:
        if row.get("content_hash") == payload.fingerprint:
            return "unchanged"  # diff-before-edit: nothing material changed
        if await messenger.edit(channel_id, message_id, payload):
            await db.set_board_message(row["id"], message_id, payload.fingerprint, channel_id)
            return "edited"
        # The message vanished (deleted) — fall through and self-heal by re-posting.

    new_id = await messenger.send(channel_id, payload)
    await db.set_board_message(row["id"], new_id, payload.fingerprint, channel_id)
    return "created"
