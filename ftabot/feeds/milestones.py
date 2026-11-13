"""Guild milestones feed: new boss kills, Mythic+ score thresholds, level caps.

The alert side of the data the boards show. It needs no API of its own: each
poll runs ``milestones.collect`` over what the sync has stored, then offers
every milestone from the last few days as a ``FeedEvent``. The runner's
``seen_items`` dedup makes that idempotent, and re-offering is deliberate: an
alert the posting guardrail held back is still on offer once it opens.

Event types (route each with ``/feed route``): ``milestone.raid-kill``,
``milestone.mplus`` and ``milestone.level-cap``.
"""

from __future__ import annotations

from typing import Any

from .. import milestones
from ..config import Config
from ..models import MAX_LEVEL
from .base import FeedEvent, FeedSource

SOURCE_NAME = "milestones"
# How long a detected milestone stays on offer to the runner.
_OFFER_DAYS = 7
_DIFFICULTY_NAMES = {"normal": "Normal", "heroic": "Heroic", "mythic": "Mythic"}


def to_event(row: dict[str, Any], guild_name: str) -> FeedEvent | None:
    """Render one stored milestone as a FeedEvent (``None`` for an unknown kind)."""
    detail = row.get("detail") or {}
    subject = row["subject"]
    value = int(row.get("value") or 0)
    common: dict[str, Any] = {
        "source": SOURCE_NAME,
        "external_id": f"milestone-{row['id']}",
        "published_at": row.get("achieved_at"),
        "extra": {"kind": row["kind"], "value": value, **detail},
    }

    if row["kind"] == "raid_kill":
        difficulty = _DIFFICULTY_NAMES.get(detail.get("difficulty"), "Heroic")
        total = int(detail.get("total_bosses") or 0)
        progress = f"{value}/{total}" if total else str(value)
        if total and value >= total:
            title = f"🏆 {subject} cleared on {difficulty} ({progress})"
            summary = f"{guild_name} have every boss down on {difficulty}. Take a bow."
        else:
            title = f"🔥 Boss down: {progress} {difficulty} in {subject}"
            summary = f"{guild_name} just pushed further into {subject}."
        return FeedEvent(
            event_type="milestone.raid-kill",
            title=title,
            summary=summary,
            categories=["Milestone", "Raid"],
            severity="warning",
            **common,
        )

    if row["kind"] == "mplus":
        score = detail.get("score")
        now_at = f"Now sitting at {score:,.0f}. " if isinstance(score, (int, float)) else ""
        return FeedEvent(
            event_type="milestone.mplus",
            title=f"⚡ {subject} broke {value:,} Mythic+ score",
            summary=f"{now_at}The keys are getting spicy.",
            url=detail.get("profile_url"),
            categories=["Milestone", "Mythic+"],
            **common,
        )

    if row["kind"] == "level_cap":
        return FeedEvent(
            event_type="milestone.level-cap",
            title=f"🎉 {subject} reached level {MAX_LEVEL}",
            summary="Fresh at the cap. The real game starts here.",
            url=detail.get("profile_url"),
            categories=["Milestone", "Levelling"],
            **common,
        )

    return None


class MilestonesSource(FeedSource):
    name = SOURCE_NAME

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        from ..db import Database

        async with Database(self.config.database_path) as db:
            await milestones.collect(db)
            rows = await db.recent_milestones(days=_OFFER_DAYS)
        events = (to_event(row, self.config.guild_name) for row in rows)
        return [event for event in events if event is not None]
