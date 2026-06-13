"""View-models for the GM, Raid Leader and Loot views.

These async functions read the database, run the pure functions in
``ftabot.metrics``, and return plain dicts. The web layer (``web.py``) and the
Discord cogs both render from these, so the two surfaces never drift apart.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import metrics
from .config import Config
from .db import Database
from .models import Member
from .wow_data import buff_coverage, missing_buffs


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _days_since(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400)


def _member_brief(m: Member) -> dict:
    return {
        "name": m.character_name,
        "class": m.class_name,
        "spec": m.active_spec,
        "role": m.role,
        "item_level": m.item_level,
        "mythic_plus_score": m.mythic_plus_score,
        "best_parse_avg": m.best_parse_avg,
        "vault_slots_filled": m.vault_slots_filled,
        "wishlist_pct": m.wishlist_pct,
        "guild_rank": m.guild_rank,
        "raid_team": m.raid_team,
    }


# --- member detail ----------------------------------------------------------


async def member_detail(
    db: Database,
    config: Config,
    name: str,
    *,
    realm: str | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Everything we know about one character, for a profile page."""
    now = _now(now)
    realm = realm or config.guild_realm
    m = await db.get_member(name, realm, config.guild_region)
    if m is None:
        return None

    roster = await db.all_members()
    team_ilvls = [x.item_level for x in roster if x.item_level is not None]
    present, scheduled, att = await db.attendance_stats(m.character_name)
    readiness = metrics.compute_readiness(
        m,
        team_item_levels=team_ilvls,
        attendance_pct=att,
        now=now,
        window_days=config.activity_window_days,
    )
    wishlist = await db.get_wishlist(m.character_name)
    got, total, pct = await db.wishlist_progress(m.character_name)
    loot = await db.get_loot(character=m.character_name, limit=50)
    alts = await db.alts_of(m.id) if m.id is not None else []

    return {
        "member": m.to_dict(include_details=True),
        "active": metrics.is_active(m, window_days=config.activity_window_days, now=now),
        "readiness": {
            "score": readiness.score,
            "components": readiness.components,
            "flags": readiness.flags,
        },
        "attendance": {"present": present, "scheduled": scheduled, "pct": att},
        "wishlist": {
            "items": [w.to_dict() for w in wishlist],
            "obtained": got,
            "total": total,
            "pct": pct,
        },
        "loot": [e.to_dict() for e in loot],
        "alts": [a.character_name for a in alts],
        "identity": await db.get_identity(m.character_name, m.realm_slug, m.region),
    }


# --- trends -----------------------------------------------------------------

_TREND_FIELDS = (
    "health_score",
    "member_count",
    "active_count",
    "avg_item_level",
    "avg_mythic_plus",
)


async def _trend_series(db: Database, *, limit: int = 30) -> dict:
    """Chronological snapshot series + first→latest deltas for each metric."""
    snaps = list(reversed(await db.snapshots(limit=limit)))  # oldest -> newest
    series = {f: [s.get(f) for s in snaps] for f in _TREND_FIELDS}
    points = [
        {"taken_at": s["taken_at"], **{f: s.get(f) for f in _TREND_FIELDS}}
        for s in snaps
    ]
    deltas: dict[str, float] = {}
    for f in _TREND_FIELDS:
        vals = [v for v in series[f] if v is not None]
        if len(vals) >= 2:
            deltas[f] = vals[-1] - vals[0]
    return {
        "count": len(snaps),
        "points": points,
        "series": series,
        "deltas": deltas,
        "latest": points[-1] if points else None,
        "first": points[0] if points else None,
    }


async def trends(db: Database, config: Config, *, limit: int = 30) -> dict:
    return await _trend_series(db, limit=limit)


# --- team chemistry roster (next raid sign-ups) -----------------------------

# Buckets the roster is grouped into, in display order. Raw WoW Audit sign-up
# statuses are normalised onto these; anything unrecognised falls into "Other".
ROSTER_BUCKETS: tuple[str, ...] = (
    "Confirmed",
    "Tentative",
    "Benched",
    "Late",
    "Absent",
    "Other",
)

