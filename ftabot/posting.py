"""The posting guardrail: one choke point that decides where (or whether) any
Calcifer message may be sent.

Every outbound surface — living boards and feed alerts alike — resolves its
destination through a :class:`PostGate` before a single byte reaches Discord, so
posting is **deny-by-default** and promotion to a real channel is deliberate.

Three stages, set by ``POSTING_MODE`` (default ``off``):

* **off**  — post nothing, anywhere. The master guardrail; the bot is mute.
* **test** — every post is redirected to the confirmed ``TEST_CHANNEL_ID``;
  production channels never receive anything. If no test channel is confirmed,
  nothing is sent (you must confirm the location first).
* **live** — posts reach their real channel, but only surfaces explicitly marked
  ``promoted`` go to production; anything not yet promoted still lands in the
  test channel. So a board is seen by the community only once it has been
  reviewed in full and promoted to its correct channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


class PostBlocked(Exception):
    """Raised by a sender when the guardrail dropped a post (off / unconfirmed).

    Distinct from a transport failure: the event was never delivered *and must
    not be marked seen / consumed*, so it can still post once the gate opens.
    """


class PostingMode(str, Enum):
    OFF = "off"
    TEST = "test"
    LIVE = "live"

    @classmethod
    def parse(cls, value: str | None) -> "PostingMode":
        """Map a raw config string to a mode, defaulting to the safe OFF."""
        try:
            return cls((value or "off").strip().lower())
        except ValueError:
            return cls.OFF


@dataclass(frozen=True)
class PostGate:
    """Resolves a post's effective channel, or ``None`` to block the send."""

    mode: PostingMode = PostingMode.OFF
    test_channel_id: str | None = None

    @classmethod
    def from_config(cls, config: "Config") -> "PostGate":
        return cls(PostingMode.parse(config.posting_mode), config.test_channel_id)

    @property
    def silent(self) -> bool:
        """True when nothing can post at all (master guardrail engaged)."""
        return self.mode is PostingMode.OFF

    def resolve(self, production_channel_id: str | None, *, promoted: bool = False) -> str | None:
        """The channel a post should actually go to, or ``None`` to drop it.

        ``promoted`` marks a surface confirmed-for-production. Feed alerts pass
        ``promoted=True`` because their per-rule channel is already an officer's
        explicit choice; boards pass their own stored promotion flag.
        """
        if self.mode is PostingMode.OFF:
            return None
        if self.mode is PostingMode.TEST:
            # Everything is sandboxed; only sent once a test channel is confirmed.
            return self.test_channel_id or None
        # LIVE: promoted surfaces reach production; the rest stay sandboxed.
        if promoted:
            return production_channel_id or None
        return self.test_channel_id or None
