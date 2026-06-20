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


def _sparkline_points(
    values: list, *, width: int = 200, height: int = 36
) -> str:
    """A polyline ``"x,y x,y …"`` string for an inline-SVG sparkline.

    Keeps the geometry in (unit-tested) Python so the dashboard JS only has to
    drop the string into an ``<svg>`` — it does no math. ``None`` values are
    skipped; fewer than two known points yields an empty string (the client then
    renders a "no trend yet" placeholder). Mirrors the Python ``_sparkline`` and
    the JS ``sparkline`` fallback.
    """
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return ""
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1
    step = width / max(1, len(values) - 1)
    coords = [
        f"{i * step:.1f},{height - ((v - lo) / span) * (height - 4) - 2:.1f}"
        for i, v in enumerate(values)
        if v is not None
    ]
    return " ".join(coords)


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
        "sparklines": {f: _sparkline_points(series[f]) for f in _TREND_FIELDS},
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


# --- Admin: 360 overview / command center -----------------------------------


async def overview(db: Database, config: Config) -> dict:
    """A command-center snapshot: feature status, the live posting guardrail,
    headline counts and the latest guild-health point. The web endpoint merges
    the live sync status (held in the app, not the DB) on top of this."""
    from .runtime_settings import effective_post_gate

    boards = await db.boards(enabled_only=False)
    rules = await db.all_feed_rules()
    snaps = await db.snapshots(limit=1)
    leads = await db.leads(limit=500)
    gate = await effective_post_gate(db, config)
    return {
        "guild": config.guild_name,
        "posting": {"mode": gate.mode.value, "test_channel_id": gate.test_channel_id},
        "features": {
            "wowaudit": config.wowaudit_enabled,
            "blizzard": config.blizzard_enabled,
            "warcraftlogs": config.warcraftlogs_enabled,
            "scout": config.scout_credentials,
            "feeds": config.feeds_enabled,
            "boards": config.boards_enabled,
            "scores": config.scores_enabled,
            "bot_commands": config.bot_commands_enabled,
        },
        "counts": {
            "members": await db.count(),
            "boards": len(boards),
            "boards_promoted": sum(1 for b in boards if b["promoted"]),
            "feed_rules": len(rules),
            "feed_rules_enabled": sum(1 for r in rules if r["enabled"]),
            "leads": len(leads),
        },
        "health": snaps[0]["health_score"] if snaps else None,
        "health_at": snaps[0]["taken_at"] if snaps else None,
    }


# --- Home: pulse + "needs attention" triage ---------------------------------

# Triage thresholds for the Home queue (module constants so they are easy to
# tune and to assert against in tests).
COLD_LEAD_DAYS = 7.0
LOW_READINESS = 50.0
SYNC_STALE_DAYS_HIGH = 3.0
SYNC_STALE_DAYS_MED = 1.0

_SEVERITY_RANK = {"high": 3, "med": 2, "low": 1}


def _names_detail(names: list, cap: int = 6) -> str:
    """Join a list of names for a triage item's one-line detail, capped."""
    clean = [str(n) for n in names if n]
    shown = clean[:cap]
    text = ", ".join(shown)
    extra = len(clean) - len(shown)
    if extra > 0:
        text += f" +{extra} more"
    return text


