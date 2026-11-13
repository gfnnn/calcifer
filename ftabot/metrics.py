"""Guild + raider metrics shared by the web and Discord layers.

Everything here is pure (no I/O) so it is easy to test and reuse. Functions take
already-fetched data (``Member`` lists, attendance percentages, loot counts) and
return scores/flags. The web and Discord views do the database calls and then
call into this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import Member

# Default raid role mix used to judge roster balance (proportions, must sum ~1).
DEFAULT_ROLE_TARGETS: dict[str, float] = {"TANK": 0.10, "HEALER": 0.20, "DPS": 0.70}


# --- small helpers ----------------------------------------------------------


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def percentile_rank(values: list[float], value: float | None) -> float:
    """Fraction of ``values`` that ``value`` is >= (0..1). None -> 0.0."""
    if value is None:
        return 0.0
    usable = [v for v in values if v is not None]
    if not usable:
        return 0.0
    at_or_below = sum(1 for v in usable if v <= value)
    return at_or_below / len(usable)


# --- activity ---------------------------------------------------------------


def is_active(
    member: Member, *, window_days: int = 14, now: datetime | None = None
) -> bool:
    """Whether a member has been seen playing within ``window_days``.

    Best signal first: a real ``last_login`` (WoW Audit), else when Raider.IO
    last crawled the character (it re-crawls characters that are being played,
    so this is the keyless stand-in). ``last_synced`` is only the last resort
    for a row with neither: it says when *we* looked, not when they played, so
    preferring it would count the whole roster as active after every sync.
    """
    now = now or datetime.now(timezone.utc)
    seen = (
        _parse_dt(member.last_login)
        or _parse_dt(member.last_crawled_at)
        or _parse_dt(member.last_synced)
    )
    if seen is None:
        return False
    return (now - seen) <= timedelta(days=window_days)


def activity_rate(
    members: list[Member], *, window_days: int = 14, now: datetime | None = None
) -> float | None:
    if not members:
        return None
    active = sum(1 for m in members if is_active(m, window_days=window_days, now=now))
    return active / len(members)


# --- preparedness / vault ---------------------------------------------------


def prep_ready(member: Member) -> bool | None:
    """True if no missing enchants/gems, None if we have no prep data."""
    if member.missing_enchants is None and member.missing_gems is None:
        return None
    return (member.missing_enchants or 0) == 0 and (member.missing_gems or 0) == 0


def prep_rate(members: list[Member]) -> float | None:
    known = [prep_ready(m) for m in members]
    known = [v for v in known if v is not None]
    if not known:
        return None
    return sum(1 for v in known if v) / len(known)


def vault_engagement_rate(members: list[Member]) -> float | None:
    known = [m.vault_slots_filled for m in members if m.vault_slots_filled is not None]
    if not known:
        return None
    return sum(1 for v in known if v >= 1) / len(known)


# --- roster composition / balance ------------------------------------------


def role_counts(members: list[Member]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in members:
        if m.role:
            counts[m.role] = counts.get(m.role, 0) + 1
    return counts


def class_counts(members: list[Member]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in members:
        if m.class_name:
            counts[m.class_name] = counts.get(m.class_name, 0) + 1
    return counts


def role_balance_score(
    members: list[Member], targets: dict[str, float] | None = None
) -> float | None:
    """1.0 = role proportions match the target mix; 0.0 = maximally off."""
    targets = targets or DEFAULT_ROLE_TARGETS
    counts = role_counts(members)
    total = sum(counts.get(r, 0) for r in targets)
    if total == 0:
        return None
    deviation = sum(abs(counts.get(r, 0) / total - targets[r]) for r in targets)
    # Max possible deviation is 2 (entirely in the wrong bucket).
    return clamp01(1 - deviation / 2)


# --- progression ------------------------------------------------------------

_DIFF_ORDER = ("M", "H", "N")
_PROG_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*([NHM])", re.IGNORECASE)


def parse_progression(summary: str | None) -> dict[str, tuple[int, int]]:
    """Parse "Name: 8/8 N, 6/8 H, 2/8 M" into {'N': (8,8), 'H': (6,8), ...}."""
    out: dict[str, tuple[int, int]] = {}
    if not summary:
        return out
    for killed, total, diff in _PROG_RE.findall(summary):
        out[diff.upper()] = (int(killed), int(total))
    return out


def progression_fraction(summary: str | None) -> float | None:
    """Score 0..1 from the hardest difficulty with any kills (M > H > N).

    Mythic progress always outranks Heroic/Normal: a guild 2/8 M is further
    along than one 8/8 H, so we take the hardest tier that has any kills.
    """
    prog = parse_progression(summary)
    if not prog:
        return None
    weights = {"M": 1.0, "H": 0.6, "N": 0.3}
    for diff in _DIFF_ORDER:  # hardest first
        killed, total = prog.get(diff, (0, 0))
        if killed > 0 and total > 0:
            return weights[diff] * (killed / total)
    return 0.0


def guild_progression(members: list[Member]) -> tuple[str | None, float | None]:
    """Pick the furthest-progressed member's summary as the guild's progress."""
    best_summary: str | None = None
    best_score: float | None = None
    for m in members:
        score = progression_fraction(m.raid_progression)
        if score is not None and (best_score is None or score > best_score):
            best_score, best_summary = score, m.raid_progression
    return best_summary, best_score


# --- raider readiness -------------------------------------------------------

_READINESS_WEIGHTS = {
    "performance": 0.35,
    "gear": 0.20,
    "prep": 0.15,
    "engagement": 0.20,
    "wishlist": 0.10,
}


@dataclass
class ReadinessResult:
    score: float  # 0..100
    components: dict[str, float] = field(default_factory=dict)  # each 0..1
    flags: list[str] = field(default_factory=list)


def compute_readiness(
    member: Member,
    *,
    team_item_levels: list[float],
    attendance_pct: float | None = None,
    now: datetime | None = None,
    window_days: int = 14,
) -> ReadinessResult:
    """Blend performance indicators into a 0..100 readiness score + flags."""
    flags: list[str] = []

    # Performance: best parse average (0..100) -> 0..1.
    if member.best_parse_avg is not None:
        performance = clamp01(member.best_parse_avg / 100)
        if member.best_parse_avg < 50:
            flags.append("🟡 low parse")
    else:
        performance = 0.5  # neutral when unknown
        flags.append("⚪ no parse data")

    # Gear: equipped item-level percentile within the team.
    gear = percentile_rank(team_item_levels, member.item_level)

    # Prep: enchants + gems complete.
    ready = prep_ready(member)
    if ready is None:
        prep = 0.5
    elif ready:
        prep = 1.0
    else:
        prep = 0.0
        if member.missing_enchants:
            flags.append("🔴 missing enchants")
        if member.missing_gems:
            flags.append("🔴 missing gems")

    # Engagement: attendance + vault, averaged over whatever we know.
    engagement_signals: list[float] = []
    if attendance_pct is not None:
        engagement_signals.append(clamp01(attendance_pct / 100))
        if attendance_pct < 60:
            flags.append("🟡 low attendance")
    if member.vault_slots_filled is not None:
        engagement_signals.append(clamp01(member.vault_slots_filled / 9))
    engagement = sum(engagement_signals) / len(engagement_signals) if engagement_signals else 0.5

    # Wishlist: credit by how filled it is (a percentage), not mere presence.
    has_wishlist = member.wishlist_pct is not None
    wishlist = clamp01(member.wishlist_pct / 100) if has_wishlist else 0.0
    if not has_wishlist:
        flags.append("⚪ no wishlist")

    if not is_active(member, window_days=window_days, now=now):
        flags.append("🔴 inactive")

    components = {
        "performance": performance,
        "gear": gear,
        "prep": prep,
        "engagement": engagement,
        "wishlist": wishlist,
    }
    score = 100 * sum(_READINESS_WEIGHTS[k] * v for k, v in components.items())
    return ReadinessResult(score=round(score, 1), components=components, flags=flags)


# --- guild health -----------------------------------------------------------

_HEALTH_WEIGHTS = {
    "activity": 0.30,
    "readiness": 0.25,
    "progression": 0.20,
    "balance": 0.15,
    "retention": 0.10,
}


@dataclass
class HealthResult:
    score: float | None  # 0..100, or None if no data at all
    components: dict[str, float] = field(default_factory=dict)  # available, 0..1


def guild_health(
    members: list[Member],
    *,
    window_days: int = 14,
    attendance_rate: float | None = None,
    prev_member_count: int | None = None,
    targets: dict[str, float] | None = None,
    now: datetime | None = None,
) -> HealthResult:
    """Composite 0..100 health score; weights re-normalise over known parts."""
    components: dict[str, float] = {}

    act = activity_rate(members, window_days=window_days, now=now)
    if act is not None:
        components["activity"] = act

    readiness_parts = [
        p
        for p in (prep_rate(members), vault_engagement_rate(members), attendance_rate)
        if p is not None
    ]
    if readiness_parts:
        components["readiness"] = sum(readiness_parts) / len(readiness_parts)

    _, prog = guild_progression(members)
    if prog is not None:
        components["progression"] = prog

    bal = role_balance_score(members, targets)
    if bal is not None:
        components["balance"] = bal

    if prev_member_count:
        change = (len(members) - prev_member_count) / prev_member_count
        components["retention"] = clamp01(0.5 + change)  # flat = 0.5, growth -> up

    if not components:
        return HealthResult(score=None)

    weight_total = sum(_HEALTH_WEIGHTS[k] for k in components)
    score = 100 * sum(_HEALTH_WEIGHTS[k] * v for k, v in components.items()) / weight_total
    return HealthResult(score=round(score, 1), components=components)


# --- loot -------------------------------------------------------------------


@dataclass
class LootCandidate:
    character_name: str
    is_bis: bool = False
    upgrade_pct: float | None = None  # Droptimizer DPS gain
    attendance_pct: float | None = None
    days_since_last_loot: float | None = None
    recent_wins: int = 0


def loot_priority_score(
    candidate: LootCandidate, *, max_upgrade_pct: float = 10.0
) -> float:
    """Advisory loot-council priority (higher = stronger case). 0..~1.2."""
    is_bis = 1.0 if candidate.is_bis else 0.0
    upgrade = clamp01((candidate.upgrade_pct or 0) / max_upgrade_pct)
    attendance = clamp01((candidate.attendance_pct or 0) / 100)
    # More days since last loot -> higher recency factor (cap at ~30 days).
    recency = clamp01((candidate.days_since_last_loot or 0) / 30)
    recent_penalty = clamp01(candidate.recent_wins / 5)
    return (
        0.40 * is_bis
        + 0.30 * upgrade
        + 0.20 * attendance
        + 0.10 * recency
        - 0.15 * recent_penalty
    )


def rank_loot_priority(
    candidates: list[LootCandidate], *, max_upgrade_pct: float = 10.0
) -> list[tuple[LootCandidate, float]]:
    """Return candidates sorted by descending advisory priority."""
    scored = [
        (c, round(loot_priority_score(c, max_upgrade_pct=max_upgrade_pct), 3))
        for c in candidates
    ]
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return scored
