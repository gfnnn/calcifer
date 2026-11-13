"""Raider.IO API client.

Raider.IO is the workhorse of this bot: it needs no authentication and exposes
both the full guild roster (with per-member guild rank, class and spec) and
per-character Mythic+ scores, item level and raid progression.

Docs: https://raider.io/api
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .. import raids
from ..models import CURRENT_EXPANSION_ID, Member, normalize_realm
from ._http import BaseHTTPClient, retry_after_seconds

log = logging.getLogger("ftabot.raiderio")

BASE_URL = "https://raider.io/api/v1"

# Fields we request when enriching a single character.
_CHARACTER_FIELDS = "gear,mythic_plus_scores_by_season:current,raid_progression"


# The raid catalog (names, boss counts, open/close dates) changes a few times a
# year, so one fetch is shared by every client in the process for a few hours
# rather than repeated on each score-refresh tick. Keyed by (expansion, region).
_CATALOG_TTL_SECONDS = 6 * 3600
_catalog_cache: dict[tuple[int, str], tuple[float, list[raids.RaidInfo]]] = {}


class RaiderIOError(RuntimeError):
    """Raised when Raider.IO returns an error we can't recover from."""


class RaiderIOClient(BaseHTTPClient):
    """Async Raider.IO client. Use as an async context manager."""

    # The raid catalog this client labels progression with; ``None`` until
    # ``raid_catalog`` has been called (then possibly ``[]`` if unavailable).
    _catalog: list[raids.RaidInfo] | None = None

    def _session_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def _get(
        self, path: str, params: dict[str, Any], *, retries: int = 4
    ) -> dict[str, Any]:
        """GET with backoff on 429/5xx.

        Raider.IO rate-limits bursts (e.g. enriching a 600+ character guild), so
        we retry on 429 — honouring ``Retry-After`` — and on transient 5xx,
        with exponential backoff, before giving up.
        """
        delay = 1.0
        for attempt in range(retries + 1):
            async with self.session.get(f"{BASE_URL}{path}", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 404:
                    raise RaiderIOError("not found")
                retryable = resp.status == 429 or 500 <= resp.status < 600
                if retryable and attempt < retries:
                    wait = (
                        retry_after_seconds(resp.headers, fallback=delay)
                        if resp.status == 429
                        else delay
                    )
                    await asyncio.sleep(min(wait, 10.0))
                    delay *= 2
                    continue
                body = await resp.text()
                raise RaiderIOError(f"HTTP {resp.status}: {body[:200]}")
        raise RaiderIOError("retries exhausted")

    # -- guild ---------------------------------------------------------------

    async def guild_members(
        self, name: str, realm: str, region: str
    ) -> list[Member]:
        """Return the guild roster as partially-populated Member objects.

        Each member carries name/realm/region, class, spec, role, guild rank
        and profile URL. Scores/ilvl come later from :meth:`enrich_character`.
        """
        data = await self._get(
            "/guilds/profile",
            {
                "region": region,
                "realm": realm,
                "name": name,
                "fields": "members",
            },
        )
        realm_slug = normalize_realm(realm)
        members: list[Member] = []
        for entry in data.get("members", []):
            char = entry.get("character") or {}
            char_name = char.get("name")
            if not char_name:
                continue
            members.append(
                Member(
                    character_name=char_name,
                    realm_slug=normalize_realm(char.get("realm", realm_slug)),
                    region=(char.get("region") or region).lower(),
                    class_name=char.get("class"),
                    active_spec=char.get("active_spec_name"),
                    role=(char.get("active_spec_role") or "").upper() or None,
                    faction=char.get("faction"),
                    guild_rank=entry.get("rank"),
                    profile_url=char.get("profile_url"),
                    last_crawled_at=char.get("last_crawled_at"),
                )
            )
        return members

    async def guild_overview(
        self, name: str, realm: str, region: str
    ) -> dict[str, Any]:
        """The guild's raid progression and standings in one call.

        Returns ``{"raid_progression": {slug: {...}}, "raid_rankings": {slug:
        {difficulty: {world, region, realm}}}}`` (either may be empty).
        """
        data = await self._get(
            "/guilds/profile",
            {
                "region": region,
                "realm": realm,
                "name": name,
                "fields": "raid_progression,raid_rankings",
            },
        )
        return {
            "raid_progression": data.get("raid_progression") or {},
            "raid_rankings": data.get("raid_rankings") or {},
        }

    async def raid_catalog(
        self, region: str, *, expansion_id: int = CURRENT_EXPANSION_ID
    ) -> list[raids.RaidInfo]:
        """The current expansion's raids (name, boss count, open/close dates).

        Best-effort and cached: any failure yields ``[]``, which every caller
        treats as "label raids the old way", so the catalog can improve a
        result but never break a sync. Also remembered on the client so
        ``enrich_character`` labels progression with it.
        """
        key = (expansion_id, region.lower())
        cached = _catalog_cache.get(key)
        if cached and time.monotonic() - cached[0] < _CATALOG_TTL_SECONDS:
            self._catalog = cached[1]
            return cached[1]
        try:
            data = await self._get(
                "/raiding/static-data", {"expansion_id": expansion_id}
            )
            catalog = raids.parse_catalog(data, region)
        except Exception as exc:  # noqa: BLE001 - the catalog is an optional nicety
            log.warning("Raider.IO raid catalog unavailable: %s", exc)
            catalog = []
        if catalog:  # never cache a miss, so the next call retries
            _catalog_cache[key] = (time.monotonic(), catalog)
        self._catalog = catalog
        return catalog

    async def guild_raid_progression(
        self, name: str, realm: str, region: str
    ) -> dict[str, Any]:
        data = await self._get(
            "/guilds/profile",
            {
                "region": region,
                "realm": realm,
                "name": name,
                "fields": "raid_progression",
            },
        )
        return data.get("raid_progression", {})

    async def guild_strength(self, name: str, realm: str, region: str) -> float | None:
        """0..1 estimate of a guild's current-tier raid strength (mythic kills / total).

        Returns None when the guild can't be found or has no progression data.
        Used to anchor the poachability calculation against the candidate's guild.
        """
        try:
            prog = await self.guild_raid_progression(name, realm, region)
        except RaiderIOError:
            return None
        if not prog:
            return None
        # raid_progression maps a raid slug -> a dict of kill counts. Skip any
        # non-dict value (defensive: the API shape can drift) so a malformed
        # entry yields None rather than an AttributeError into the scout.
        raid = next((v for v in prog.values() if isinstance(v, dict)), None)
        if raid is None:
            return None
        total = raid.get("total_bosses") or 0
        mythic = raid.get("mythic_bosses_killed") or 0
        if total == 0:
            return None
        return round(min(1.0, mythic / total), 3)

    # -- character -----------------------------------------------------------

    async def enrich_character(self, member: Member) -> Member:
        """Fill in item level, Mythic+ score and raid progression in place."""
        # Load the raid catalog on first use (cached for hours process-wide), so
        # every caller labels progression against the raid that is current.
        if self._catalog is None:
            await self.raid_catalog(member.region)
        try:
            data = await self._get(
                "/characters/profile",
                {
                    "region": member.region,
                    "realm": member.realm_slug,
                    "name": member.character_name,
                    "fields": _CHARACTER_FIELDS,
                },
            )
        except RaiderIOError:
            # Character may be too low level / not yet crawled; leave as-is.
            return member

        member.class_name = data.get("class") or member.class_name
        member.active_spec = data.get("active_spec_name") or member.active_spec
        member.role = (data.get("active_spec_role") or "").upper() or member.role
        member.race = data.get("race") or member.race
        member.faction = data.get("faction") or member.faction
        member.thumbnail_url = data.get("thumbnail_url") or member.thumbnail_url
        member.profile_url = data.get("profile_url") or member.profile_url
        member.last_crawled_at = data.get("last_crawled_at") or member.last_crawled_at

        gear = data.get("gear") or {}
        if gear.get("item_level_equipped") is not None:
            member.item_level = float(gear["item_level_equipped"])

        seasons = data.get("mythic_plus_scores_by_season") or []
        if seasons:
            scores = seasons[0].get("scores") or {}
            if scores.get("all") is not None:
                member.mythic_plus_score = float(scores["all"])

        progression = data.get("raid_progression") or {}
        member.raid_progression = _summarize_progression(progression, self._catalog)
        # Keep the full per-raid breakdown so /member can show every raid, open
        # raids first, with their real names alongside (Raider.IO keys by slug).
        if progression:
            ordered = raids.order_progression(progression, self._catalog)
            member.details = {
                **(member.details or {}),
                "raid_progression": ordered,
                "raid_names": {
                    slug: raids.raid_name(slug, self._catalog) for slug in ordered
                },
            }
        return member

    async def character_card(
        self, name: str, realm: str, region: str, seasons: list[str]
    ) -> dict[str, Any] | None:
        """Fetch a recruitment candidate's overview in one call.

        Returns class/spec/role, equipped ilvl, current guild, a Mythic+ score
        per requested season (aligned to ``seasons`` order, oldest first, ``None``
        where the player has no record), and an ``lfg_active`` flag when the
        character has an active Looking For Guild profile on Raider.IO.
        """
        fields = (
            "gear,guild,mythic_plus_scores_by_season:"
            + ":".join(seasons)
            + ",recruitmentProfiles"
        )
        try:
            data = await self._get(
                "/characters/profile",
                {"region": region, "realm": realm, "name": name, "fields": fields},
            )
        except RaiderIOError:
            return None

        by_season: dict[str, float | None] = {}
        for entry in data.get("mythic_plus_scores_by_season") or []:
            sid = entry.get("season")
            score = (entry.get("scores") or {}).get("all")
            if sid:
                by_season[sid] = float(score) if score is not None else None

        gear = data.get("gear") or {}
        guild = data.get("guild") or {}
        ilvl = gear.get("item_level_equipped")
        # recruitmentProfiles is a non-empty array when the character has an
        # active LFG profile on Raider.IO; absent or [] when they don't.
        rec = data.get("recruitmentProfiles")
        lfg_active = bool(rec)
        return {
            "name": data.get("name") or name,
            "realm_slug": normalize_realm(data.get("realm") or realm),
            "region": (data.get("region") or region).lower(),
            "class_name": data.get("class"),
            "active_spec": data.get("active_spec_name"),
            "role": (data.get("active_spec_role") or "").upper() or None,
            "item_level": float(ilvl) if ilvl is not None else None,
            "guild_name": guild.get("name") if isinstance(guild, dict) else None,
            "mplus_seasons": [by_season.get(s) for s in seasons],
            "lfg_active": lfg_active,
        }

    async def current_affixes(self, region: str) -> list[dict[str, Any]]:
        """Return this week's M+ affixes for a region.

        Each affix is a dict with keys: id, name, description, wowhead_url, icon.
        Returns an empty list when the endpoint is unavailable.
        """
        try:
            data = await self._get(
                "/mythic-plus/affixes", {"region": region.lower(), "locale": "en"}
            )
        except RaiderIOError:
            return []
        return data.get("affix_details") or []

    async def realm_raid_progression(
        self,
        raid_slug: str,
        region: str,
        realm: str,
        difficulty: str = "mythic",
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the raid guild standings for a realm, ordered by progression.

        Each entry is a RaidRankingEntry with ``guild`` (name/faction),
        ``rank``, and ``encountersDefeated`` (list of {slug, firstDefeated,
        lastDefeated}). Returns an empty list when the realm or raid isn't found.
        """
        try:
            data = await self._get(
                "/raiding/raid-rankings",
                {
                    "region": region.lower(),
                    "realm": realm.lower(),
                    "raid": raid_slug,
                    "difficulty": difficulty.lower(),
                    "limit": limit,
                },
            )
        except RaiderIOError:
            return []
        return data.get("raidRankings") or []

    async def recent_runs(
        self, name: str, realm: str, region: str
    ) -> list[dict[str, Any]]:
        """Return a character's recent Mythic+ runs (for the M+ feed)."""
        try:
            data = await self._get(
                "/characters/profile",
                {
                    "region": region,
                    "realm": realm,
                    "name": name,
                    "fields": "mythic_plus_recent_runs",
                },
            )
        except RaiderIOError:
            return []
        out: list[dict[str, Any]] = []
        for r in data.get("mythic_plus_recent_runs") or []:
            out.append(
                {
                    "dungeon": r.get("dungeon"),
                    "short_name": r.get("short_name"),
                    "level": r.get("mythic_level"),
                    "upgrades": r.get("num_keystone_upgrades"),
                    "completed_at": r.get("completed_at"),
                    "url": r.get("url"),
                    "score": r.get("score"),
                }
            )
        return out

    async def enrich_all(
        self, members: list[Member], *, concurrency: int = 2, chunk_size: int = 25
    ) -> list[Member]:
        """Enrich members with bounded concurrency, processed in chunks.

        Chunking keeps the number of in-flight requests and pending tasks flat
        regardless of roster size, so a full sync of a large guild stays gentle
        on memory (rather than scheduling one coroutine per member at once).
        """
        semaphore = asyncio.Semaphore(max(1, concurrency))
        # One catalog fetch up front, so every member's progression is labelled
        # against the raid that is actually current (see ``raids.py``).
        if members and self._catalog is None and self._session is not None:
            await self.raid_catalog(members[0].region)

        async def _one(m: Member) -> Member:
            async with semaphore:
                return await self.enrich_character(m)

        enriched: list[Member] = []
        for start in range(0, len(members), max(1, chunk_size)):
            batch = members[start : start + chunk_size]
            # return_exceptions so one member's unexpected error (e.g. an
            # aiohttp timeout that enrich_character doesn't wrap) doesn't cancel
            # the rest of the batch; keep the un-enriched member in that case.
            results = await asyncio.gather(
                *(_one(m) for m in batch), return_exceptions=True
            )
            for m, res in zip(batch, results):
                if isinstance(res, Member):
                    enriched.append(res)
                elif isinstance(res, Exception):
                    log.warning(
                        "Raider.IO enrich failed for %s-%s: %s",
                        m.character_name,
                        m.realm_slug,
                        res,
                    )
                    enriched.append(m)
                else:  # a BaseException (e.g. CancelledError) — never swallow it
                    raise res
        return enriched


def _summarize_progression(
    progression: dict[str, Any], catalog: list[raids.RaidInfo] | None = None
) -> str | None:
    """Turn Raider.IO raid_progression into a compact label.

    Describes the raid that is current per the ``catalog`` (the biggest raid
    open right now), e.g. "Nerub-ar Palace: 8/8 N, 6/8 H, 2/8 M". Without a
    catalog it falls back to the first raid in the payload, which is not
    reliably the current one: Raider.IO's ordering is not chronological.
    """
    return raids.summarize(progression, catalog)
