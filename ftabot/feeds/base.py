"""Core feed abstractions: the normalized event + the source interface."""

from __future__ import annotations

import abc
import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any

import aiohttp


@dataclass
class FeedEvent:
    """One normalized item from any feed, ready to dedup / route / publish."""

    source: str  # e.g. "wowhead"
    event_type: str  # e.g. "wowhead.news" / "wowhead.patch-notes"
    external_id: str  # stable id for dedup (RSS guid, event id, …)
    title: str
    url: str | None = None
    summary: str | None = None
    categories: list[str] = field(default_factory=list)
    published_at: str | None = None  # ISO-8601 if known
    severity: str = "info"  # info / warning / critical
    image_url: str | None = None
    # Whether Calcifer *authored* this item's title/summary (so the publisher
    # enforces the house style on it) versus *relayed* it verbatim from an
    # external source (a Wowhead headline), which is left untouched. Most
    # sources author their copy; only pure relays set this False.
    authored: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.source, self.external_id)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "FeedEvent":
        return cls(**json.loads(raw))


class FeedSource(abc.ABC):
    """A pollable feed. Subclasses implement :meth:`fetch`."""

    #: Stable short name, also the ``FeedEvent.source`` value.
    name: str

    @abc.abstractmethod
    async def fetch(self, session: aiohttp.ClientSession) -> list[FeedEvent]:
        """Return the current items as FeedEvents (newest-first is fine).

        Implementations should be defensive — a transient fetch/parse error
        should raise so the runner can log and continue, never crash the loop.
        """
        raise NotImplementedError
