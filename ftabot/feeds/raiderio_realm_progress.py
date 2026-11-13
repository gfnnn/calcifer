"""Raider.IO realm progression feed — tracks raid standings on our home realm.

On each poll we fetch the raid guild rankings for every tracked raid, upsert
every guild into the realm_progress table, and emit FeedEvents only for guilds
whose kill count has increased since last poll.

By default the tracked raids are the ones open right now (per Raider.IO's raid
catalog), so a new tier is picked up when it opens with no config change;
REALM_PROGRESS_RAIDS pins specific slugs instead. Existing history for older
raids is unaffected and a new raid starts tracking from its first poll.

Our own guild uses event_type "raiderio.our-progression" so officers can route
it to a dedicated progression channel; rival guilds use "raiderio.realm-progression"
and can be routed to a competitive-intel channel or suppressed entirely.
"""

from __future__ import annotations

from ..config import Config
from ..models import normalize_realm
from ..raids import current_raids
from ..services.raiderio import RaiderIOClient
from .base import FeedEvent, FeedSource


class RaiderIORealmProgressSource(FeedSource):
    name = "raiderio_realm_progress"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        cfg = self.config
        realm_slug = normalize_realm(cfg.guild_realm)
        region = cfg.guild_region
        difficulty = cfg.realm_progress_difficulty
        raid_slugs = cfg.realm_progress_raids

        from ..db import Database
        events: list[FeedEvent] = []
        async with RaiderIOClient(session=session) as rio:
            if not raid_slugs:
                # Nothing pinned: follow the raids that are open right now, so
                # the feed moves to a new tier without a config change.
                raid_slugs = tuple(
                    raid.slug for raid in current_raids(await rio.raid_catalog(region))
                )
            if not raid_slugs:
                return []
            async with Database(cfg.database_path) as db:
                for raid_slug in raid_slugs:
                    guilds = await rio.realm_raid_progression(
                        raid_slug, region, realm_slug, difficulty
                    )
                    for rank, entry in enumerate(guilds, start=1):
                        guild_name = _guild_name(entry)
                        if not guild_name:
                            continue
                        kills, total, summary = _parse_progress(entry)
                        new_kill = await db.upsert_realm_guild(
                            guild_name, realm_slug, region, raid_slug, difficulty, kills,
                            summary=summary, realm_rank=rank,
                        )
                        if not new_kill:
                            continue

                        is_ours = guild_name.lower() == cfg.guild_name.lower()
                        event_type = (
                            "raiderio.our-progression" if is_ours
                            else "raiderio.realm-progression"
                        )
                        prefix = "🔥" if is_ours else "⚔️"
                        kills_label = f"{kills}/{total}" if total else str(kills)
                        raid_label = raid_slug.replace("-", " ").title()
                        ext_id = (
                            f"realm-prog-{guild_name.lower().replace(' ', '-')}"
                            f"-{realm_slug}-{raid_slug}-{difficulty}-{kills}"
                        )
                        events.append(
                            FeedEvent(
                                source=self.name,
                                event_type=event_type,
                                external_id=ext_id,
                                title=(
                                    f"{prefix} ⟨{guild_name}⟩ · {kills_label} M "
                                    f"({raid_label})"
                                ),
                                summary=f"Realm rank #{rank} · {summary}",
                                categories=["Progression"],
                                severity="warning" if is_ours else "info",
                                extra={
                                    "guild_name": guild_name,
                                    "realm_slug": realm_slug,
                                    "kills": kills,
                                    "total": total,
                                    "realm_rank": rank,
                                    "raid_slug": raid_slug,
                                    "difficulty": difficulty,
                                },
                            )
                        )
        return events


def _guild_name(entry: dict) -> str | None:
    return (entry.get("guild") or {}).get("name")


def _parse_progress(entry: dict) -> tuple[int, int, str]:
    encounters = entry.get("encountersDefeated") or []
    total = len(encounters)
    kills = sum(1 for e in encounters if e.get("firstDefeated"))
    summary = f"{kills}/{total} M" if total else str(kills)
    return kills, total, summary
