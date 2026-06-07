"""Raider.IO Mythic+ feed — celebrate the guild's notable new keys.

Polls a bounded set of the roster (raiders first, then the top M+ players) for
their recent runs and emits a FeedEvent for each timed key.

Two event lanes:
  raiderio.mplus-run  — at/above the floor OR a personal best (gets realtime alert)
  raiderio.mplus-log  — below the floor, not a PB (quiet lane; digest-mode friendly)

Dedup is by run URL, so a key is announced once. Raider.IO has no webhook, so
this is a pull source on the normal feed loop.
"""

from __future__ import annotations

from ..config import Config
from ..models import Member
from ..services.raiderio import RaiderIOClient
from .base import FeedEvent, FeedSource

_CharKey = tuple[str, str, str]  # (name_lower, realm_lower, region_lower)


def _char_key(m: Member) -> _CharKey:
    return (
        m.character_name.lower(),
        (m.realm_slug or "").lower(),
        (m.region or "").lower(),
    )


def _select_members(members: list[Member], cap: int) -> list[Member]:
    """Raiders first, then fill with the top M+ scorers, up to ``cap``."""
    if cap <= 0:  # a cap of zero (or less) means "poll nobody"
        return []
    raiders = [m for m in members if m.raid_team]
    others = [m for m in members if not m.raid_team]
    others.sort(key=lambda m: m.mythic_plus_score or 0, reverse=True)
    picked: list[Member] = []
    seen: set[_CharKey] = set()
    for m in raiders + others:
        key = _char_key(m)
        if key in seen:
            continue
        seen.add(key)
        picked.append(m)
        if len(picked) >= cap:
            break
    return picked


def _run_event(member: Member, run: dict, *, is_pb: bool = False) -> FeedEvent:
    level = run.get("level")
    upgrades = run.get("upgrades") or 0
    stars = "⭐" * min(max(upgrades, 1), 3)
    dungeon = run.get("dungeon") or run.get("short_name") or "a dungeon"
    if is_pb:
        title = f"🔑 {member.character_name} set a new PB: +{level} {dungeon} {stars}"
    else:
        title = f"🔑 {member.character_name} timed a +{level} {dungeon} {stars}"
    return FeedEvent(
        source="raiderio_mplus",
        event_type="raiderio.mplus-run",
        external_id=run.get("url") or f"{member.character_name}-{run.get('completed_at')}",
        title=title,
        url=run.get("url"),
        summary=(
            f"{member.character_name}-{member.realm_slug.title()} "
            f"· +{level} in time ({upgrades} chest{'s' if upgrades != 1 else ''})"
            + (f" · {run['score']:.0f} score" if run.get("score") else "")
        ),
        categories=["Mythic+"],
        published_at=run.get("completed_at"),
        extra={"level": level, "upgrades": upgrades, "is_pb": is_pb},
    )


def _log_event(member: Member, run: dict) -> FeedEvent:
    """Quiet log-lane event for below-floor non-PB keys (digest-mode fodder)."""
    level = run.get("level")
    dungeon = run.get("dungeon") or run.get("short_name") or "a dungeon"
    return FeedEvent(
        source="raiderio_mplus",
        event_type="raiderio.mplus-log",
        external_id=run.get("url") or f"{member.character_name}-{run.get('completed_at')}",
        title=f"📊 {member.character_name} ran a +{level} {dungeon}",
        url=run.get("url"),
        categories=["Mythic+", "Log"],
        published_at=run.get("completed_at"),
        extra={"level": level},
    )


class RaiderIOMythicSource(FeedSource):
    name = "raiderio_mplus"

    def __init__(self, config: Config):
        self.config = config

    async def fetch(self, session) -> list[FeedEvent]:
        from ..db import Database

        async with Database(self.config.database_path) as db:
            members = await db.all_members(order_by="mythic_plus_score")
            # Pre-load stored personal bests for all roster members.
            bests: dict[_CharKey, int] = {}
            for m in members:
                bests[_char_key(m)] = await db.get_mplus_best(
                    m.character_name, m.realm_slug or "", m.region or ""
                )

        picks = _select_members(members, self.config.mplus_feed_member_cap)
        floor = self.config.mplus_alert_min_level
        events: list[FeedEvent] = []
        new_bests: dict[_CharKey, tuple[Member, int]] = {}  # key → (member, level)

        async with RaiderIOClient(session=session) as rio:
            for member in picks:
                ck = _char_key(member)
                runs = await rio.recent_runs(
                    member.character_name, member.realm_slug, member.region
                )
                for run in runs:
                    if (run.get("upgrades") or 0) < 1:  # timed keys only
                        continue
                    level = run.get("level") or 0
                    stored = max(bests.get(ck, 0), new_bests[ck][1] if ck in new_bests else 0)
                    is_pb = level > stored
                    if is_pb:
                        bests[ck] = level
                        new_bests[ck] = (member, level)
                    if level >= floor or is_pb:
                        events.append(_run_event(member, run, is_pb=is_pb))
                    else:
                        events.append(_log_event(member, run))

        # Persist any new personal bests after the run loop.
        if new_bests:
            async with Database(self.config.database_path) as db:
                for _ck, (m, level) in new_bests.items():
                    await db.set_mplus_best(
                        m.character_name, m.realm_slug or "", m.region or "", level
                    )

        return events
