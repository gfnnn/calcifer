"""Recruitment scout orchestration: roster gap-analysis → market scan.

Ties the pieces together: read our synced roster, find weak slots, discover
top performers of the same class via Warcraft Logs, vet each through Raider.IO +
WCL parse history, and rank the upgrades. The maths lives in :mod:`ftabot.recruit`
(pure, tested); this module does the I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import recruit
from .config import Config
from .db import Database
from .models import CLASSES
from .services.raiderio import RaiderIOClient
from .services.warcraftlogs import WarcraftLogsClient, metric_for

log = logging.getLogger("ftabot.scout")


class ScoutUnavailable(RuntimeError):
    """Raised when the scout cannot run (e.g. Warcraft Logs creds missing)."""


@dataclass
class SlotReport:
    slot: recruit.WeakSlot
    matches: list[recruit.ScoutMatch]


def _normalise_class(name: str) -> str | None:
    """Map free-text user input to a canonical class name, or None."""
    want = name.strip().lower()
    for klass in CLASSES:
        if klass.lower() == want:
            return klass
    return None


def _slot_for_class(roster: list, class_name: str, config: Config) -> recruit.WeakSlot:
    """Build a slot for an explicitly-requested class (weakest holder, if any)."""
    holders = [m for m in roster if (m.class_name or "").lower() == class_name.lower()]
    if holders:
        weakest = min(holders, key=lambda m: recruit.player_strength(m, roster))
        return recruit.WeakSlot(
            class_name=class_name,
            spec=weakest.active_spec,
            role=weakest.role,
            incumbent=weakest.character_name,
            incumbent_strength=recruit.player_strength(weakest, roster),
            incumbent_parse=weakest.best_parse_avg,
            incumbent_mplus=weakest.mythic_plus_score,
        )
    # Nobody of this class rostered: treat as an open slot at a neutral bar.
    return recruit.WeakSlot(
        class_name=class_name, spec=None, role=None,
        incumbent="(none rostered)", incumbent_strength=0.5,
    )


async def run_scout(
    db: Database,
    config: Config,
    *,
    only_class: str | None = None,
    max_slots: int = 3,
    per_slot: int = 5,
    candidate_pool: int = 8,
) -> list[SlotReport]:
    """Scout upgrades for our weak slots (or one named class).

    Bounded on purpose — at most ``max_slots`` slots, ``candidate_pool`` players
    vetted each — so an on-demand command stays within API budgets.
    """
    if not config.scout_credentials:
        raise ScoutUnavailable(
            "The scout needs Warcraft Logs API credentials and a tier zone id "
            "(WARCRAFTLOGS_CLIENT_ID / _SECRET / _ZONE_ID[S])."
        )

    roster = await db.all_members()
    if not roster:
        return []

    if only_class:
        canonical = _normalise_class(only_class)
        if canonical is None:
            raise ScoutUnavailable(f"Unknown class '{only_class}'.")
        slots = [_slot_for_class(roster, canonical, config)]
    else:
        slots = recruit.weak_slots(roster, threshold=config.recruit_weak_threshold)[:max_slots]

    if not slots:
        return []

    roster_names = {m.character_name.lower() for m in roster}
    our_guild = config.guild_name.lower()
    zone_ids = list(config.scout_zone_ids)
    scan_zone = zone_ids[-1]  # discover on the newest tier
    seasons = list(config.recruit_seasons)
    req = recruit.Requirements(
        min_item_level=config.recruit_min_item_level,
        roles_open=tuple(config.recruit_roles_open),
        region=config.guild_region,
    )

    reports: list[SlotReport] = []
    async with WarcraftLogsClient(
        config.warcraftlogs_client_id,  # type: ignore[arg-type]
        config.warcraftlogs_client_secret,  # type: ignore[arg-type]
        scan_zone,
    ) as wcl, RaiderIOClient() as rio:
        encounters = await wcl.zone_encounters(scan_zone)
        if not encounters:
            raise ScoutUnavailable(f"No encounters found for zone {scan_zone}.")
        encounter_ids = _sample_encounters(encounters)

        # Fetch our own guild's progression once to anchor poachability scores.
        our_strength = await rio.guild_strength(
            config.guild_name, config.guild_realm, config.guild_region
        )

        for slot in slots:
            metric = metric_for(slot.spec, slot.role)
            # Merge rankings across a spread of encounters for a wider pool.
            rankings: list[dict] = []
            for enc_id in encounter_ids:
                batch = await wcl.character_rankings(
                    enc_id,
                    class_name=slot.class_name,
                    spec_name=slot.spec,
                    metric=metric,
                    region=config.guild_region,
                    pages=1,
                )
                rankings.extend(batch)
            picks = _dedup_pool(rankings, roster_names, our_guild, candidate_pool)
            candidates = await _vet(rio, wcl, picks, slot, seasons, zone_ids, config)
            matches = recruit.rank_candidates(
                candidates, slot, req, our_strength=our_strength
            )[:per_slot]
            reports.append(SlotReport(slot=slot, matches=matches))

    return reports


def lead_dicts(reports: list[SlotReport]) -> list[dict]:
    """Flatten scout reports into lead rows for ``db.upsert_lead`` (pure).

    One row per matched candidate, carrying the discovered fields the funnel
    tracks. Dedupe is left to the DB's identity key, so the same player surfaced
    under two slots updates one lead.
    """
    leads: list[dict] = []
    for report in reports:
        for match in report.matches:
            c = match.candidate
            leads.append(
                {
                    "name": c.name,
                    "realm": c.realm,
                    "region": c.region,
                    "class_name": c.class_name,
                    "spec": c.spec,
                    "role": c.role,
                    "item_level": c.item_level,
                    "score": match.score,
                    "lfg": c.lfg_active,
                    "guild_name": c.guild_name,
                    "source": "scout",
                }
            )
    return leads


def _sample_encounters(encounters: list[dict]) -> list[int]:
    """Pick a spread of encounter ids (first, mid, last) for a wider candidate pool.

    The end boss has the fewest parses — fewer guilds reach it. Sampling a spread
    gives a more diverse pool without tripling WCL calls; for short tiers (<=3
    bosses) we just use all of them.
    """
    ids = [e["id"] for e in encounters if e.get("id") is not None]
    if len(ids) <= 3:
        return ids
    mid = len(ids) // 2
    return list(dict.fromkeys([ids[0], ids[mid], ids[-1]]))  # dedup, preserve order


def _dedup_pool(rankings, roster_names, our_guild, limit):
    """Drop our own players/guild and duplicates; cap the pool."""
    picks: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in rankings:
        name = r.get("name")
        if not name:
            continue
        key = (name.lower(), r.get("realm_slug") or "")
        if key in seen or name.lower() in roster_names:
            continue
        if (r.get("guild_name") or "").lower() == our_guild:
            continue
        seen.add(key)
        picks.append(r)
        if len(picks) >= limit:
            break
    return picks


async def _vet(rio, wcl, picks, slot, seasons, zone_ids, config) -> list[recruit.Candidate]:
    """Enrich each discovered player into a scored-ready Candidate."""
    candidates: list[recruit.Candidate] = []
    for r in picks:
        realm = r.get("realm_slug") or config.guild_realm
        region = r.get("region") or config.guild_region
        card = await rio.character_card(r["name"], realm, region, seasons)
        if card is None:
            continue
        parse_curve = await wcl.parse_curve(
            r["name"], card["realm_slug"], card["region"], zone_ids
        )
        guild_strength: float | None = None
        if card.get("guild_name"):
            try:
                guild_strength = await rio.guild_strength(
                    card["guild_name"], card["realm_slug"], card["region"]
                )
            except Exception:  # noqa: BLE001 - one bad lookup never aborts vetting
                pass
        candidates.append(
            recruit.Candidate(
                name=card["name"],
                realm=card["realm_slug"],
                region=card["region"],
                class_name=card["class_name"] or slot.class_name,
                spec=card["active_spec"] or slot.spec,
                role=card.get("role"),
                item_level=card["item_level"],
                parse_seasons=parse_curve,
                mplus_seasons=card["mplus_seasons"],
                guild_name=card["guild_name"] or r.get("guild_name"),
                guild_strength=guild_strength,
                lfg_active=card.get("lfg_active", False),
            )
        )
    return candidates
