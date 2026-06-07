"""Roster synchronisation: pull from the data sources, write to SQLite.

The flow is:
  1. Fetch the guild roster from Raider.IO (no auth needed).
  2. Enrich each character with Raider.IO M+ score / ilvl / raid progress.
  3. Optionally enrich with Blizzard (rank/race/level/ilvl) and Warcraft Logs.
  4. Upsert every character (sync fields only) and prune anyone who left.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import metrics
from .config import Config
from .db import Database
from .models import Member
from .services.blizzard import BlizzardClient
from .services.raiderio import RaiderIOClient, RaiderIOError
from .services.warcraftlogs import WarcraftLogsClient
from .services.wowaudit import WowAuditClient

log = logging.getLogger("ftabot.sync")


@dataclass
class SyncResult:
    fetched: int = 0
    upserted: int = 0
    pruned: int = 0
    enriched: int = 0
    raiders: int = 0
    wishlist_items: int = 0
    attendance_events: int = 0
    health_score: float | None = None
    sources: tuple[str, ...] = ()
    error: str | None = None

    def summary(self) -> str:
        if self.error:
            return f"Sync failed: {self.error}"
        src = ", ".join(self.sources) if self.sources else "none"
        extras = ""
        if self.raiders or self.wishlist_items:
            extras = (
                f" Raid team: {self.raiders} raider(s), "
                f"{self.wishlist_items} wishlist item(s), "
                f"{self.attendance_events} raid night(s)."
            )
        health = (
            f" Guild health: {self.health_score:.0f}/100."
            if self.health_score is not None
            else ""
        )
        return (
            f"Synced {self.upserted} member(s) from {self.fetched} on the roster "
            f"(enriched {self.enriched}, pruned {self.pruned}). "
            f"Sources: {src}.{extras}{health}"
        )


async def sync_guild(
    db: Database,
    config: Config,
    *,
    name: str | None = None,
    realm: str | None = None,
    region: str | None = None,
    prune: bool = True,
) -> SyncResult:
    """Run a full roster sync into ``db``. Safe to call repeatedly."""
    name = name or config.guild_name
    realm = realm or config.guild_realm
    region = region or config.guild_region
    sources: list[str] = []

    # 1: Raider.IO roster of record for the WHOLE guild (lean — class/spec/rank;
    #    per-character stats come from the enrichment step below).
    async with RaiderIOClient() as rio:
        try:
            members = await rio.guild_members(name, realm, region)
        except RaiderIOError as exc:
            return SyncResult(error=f"Raider.IO roster: {exc}")
        sources.append("Raider.IO")

    by_name: dict[str, Member] = {m.character_name.lower(): m for m in members}

    # 2: WoW Audit overlay — team tag, Great Vault, wishlists, attendance. Also
    #    identifies the raiders so we can prioritise their enrichment.
    wishlists: list = []
    attendance: list = []
    raiders = 0
    raider_names: set[str] = set()
    if config.wowaudit_enabled:
        try:
            async with WowAuditClient(
                config.wowaudit_api_key,  # type: ignore[arg-type]
                base_url=config.wowaudit_base_url,
                default_region=region,
                default_realm=realm,
            ) as wa:
                team = await wa.team_name()
                wa_members = await wa.characters()
                vault = await wa.vault_slots()
                wishlists = await wa.wishlists()
                attendance = await wa.recent_attendance()

            raiders = len(wa_members)
            for wm in wa_members:
                raider_names.add(wm.character_name.lower())
                existing = by_name.get(wm.character_name.lower())
                if existing is not None:
                    existing.raid_team = team
                else:
                    # A raider not in the Raider.IO guild roster (e.g. cross-realm).
                    wm.raid_team = team
                    members.append(wm)
                    by_name[wm.character_name.lower()] = wm
            for lname, slots in vault.items():
                m = by_name.get(lname)
                if m is not None:
                    m.vault_slots_filled = slots
            sources.append("WoW Audit")
        except Exception as exc:  # noqa: BLE001 - overlay is best-effort
            log.warning("WoW Audit overlay skipped: %s", exc)

    # 2.5: incremental + prioritised enrichment. Skip members whose stored stats
    #      are still fresh (so repeat syncs are fast), always enrich raiders, and
    #      cap the rest if configured (the long tail refreshes oldest-first over
    #      successive runs).
    existing_by_name = {
        m.character_name.lower(): m for m in await db.all_members()
    }
    to_enrich = _select_for_enrichment(
        members,
        existing_by_name,
        raider_names,
        max_age_hours=config.enrich_max_age_hours,
        limit=config.enrich_limit,
    )
    if to_enrich:
        try:
            async with RaiderIOClient() as rio:
                await rio.enrich_all(to_enrich, concurrency=config.enrich_concurrency)
        except RaiderIOError as exc:
            log.warning("Raider.IO enrichment skipped: %s", exc)
    enriched = len(to_enrich)

    # 3a: Blizzard enrichment (authoritative rank/level/race), if configured.
    if config.blizzard_enabled:
        try:
            async with BlizzardClient(
                config.blizzard_client_id,  # type: ignore[arg-type]
                config.blizzard_client_secret,  # type: ignore[arg-type]
                region=region,
            ) as bz:
                ranks = {
                    nm.lower(): (level, rank)
                    for nm, _rlm, level, rank in await bz.guild_roster(name, realm)
                }
                for m in members:
                    info = ranks.get(m.character_name.lower())
                    if info:
                        m.level, m.guild_rank = info[0] or m.level, info[1]
                    await bz.enrich_character(m)
            sources.append("Blizzard")
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            log.warning("Blizzard enrichment skipped: %s", exc)

    # 3b: Warcraft Logs parse averages, if configured.
    if config.warcraftlogs_enabled:
        try:
            async with WarcraftLogsClient(
                config.warcraftlogs_client_id,  # type: ignore[arg-type]
                config.warcraftlogs_client_secret,  # type: ignore[arg-type]
                config.warcraftlogs_zone_id,  # None => auto-detect current tier
            ) as wcl:
                for m in members:
                    await wcl.enrich_character(m)
            sources.append("Warcraft Logs")
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            log.warning("Warcraft Logs enrichment skipped: %s", exc)

    # 4: persist members.
    present_ids: list[int] = []
    for m in members:
        m.id = await db.upsert_synced(m)
        present_ids.append(m.id)

    # Protect the core invariant: never let a successful-but-empty roster fetch
    # wipe the table. An empty `members` means the upstream returned nothing
    # usable (a transient 200, schema drift, a renamed/transferred guild), and
    # pruning against [] would delete every row, manually-entered fields and all.
    # Skip the prune and warn instead, so the roster (and the human data on it)
    # survives a bad fetch and recovers on the next good sync.
    pruned = 0
    if prune and not members:
        log.warning(
            "Roster fetch returned 0 members; skipping prune to protect the table."
        )
    elif prune:
        pruned = await db.prune_missing(present_ids)

    # 5: persist the wishlists gathered from WoW Audit, refresh completion %.
    wishlist_items = 0
    if wishlists:
        grouped: dict[str, list] = defaultdict(list)
        for w in wishlists:
            grouped[w.character_name].append(w)
        for character, items in grouped.items():
            wishlist_items += await db.replace_wishlist(character, items)
        for m in members:
            _got, total, pct = await db.wishlist_progress(m.character_name)
            if m.id is not None and total:
                await db.set_wishlist_pct(m.id, pct)

    # 5b: persist raid attendance from WoW Audit (recent past raids).
    attendance_events = 0
    for event in attendance:
        event_id = await db.add_raid_event(
            started_at=event.get("date"),
            instance=event.get("instance"),
            difficulty=event.get("difficulty"),
            source="wowaudit",
        )
        for char_name, status in event.get("signups", []):
            await db.add_attendance(event_id, char_name, status=status or "present")
        attendance_events += 1

    # 6: snapshot the guild for trend lines. Pass the previous snapshot's member
    #    count so the stored health_score carries the retention component the live
    #    GM view also computes (so the GM page's health delta compares like for
    #    like instead of a retention-weighted score against one without it).
    stored = await db.all_members()
    prev_snaps = await db.snapshots(limit=1)
    prev_count = prev_snaps[0]["member_count"] if prev_snaps else None
    snapshot = _build_snapshot(stored, config, prev_member_count=prev_count)
    await db.add_snapshot(snapshot)

    return SyncResult(
        fetched=len(members),
        upserted=len(present_ids),
        pruned=pruned,
        enriched=enriched,
        raiders=raiders,
        wishlist_items=wishlist_items,
        attendance_events=attendance_events,
        health_score=snapshot.get("health_score"),
        sources=tuple(sources),
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _select_for_enrichment(
    members: list[Member],
    existing_by_name: dict[str, Member],
    raider_names: set[str],
    *,
    max_age_hours: int,
    limit: int,
    now: datetime | None = None,
) -> list[Member]:
    """Pick which members to deep-enrich this run.

    A member is skipped only if its stored row was enriched (has an item level)
    within ``max_age_hours``. Raiders are always (re)enriched when stale; other
    stale members are taken oldest-first and capped by ``limit`` (0 = no cap).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = timedelta(hours=max_age_hours) if max_age_hours > 0 else None

    raiders_due: list[Member] = []
    others_due: list[Member] = []
    for m in members:
        ex = existing_by_name.get(m.character_name.lower())
        fresh = (
            cutoff is not None
            and ex is not None
            and ex.item_level is not None
            and (dt := _parse_iso(ex.last_synced)) is not None
            and (now - dt) <= cutoff
        )
        if fresh:
            continue
        if m.character_name.lower() in raider_names:
            raiders_due.append(m)
        else:
            others_due.append(m)

    def _last_synced(m: Member) -> str:
        ex = existing_by_name.get(m.character_name.lower())
        return (ex.last_synced if ex and ex.last_synced else "")

    others_due.sort(key=_last_synced)  # oldest (or never-synced) first
    if limit and limit > 0:
        others_due = others_due[:limit]
    return raiders_due + others_due


def _build_snapshot(
    members: list[Member], config: Config, *, prev_member_count: int | None = None
) -> dict:
    """Compute a guild-wide snapshot row (used for trends + health history).

    ``prev_member_count`` (the prior snapshot's member count) feeds the health
    score's retention component, matching how the live GM view computes health,
    so the two stay on the same scale.
    """
    ilvls = [m.item_level for m in members if m.item_level is not None]
    scores = [m.mythic_plus_score for m in members if m.mythic_plus_score is not None]
    summary, _ = metrics.guild_progression(members)
    health = metrics.guild_health(
        members,
        window_days=config.activity_window_days,
        prev_member_count=prev_member_count,
    )
    active = metrics.activity_rate(members, window_days=config.activity_window_days)
    return {
        "taken_at": None,  # filled in by the DB layer
        "member_count": len(members),
        "active_count": int(round((active or 0) * len(members))),
        "avg_item_level": (sum(ilvls) / len(ilvls)) if ilvls else None,
        "avg_mythic_plus": (sum(scores) / len(scores)) if scores else None,
        "progression_summary": summary,
        "role_counts": metrics.role_counts(members),
        "class_counts": metrics.class_counts(members),
        "health_score": health.score,
    }