_STATUS_BUCKETS: dict[str, str] = {
    "present": "Confirmed",
    "confirmed": "Confirmed",
    "signed": "Confirmed",
    "selected": "Confirmed",
    "accepted": "Confirmed",
    "tentative": "Tentative",
    "maybe": "Tentative",
    "bench": "Benched",
    "benched": "Benched",
    "backup": "Benched",
    "late": "Late",
    "absent": "Absent",
    "declined": "Absent",
    "decline": "Absent",
    "no": "Absent",
}


def bucket_for_status(status: str | None) -> str:
    """Normalise a raw WoW Audit sign-up status onto a roster bucket."""
    return _STATUS_BUCKETS.get((status or "").strip().lower(), "Other")


def group_signups(
    signups: list[dict], members_by_name: dict[str, Member]
) -> dict[str, list[dict]]:
    """Group raw sign-ups into ordered buckets, enriched with class/role.

    Pure (no I/O): the caller supplies the sign-up rows and a name->Member map,
    so this is unit-testable like the rest of the view layer. Only buckets with
    at least one member are returned, in :data:`ROSTER_BUCKETS` order.
    """
    grouped: dict[str, list[dict]] = {b: [] for b in ROSTER_BUCKETS}
    for s in signups:
        name = s.get("name")
        if not name:
            continue
        m = members_by_name.get(str(name).lower())
        grouped[bucket_for_status(s.get("status"))].append(
            {
                "name": name,
                "status": s.get("status"),
                "class": m.class_name if m else None,
                "role": m.role if m else None,
            }
        )
    for rows in grouped.values():
        rows.sort(key=lambda r: str(r["name"]).lower())
    return {b: rows for b in ROSTER_BUCKETS if (rows := grouped[b])}


async def roster_board(db: Database, config: Config) -> dict:
    """The next raid's sign-ups, grouped confirmed/benched/absent for /roster."""
    state = await db.roster_signups()
    members_by_name = {m.character_name.lower(): m for m in await db.all_members()}
    groups = group_signups(state.get("signups") or [], members_by_name)
    return {
        "raid": state.get("raid") or {},
        "groups": groups,
        "count": sum(len(rows) for rows in groups.values()),
    }


# --- GM view ----------------------------------------------------------------


async def gm_report(
    db: Database, config: Config, *, now: datetime | None = None
) -> dict:
    now = _now(now)
    window = config.activity_window_days
    members = await db.all_members()

    snaps = await db.snapshots(limit=2)
    prev = snaps[1] if len(snaps) > 1 else None

    health = metrics.guild_health(
        members,
        window_days=window,
        prev_member_count=prev["member_count"] if prev else None,
        now=now,
    )

    active = [m for m in members if metrics.is_active(m, window_days=window, now=now)]
    inactive = [m for m in members if m not in active]

    officers = []
    for o in await db.officers(rank_max=config.officer_rank_max):
        _present, _sched, att = await db.attendance_stats(o.character_name)
        officers.append(
            {
                "name": o.character_name,
                "class": o.class_name,
                "guild_rank": o.guild_rank,
                "officer_role": o.officer_role,
                "attendance_pct": att,
                "active": metrics.is_active(o, window_days=window, now=now),
                "last_login": o.last_login,
            }
        )

    summary, _ = metrics.guild_progression(members)
    classes = [m.class_name for m in members if m.class_name]

    return {
        "guild": config.guild_name,
        "health": {
            "score": health.score,
            "components": health.components,
            "previous": prev["health_score"] if prev else None,
        },
        "counts": {
            "total": len(members),
            "active": len(active),
            "inactive": len(inactive),
        },
        "role_counts": metrics.role_counts(members),
        "class_counts": metrics.class_counts(members),
        "prep_rate": metrics.prep_rate(members),
        "vault_rate": metrics.vault_engagement_rate(members),
        "progression": summary,
        "officers": officers,
        "inactive_members": [
            {"name": m.character_name, "last_login": m.last_login}
            for m in sorted(inactive, key=lambda x: x.last_login or "")
        ][:25],
        "buff_coverage": buff_coverage(classes),
        "missing_buffs": missing_buffs(classes),
        "trends": await _trend_series(db, limit=30),
    }