def attention(
    *,
    inactive_raiders: list[str],
    unprepped_raiders: list[str],
    low_readiness: list[str],
    gm: dict,
    roster: dict,
    boards: dict,
    feeds: dict,
    pages: dict,
    recruiting: dict,
    leads: list[dict],
    snapshot_age_days: float | None,
    sync: dict | None,
    now: datetime,
) -> list[dict]:
    """Rank the guild's open issues into a triage queue. Pure (no I/O).

    Takes the already-fetched view dicts plus the derived raider name lists, so
    the ranking and formatting are unit-testable without a database. Returns
    items ordered high->low severity, then by count, each shaped
    ``{kind, severity, title, detail, count, action_url}``.
    """
    items: list[dict] = []

    def add(kind: str, severity: str, title: str, detail: str,
            count: int, action_url: str) -> None:
        items.append({
            "kind": kind, "severity": severity, "title": title,
            "detail": detail, "count": count, "action_url": action_url,
        })

    def s(n: int) -> str:
        return "s" if n != 1 else ""

    if inactive_raiders:
        n = len(inactive_raiders)
        add("inactive_raiders", "med", f"{n} raider{s(n)} inactive",
            _names_detail(inactive_raiders), n, "#raid")
    if unprepped_raiders:
        n = len(unprepped_raiders)
        add("unprepped_raiders", "med", f"{n} raider{s(n)} missing enchants/gems",
            _names_detail(unprepped_raiders), n, "#raid/readiness")
    if low_readiness:
        n = len(low_readiness)
        add("low_readiness", "med",
            f"{n} raider{s(n)} below readiness {int(LOW_READINESS)}",
            _names_detail(low_readiness), n, "#raid")

    tentative = (roster.get("groups") or {}).get("Tentative") or []
    if tentative:
        n = len(tentative)
        add("pending_signups", "low", f"{n} tentative sign-up{s(n)} for next raid",
            _names_detail([r.get("name") for r in tentative]), n, "#raid/signups")

    cold = [
        ld for ld in leads
        if ld.get("state") == "new"
        and (_days_since(ld.get("updated_at") or ld.get("first_seen"), now) or 0)
        > COLD_LEAD_DAYS
    ]
    if cold:
        n = len(cold)
        add("cold_leads", "med", f"{n} new lead{s(n)} untouched >{int(COLD_LEAD_DAYS)}d",
            _names_detail([ld.get("name") for ld in cold]), n, "#recruitment")

    slots = (recruiting.get("config") or {}).get("slots") or {}
    open_roles = [r for r, slot in slots.items() if int((slot or {}).get("open") or 0) > 0]
    if open_roles:
        n = len(open_roles)
        add("open_slots", "low", f"{n} role{s(n)} recruiting",
            ", ".join(sorted(open_roles)), n, "#recruitment")

    bad_boards = [
        b for b in boards.get("boards", [])
        if (b["enabled"] and not b["effective_destination"])
        or (b["promoted"] and not b["posted"])
    ]
    if bad_boards:
        n = len(bad_boards)
        add("board_issues", "high", f"{n} board{s(n)} not landing",
            _names_detail([b["name"] for b in bad_boards]), n, "#comms/boards")

    backlog = (feeds.get("pending_outbox") or 0) + (feeds.get("pending_digests") or 0)
    if backlog:
        add("feed_backlog", "high", f"{backlog} feed message{s(backlog)} queued",
            "Outbox/digest queue not draining.", backlog, "#comms/feeds")
    muted = feeds.get("muted_sources") or []
    if muted:
        n = len(muted)
        add("feed_muted", "low", f"{n} feed source{s(n)} muted",
            ", ".join(muted), n, "#comms/feeds")

    unplaced = [p for p in pages.get("pages", []) if not p["placed"]]
    if unplaced:
        n = len(unplaced)
        add("pages_unplaced", "low", f"{n} page{s(n)} not posted",
            _names_detail([p["title"] for p in unplaced]), n, "#comms/pages")

    # Sync staleness: prefer the newest snapshot's age, else the live sync's
    # last-finished time (passed in by the endpoint).
    age = snapshot_age_days
    if age is None and sync:
        age = _days_since(sync.get("last_finished"), now)
    if age is not None and age > SYNC_STALE_DAYS_MED:
        sev = "high" if age > SYNC_STALE_DAYS_HIGH else "med"
        add("sync_stale", sev, f"Last sync {int(age)}d ago",
            "Roster data is going stale — run a sync.", 1, "#settings/sync")

    dark = [o["name"] for o in gm.get("officers", []) if o.get("active") is False]
    if dark:
        n = len(dark)
        add("officer_inactive", "med", f"{n} officer{s(n)} inactive",
            _names_detail(dark), n, "#roster")

    items.sort(key=lambda it: (_SEVERITY_RANK[it["severity"]], it["count"]), reverse=True)
    return items


async def home(db: Database, config: Config, *, now: datetime | None = None) -> dict:
    """The opinionated landing view: a guild-health pulse strip + a ranked
    "needs attention" triage queue. Composes the existing view-models so the
    dashboard never disagrees with the GM/Raid/Comms surfaces. The web endpoint
    merges the live :class:`SyncManager` status onto the result."""
    now = _now(now)
    window = config.activity_window_days

    gm = await gm_report(db, config, now=now)
    roster = await roster_board(db, config)
    boards = await boards_status(db, config)
    feeds = await feed_health(db, config)
    pages = await pages_status(db, config)
    recruit = await recruiting(db, config)
    leads = await db.leads(limit=200)
    snaps = await db.snapshots(limit=1)
    snapshot_age = _days_since(snaps[0]["taken_at"], now) if snaps else None

    # Raider-specific signals (raiders = members with a raid_team). Readiness is
    # scored against the raiders' own item-level spread.
    raiders = await db.team_roster()
    raider_ilvls = [m.item_level for m in raiders if m.item_level is not None]
    inactive_raiders: list[str] = []
    unprepped: list[str] = []
    low_readiness: list[str] = []
    for m in raiders:
        if not metrics.is_active(m, window_days=window, now=now):
            inactive_raiders.append(m.character_name)
        if metrics.prep_ready(m) is False:
            unprepped.append(m.character_name)
        rr = metrics.compute_readiness(
            m, team_item_levels=raider_ilvls, now=now, window_days=window
        )
        if rr.score < LOW_READINESS:
            low_readiness.append(m.character_name)

    queue = attention(
        inactive_raiders=inactive_raiders,
        unprepped_raiders=unprepped,
        low_readiness=low_readiness,
        gm=gm, roster=roster, boards=boards, feeds=feeds, pages=pages,
        recruiting=recruit, leads=leads, snapshot_age_days=snapshot_age,
        sync=None, now=now,
    )

    return {
        "guild": config.guild_name,
        "pulse": {
            "health": gm["health"],
            "counts": gm["counts"],
            "prep_rate": gm["prep_rate"],
            "vault_rate": gm["vault_rate"],
            "progression": gm["progression"],
            "missing_buffs": gm["missing_buffs"],
            "buff_coverage": gm["buff_coverage"],
            "role_counts": gm["role_counts"],
            "trends": gm["trends"],
        },
        "attention": queue,
        "attention_total": sum(it["count"] for it in queue),
        "generated_at": now.isoformat(),
    }


