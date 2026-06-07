"""Targeted Mythic+ score refresh.

Keeps the boards' scores fresh by re-pulling only the characters that matter,
then nudging the living boards to re-render. Three triggers share this one cheap
path so we never blanket-poll the whole roster:

* a member's self-serve ``/refreshme`` (officers/Flamekeepers),
* a recurring pass over the active key-runners (the solo grinders + pushers),
* whenever the boards otherwise need their numbers brought current.

Network lives behind :class:`RaiderIOClient`, so the selection logic is pure and
unit-testable, and the refresh is testable with a faked client.
"""

from __future__ import annotations

import logging

import aiohttp

from .config import Config
from .db import Database
from .models import Member
from .posting import PostGate
from .services.raiderio import RaiderIOClient

log = logging.getLogger("ftabot.scores")


def select_active(members: list[Member], config: Config) -> list[Member]:
    """The bounded set worth refreshing: raiders first, then the top M+ scorers,
    capped at ``mplus_feed_member_cap`` — so the people actually running keys stay
    fresh without polling all ~300 characters."""
    raiders = [m for m in members if m.raid_team]
    others = sorted(
        (m for m in members if not m.raid_team),
        key=lambda m: m.mythic_plus_score or 0,
        reverse=True,
    )
    cap = config.mplus_feed_member_cap
    if cap <= 0:  # a cap of zero (or less) means "refresh nobody"
        return []
    picked: list[Member] = []
    seen: set[tuple[str, str, str]] = set()
    for member in raiders + others:
        key = (member.character_name.lower(), (member.realm_slug or "").lower(), (member.region or "").lower())
        if key in seen:
            continue
        seen.add(key)
        picked.append(member)
        if len(picked) >= cap:
            break
    return picked


async def refresh_scores(
    db: Database,
    config: Config,
    members: list[Member],
    *,
    session: aiohttp.ClientSession | None = None,
) -> list[Member]:
    """Re-pull each member's Mythic+ score (and gear/prog) from Raider.IO and
    persist it. Returns the members whose Mythic+ score actually changed."""
    if not members:
        return []
    changed: list[Member] = []
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(headers={"Accept": "application/json"})
    try:
        async with RaiderIOClient(session=session) as rio:
            for member in members:
                before = member.mythic_plus_score
                try:
                    await rio.enrich_character(member)
                except Exception as exc:  # noqa: BLE001 - one bad char never stops the batch
                    log.warning("score refresh failed for %s: %s", member.character_name, exc)
                    continue
                await db.upsert_synced(member)
                if member.mythic_plus_score != before:
                    changed.append(member)
    finally:
        if own_session:
            await session.close()
    return changed


async def refresh_boards(db: Database, config: Config, bot) -> int:
    """Re-render every enabled board (diff-before-edit, so it only edits on a
    real change). Returns how many boards were rendered. Safe to call after a
    score change so the board reflects it immediately."""
    from .boards import REGISTRY, BotBoardMessenger, refresh_board

    gate = PostGate.from_config(config)
    messenger = BotBoardMessenger(bot)
    rendered = 0
    for row in await db.boards(enabled_only=True):
        board = REGISTRY.get(row["board_type"])
        if board is None:
            continue
        try:
            await refresh_board(db, config, board, row, messenger, gate)
            rendered += 1
        except Exception as exc:  # noqa: BLE001 - one board never stops the rest
            log.warning("board refresh failed (%s): %s", row["board_type"], exc)
    return rendered
