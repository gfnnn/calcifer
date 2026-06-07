"""Raider.IO M+ affixes feed — announces the weekly rotation.

One event per week, keyed on the sorted affix IDs so it fires exactly once per
rotation regardless of which poll happens to land first. The current affixes are
also persisted in the mythic_affixes table so other features can query the
current week without a live API call.
"""

from __future__ import annotations

from ..config import Config
from ..services.raiderio import RaiderIOClient
from .base import FeedEvent, FeedSource


class RaiderIOAffixesSource(FeedSource):
    name = "raiderio_affixes"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        cfg = self.config
        region = cfg.guild_region
        async with RaiderIOClient(session=session) as rio:
            affixes = await rio.current_affixes(region)
        if not affixes:
            return []

        # Stable key: sorted IDs so rotation order can't create duplicate events.
        ids = sorted(str(a["id"]) for a in affixes if a.get("id") is not None)
        week_key = f"{region}-" + "-".join(ids)
        names = ", ".join(a["name"] for a in affixes if a.get("name"))

        from ..db import Database
        async with Database(cfg.database_path) as db:
            await db.upsert_affix_week(week_key, region, names, affixes)

        return [
            FeedEvent(
                source=self.name,
                event_type="raiderio.affixes-changed",
                external_id=week_key,
                title=f"🔑 M+ affixes this week: {names}",
                summary=_affix_summary(affixes),
                categories=["Mythic+"],
                severity="info",
            )
        ]


def _affix_summary(affixes: list[dict]) -> str:
    lines = []
    for a in affixes:
        name = a.get("name") or "Unknown"
        desc = a.get("description") or ""
        lines.append(f"**{name}**: {desc}" if desc else f"**{name}**")
    return "\n".join(lines)