# --- Raid Leader view -------------------------------------------------------


async def raid_board(
    db: Database,
    config: Config,
    *,
    team: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = _now(now)
    window = config.activity_window_days
    members = await db.team_members(team) if team else await db.all_members()
    team_ilvls = [m.item_level for m in members if m.item_level is not None]

    rows = []
    for m in members:
        _present, _sched, att = await db.attendance_stats(m.character_name)
        readiness = metrics.compute_readiness(
            m,
            team_item_levels=team_ilvls,
            attendance_pct=att,
            now=now,
            window_days=window,
        )
        rows.append(
            {
                **_member_brief(m),
                "attendance_pct": att,
                "readiness": readiness.score,
                "components": readiness.components,
                "flags": readiness.flags,
            }
        )
    rows.sort(key=lambda r: r["readiness"], reverse=True)

    classes = [m.class_name for m in members if m.class_name]
    return {
        "team": team,
        "count": len(members),
        "rows": rows,
        "role_counts": metrics.role_counts(members),
        "buff_coverage": buff_coverage(classes),
        "missing_buffs": missing_buffs(classes),
    }


# --- Loot view --------------------------------------------------------------


async def loot_overview(
    db: Database, config: Config, *, limit: int = 50, now: datetime | None = None
) -> dict:
    now = _now(now)
    members = await db.all_members()
    counts = await db.loot_counts()

    fairness = []
    for m in members:
        won = counts.get(m.character_name.lower(), 0)
        present, _sched, att = await db.attendance_stats(m.character_name)
        char_loot = await db.get_loot(character=m.character_name, limit=1)
        last_at = char_loot[0].awarded_at if char_loot else None
        per_raid = (won / present) if present else None
        fairness.append(
            {
                "name": m.character_name,
                "items_won": won,
                "raids_attended": present,
                "items_per_raid": round(per_raid, 2) if per_raid is not None else None,
                "attendance_pct": att,
                "last_loot_at": last_at,
                "days_since_last_loot": _days_since(last_at, now),
            }
        )
    fairness.sort(key=lambda r: r["items_won"], reverse=True)

    recent = [e.to_dict() for e in await db.get_loot(limit=limit)]
    return {"recent": recent, "fairness": fairness, "total_awards": sum(counts.values())}


async def loot_priority(
    db: Database,
    config: Config,
    *,
    item_name: str | None = None,
    item_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    now = _now(now)
    wishlisted = await db.wishlist_for_item(item_id=item_id, item_name=item_name)

    candidates = []
    for w in wishlisted:
        _present, _sched, att = await db.attendance_stats(w.character_name)
        char_loot = await db.get_loot(character=w.character_name, limit=50)
        recent_wins = sum(
            1
            for e in char_loot
            if (_days_since(e.awarded_at, now) or 999) <= 30
        )
        last_at = char_loot[0].awarded_at if char_loot else None
        candidates.append(
            metrics.LootCandidate(
                character_name=w.character_name,
                is_bis=w.is_bis,
                upgrade_pct=w.upgrade_pct,
                attendance_pct=att,
                days_since_last_loot=_days_since(last_at, now),
                recent_wins=recent_wins,
            )
        )

    ranked = metrics.rank_loot_priority(candidates)
    return {
        "item": item_name or item_id,
        "ranked": [
            {
                "character": c.character_name,
                "priority": score,
                "is_bis": c.is_bis,
                "upgrade_pct": c.upgrade_pct,
                "attendance_pct": c.attendance_pct,
                "days_since_last_loot": (
                    round(c.days_since_last_loot, 1)
                    if c.days_since_last_loot is not None
                    else None
                ),
                "recent_wins": c.recent_wins,
            }
            for c, score in ranked
        ],
    }