# --- Admin: board status (the management table) ------------------------------


async def boards_status(db: Database, config: Config) -> dict:
    """Every board decorated with its friendly name and the channel it would
    actually post to under the live guardrail, plus the creatable types."""
    from .boards import REGISTRY, board_name
    from .runtime_settings import effective_post_gate

    gate = await effective_post_gate(db, config)
    boards = []
    for b in await db.boards(enabled_only=False):
        destination = gate.resolve(b["channel_id"], promoted=b["promoted"])
        state = (
            "disabled"
            if not b["enabled"]
            else ("live" if b["promoted"] else "sandboxed")
        )
        boards.append(
            {
                "id": b["id"],
                "type": b["board_type"],
                "name": board_name(b["board_type"]),
                "channel_id": b["channel_id"],
                "enabled": b["enabled"],
                "promoted": b["promoted"],
                "posted": bool(b["message_id"]),
                "posted_channel_id": b["posted_channel_id"],
                "effective_destination": destination,
                "state": state,
                "config": b["config"],
            }
        )
    return {
        "boards": boards,
        "available_types": [
            {"type": t, "name": board_name(t)} for t in REGISTRY
        ],
        "posting_mode": gate.mode.value,
    }


# --- Admin: sticky-page placement -------------------------------------------


async def pages_status(db: Database, config: Config) -> dict:
    """Each sticky page's placement (where it lives / will live) for the dashboard."""
    from .pages import PAGES

    pages = []
    for page in PAGES.values():
        row = await db.get_sticky_page(page.name)
        pages.append(
            {
                "name": page.name,
                "title": page.title,
                "channel_id": row["channel_id"] if row else None,
                "posted_channel_id": row["posted_channel_id"] if row else None,
                "message_count": len(row["message_ids"]) if row else 0,
                "placed": row is not None,
            }
        )
    return {"pages": pages}


# --- Admin: feed hub status --------------------------------------------------


async def feed_health(db: Database, config: Config) -> dict:
    """Feed routing rules + operational health (muted sources, queue depths)."""
    from .runtime_settings import effective_feed_channel

    rules = await db.all_feed_rules()
    return {
        "rules": rules,
        "muted_sources": sorted(await db.muted_sources()),
        "default_channel_id": await effective_feed_channel(db, config),
        "pending_outbox": len(await db.pending_outbox()),
        "pending_digests": len(await db.pending_digests()),
    }


# --- Admin: recruitment funnel (leads + board config) -----------------------


async def recruiting(db: Database, config: Config) -> dict:
    """The recruitment funnel for the dashboard: the lead pipeline + the
    officer-configured board state, plus the choice lists the UI renders from."""
    from .boards.recruitment import ROLE_ORDER
    from .recruit import LEAD_STATES

    return {
        "leads": await db.leads(limit=200),
        "config": await db.recruitment_state(),
        "lead_states": list(LEAD_STATES),
        "roles": list(ROLE_ORDER),
        "scout_available": config.scout_credentials,
    }


# --- Admin: member manual-field editing -------------------------------------


async def members_admin(db: Database, config: Config, *, q: str | None = None) -> dict:
    """Roster rows decorated with their editable manual fields (and the resolved
    main character's name) for the member-admin page. ``q`` filters by name."""
    members = (
        await db.search_members(name=q) if q else await db.all_members(order_by="name")
    )
    rows = []
    for m in members:
        main_name = None
        if m.main_character_id:
            main = await db.get_member_by_id(m.main_character_id)
            main_name = main.character_name if main else None
        rows.append(
            {
                "id": m.id,
                "name": m.character_name,
                "realm": m.realm_slug,
                "region": m.region,
                "class": m.class_name,
                "role": m.role,
                "discord_id": m.discord_id,
                "notes": m.notes,
                "raid_team": m.raid_team,
                "officer_role": m.officer_role,
                "is_main": m.is_main,
                "main_character_id": m.main_character_id,
                "main_name": main_name,
            }
        )
    return {"members": rows, "query": q or ""}


