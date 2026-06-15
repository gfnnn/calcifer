"""WoWProgress ranking feed — announce our guild's world/realm rank movement.

WoWProgress has no RSS, but it exposes guild ranking as JSON (see
``services/wowprogress.py``). This source polls *our* guild's rank each pass and
emits an event when it moves: the dedup key encodes the world+realm rank, so the
feed runner's ``seen_items`` table fires each new standing exactly once (no extra
state needed). Per WoWProgress's terms, every event links back to the guild's
WoWProgress page and states the data source.

This is single-guild only by design (their terms require the batch export for
many guilds); realm-wide competitive intel would be a separate, later piece.
"""

from __future__ import annotations

from ..config import Config
from ..models import normalize_realm
from ..services.wowprogress import ATTRIBUTION, WowProgressClient, guild_url
from .base import FeedEvent, FeedSource


def _rank_event(region: str, realm: str, name: str, rank: dict) -> FeedEvent:
    """Build a ranking FeedEvent from a parsed json_rank dict (pure)."""
    world = rank.get("world_rank", -1)
    realm_rank = rank.get("realm_rank", -1)
    area = rank.get("area_rank", -1)
    score = rank.get("score", -1)

    def _pos(value: int) -> str:
        return f"#{value}" if value >= 0 else "unranked"

    bits = [f"World {_pos(world)}", f"Region {_pos(area)}", f"Realm {_pos(realm_rank)}"]
    score_bit = f"Score {score}. " if score >= 0 else ""
    return FeedEvent(
        source="wowprogress",
        event_type="wowprogress.rank",
        external_id=(
            f"wowprogress-{region.lower()}-{normalize_realm(realm)}-{name}"
            f"-w{world}-r{realm_rank}"
        ),
        title=f"📈 {name} on WoWProgress: world {_pos(world)}, realm {_pos(realm_rank)}",
        url=guild_url(region, realm, name),
        summary=f"{', '.join(bits)}. {score_bit}{ATTRIBUTION}.",
        categories=["Ranking"],
        severity="info",
        extra={"world_rank": world, "realm_rank": realm_rank, "area_rank": area, "score": score},
    )


class WowProgressSource(FeedSource):
    name = "wowprogress"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        cfg = self.config
        async with WowProgressClient(session=session) as wp:
            rank = await wp.guild_rank(
                cfg.guild_region,
                cfg.guild_realm,
                cfg.guild_name,
                rating=cfg.wowprogress_rating,
            )
        if not rank:
            return []
        return [_rank_event(cfg.guild_region, cfg.guild_realm, cfg.guild_name, rank)]