# --- Deeper analytics (roll-ups that enrich Raid / Loot / Recruitment) -------

ATTENDANCE_TREND_DELTA = 10.0  # pct swing (recent vs overall) to call a trend


async def attendance_report(
    db: Database, config: Config, *, recent_window: int = 4, limit: int = 12
) -> dict:
    """Per-raider attendance + reliability trend, plus recent raid turnout.

    Reuses the same "present = not absent" rule as ``db.attendance_stats``. A
    raider whose recent window is >=10pts below their overall rate is flagged
    ``down`` (slipping); >=10pts above is ``up``.
    """
    raids = await db.recent_raids(limit=limit)
    characters = []
    for r in await db.attendance_by_character(recent_window=recent_window):
        pct = round(100.0 * r["present"] / r["scheduled"], 1) if r["scheduled"] else None
        rpct = (
            round(100.0 * r["recent_present"] / r["recent_scheduled"], 1)
            if r["recent_scheduled"]
            else None
        )
        trend = None
        if pct is not None and rpct is not None:
            delta = rpct - pct
            trend = (
                "down" if delta <= -ATTENDANCE_TREND_DELTA
                else "up" if delta >= ATTENDANCE_TREND_DELTA
                else "steady"
            )
        characters.append({**r, "pct": pct, "recent_pct": rpct, "trend": trend})
    # Worst attendance first — that is the GM's triage order.
    characters.sort(key=lambda c: (c["pct"] if c["pct"] is not None else 999))
    # Oldest -> newest turnout so the JS can sparkline it.
    turnout = [
        {"started_at": e["started_at"], "present": e["present"], "roster": e["roster"]}
        for e in reversed(raids)
    ]
    return {
        "raids": raids,            # newest first
        "characters": characters,  # worst first
        "turnout": turnout,
        "event_count": len(raids),
    }


async def loot_analytics(
    db: Database, config: Config, *, now: datetime | None = None
) -> dict:
    """Loot equity + drought + distribution, built on the loot fairness view."""
    now = _now(now)
    overview = await loot_overview(db, config, now=now)
    fairness = overview["fairness"]
    won = [f["items_won"] for f in fairness]
    avg = round(sum(won) / len(won), 2) if won else 0.0
    drought = sorted(
        (f for f in fairness if f.get("days_since_last_loot") is not None),
        key=lambda f: f["days_since_last_loot"],
        reverse=True,
    )[:15]
    # Players who have never received loot (and have attended at least one raid).
    never = [f["name"] for f in fairness if f["items_won"] == 0 and f["raids_attended"]]
    return {
        "total_awards": overview["total_awards"],
        "avg_per_player": avg,
        "drought": drought,            # longest since last drop first
        "never_won": never,
        "fairness": fairness,          # full table, most won first
        "breakdown": await db.loot_breakdown(),  # by slot / difficulty / response
    }


async def wishlist_analytics(db: Database, config: Config) -> dict:
    """Most-contested wishlist drops + per-raider BiS completion."""
    contention = [
        {
            **c,
            "avg_upgrade": (
                round(c["avg_upgrade"], 1) if c["avg_upgrade"] is not None else None
            ),
            "open": c["wishlisters"] - c["obtained"],
        }
        for c in await db.wishlist_contention(limit=40)
    ]
    coverage = []
    for r in await db.wishlist_coverage():
        pct = round(100.0 * r["obtained"] / r["total"], 1) if r["total"] else None
        coverage.append({**r, "pct": pct})
    coverage.sort(key=lambda c: (c["pct"] if c["pct"] is not None else -1.0))
    return {"contention": contention, "coverage": coverage}


async def recruitment_funnel(db: Database, config: Config) -> dict:
    """Lead pipeline as a funnel: stage counts, close rate, source/class quality."""
    from .recruit import LEAD_STATES

    funnel = await db.lead_funnel()
    by_state = funnel["by_state"]
    joined = by_state.get("joined", 0)
    passed = by_state.get("passed", 0)
    active = sum(by_state.get(s, 0) for s in ("new", "contacted", "trialing"))
    return {
        "stages": [{"state": s, "count": by_state.get(s, 0)} for s in LEAD_STATES],
        "total": sum(by_state.values()),
        "active": active,
        "joined": joined,
        "passed": passed,
        # Of the leads that reached a decision, how many we landed.
        "close_rate": (
            round(100.0 * joined / (joined + passed), 1) if (joined + passed) else None
        ),
        "by_source": funnel["by_source"],
        "by_class": funnel["by_class"],
    }
