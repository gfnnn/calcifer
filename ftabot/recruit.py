"""Recruitment scout scoring — pure, no I/O.

The scout is a roster gap-analysis + market scan: find our weak slots, then rank
discovered candidates of the *same class* that out-score the incumbent. This
module holds the maths (weak-slot detection, multi-season trajectory, candidate
scoring, poachability) so it is trivially testable; the service layer does the
network calls and feeds already-fetched data in.

Guiding principle (officer-stated): a multi-season record of *consistent,
improving* results beats one spiky season. A steady climb signals commitment and
low burn-out risk; a lone standout season with no history is a flag, not a win.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from .metrics import clamp01, percentile_rank
from .models import Member

# --- multi-season trajectory ------------------------------------------------


@dataclass
class Trajectory:
    """A read on a candidate's season-over-season performance curve."""

    seasons: int  # how many seasons of data we have
    latest: float | None  # most recent season's value
    slope: float  # average change per season (improving > 0)
    consistency: float  # 0..1, 1 = very steady season to season
    trend: str  # "improving" / "steady" / "declining" / "unknown"
    spike: bool  # a lone standout with little/no track record


def trajectory(values: list[float | None], *, improve_eps: float = 0.0) -> Trajectory:
    """Summarise a season-ordered (oldest → newest) list of scores.

    ``None`` entries (a season the player sat out / has no data) are dropped
    before analysis but still inform tenure via the surviving count.
    """
    series = [float(v) for v in values if v is not None]
    n = len(series)
    if n == 0:
        return Trajectory(0, None, 0.0, 0.0, "unknown", False)
    latest = series[-1]
    if n == 1:
        # One data point: no trend, and a high lone value is a spike risk.
        return Trajectory(1, latest, 0.0, 0.0, "unknown", True)

    # Average per-season change (simple end-to-end slope is robust + readable).
    slope = (series[-1] - series[0]) / (n - 1)

    # Consistency from the coefficient of variation: steadier → closer to 1.
    avg = mean(series)
    cov = (pstdev(series) / avg) if avg else 0.0
    consistency = clamp01(1 - cov)

    if slope > improve_eps:
        trend = "improving"
    elif slope < -improve_eps:
        trend = "declining"
    else:
        trend = "steady"

    # Spike: a recent value well above an otherwise thin/flat history.
    prior = series[:-1]
    spike = bool(prior) and latest > mean(prior) * 1.5 and n <= 2
    return Trajectory(n, round(latest, 1), round(slope, 2), round(consistency, 3), trend, spike)


def trajectory_phrase(label: str, values: list[float | None]) -> str | None:
    """A human line like "M+ 2.1k → 2.6k → 2.9k over 3 seasons, improving"."""
    series = [v for v in values if v is not None]
    if not series:
        return None
    traj = trajectory(values)
    rendered = " → ".join(_short(v) for v in series)
    return f"{label} {rendered} over {traj.seasons} season(s), {traj.trend}"


def _short(value: float) -> str:
    """Compact number: 2640 -> "2.6k", 78.5 -> "78"."""
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


# --- weak-slot detection ----------------------------------------------------


def player_strength(member: Member, roster: list[Member]) -> float:
    """A 0..1 standing for a raider *within the team* across parse / M+ / ilvl.

    Percentile-based so it self-calibrates to the guild's own level — a "weak"
    slot is weak relative to teammates, not an absolute bar.
    """
    parses = [m.best_parse_avg for m in roster]
    scores = [m.mythic_plus_score for m in roster]
    ilvls = [m.item_level for m in roster]
    parts = [
        (percentile_rank(parses, member.best_parse_avg), 0.5 if member.best_parse_avg is not None else 0.0),
        (percentile_rank(scores, member.mythic_plus_score), 0.3 if member.mythic_plus_score is not None else 0.0),
        (percentile_rank(ilvls, member.item_level), 0.2 if member.item_level is not None else 0.0),
    ]
    weight = sum(w for _, w in parts)
    if weight == 0:
        return 0.0
    return round(sum(v * w for v, w in parts) / weight, 3)


@dataclass
class WeakSlot:
    """A roster position worth shopping for an upgrade."""

    class_name: str
    spec: str | None
    role: str | None
    incumbent: str  # weakest current raider of this class
    incumbent_strength: float  # 0..1 within the team
    incumbent_parse: float | None = None
    incumbent_mplus: float | None = None


def weak_slots(
    roster: list[Member], *, threshold: float = 0.4, team: str | None = None
) -> list[WeakSlot]:
    """Find classes where our weakest raider lags the team (bottom ``threshold``).

    Like-for-like: we group by class and only flag the *weakest* holder of each
    class, so a class we're strong in is never surfaced. ``team`` optionally
    restricts the analysis to one raid team's members.
    """
    pool = [m for m in roster if m.class_name and (team is None or m.raid_team == team)]
    by_class: dict[str, list[Member]] = {}
    for m in pool:
        by_class.setdefault(m.class_name, []).append(m)  # type: ignore[arg-type]

    slots: list[WeakSlot] = []
    for class_name, members in by_class.items():
        weakest = min(members, key=lambda m: player_strength(m, pool))
        strength = player_strength(weakest, pool)
        if strength <= threshold:
            slots.append(
                WeakSlot(
                    class_name=class_name,
                    spec=weakest.active_spec,
                    role=weakest.role,
                    incumbent=weakest.character_name,
                    incumbent_strength=strength,
                    incumbent_parse=weakest.best_parse_avg,
                    incumbent_mplus=weakest.mythic_plus_score,
                )
            )
    slots.sort(key=lambda s: s.incumbent_strength)  # weakest first
    return slots


# --- candidate scoring ------------------------------------------------------


@dataclass
class Candidate:
    """A discovered player being weighed against one of our weak slots."""

    name: str
    realm: str
    region: str
    class_name: str
    spec: str | None = None
    role: str | None = None  # TANK / HEALER / DPS, when the API reports it
    item_level: float | None = None
    parse_seasons: list[float | None] = field(default_factory=list)  # oldest→newest
    mplus_seasons: list[float | None] = field(default_factory=list)  # oldest→newest
    guild_name: str | None = None
    guild_strength: float | None = None  # 0..1, how strong their current guild is
    lfg_active: bool = False  # has an active Looking For Guild profile on Raider.IO

    @property
    def current_parse(self) -> float | None:
        usable = [v for v in self.parse_seasons if v is not None]
        return usable[-1] if usable else None

    @property
    def current_mplus(self) -> float | None:
        usable = [v for v in self.mplus_seasons if v is not None]
        return usable[-1] if usable else None


@dataclass
class Requirements:
    """The guild's hard floors (from the live recruitment profile)."""

    min_item_level: float = 275.0
    roles_open: tuple[str, ...] = ("TANK", "HEALER", "DPS")
    region: str = "eu"


# How the recommendation score is blended. Track record is weighted heavily on
# purpose: trajectory + consistency together outweigh a single peak season.
SCORE_WEIGHTS = {
    "current": 0.30,
    "trajectory": 0.25,
    "consistency": 0.20,
    "upgrade": 0.25,
}


@dataclass
class ScoutMatch:
    candidate: Candidate
    score: float  # 0..100 overall recommendation
    upgrade_delta: float  # candidate strength - incumbent strength (>0 = upgrade)
    poachability: float  # 0..1, higher = more realistic to actually land
    parse_trajectory: Trajectory
    mplus_trajectory: Trajectory
    flags: list[str] = field(default_factory=list)


def meets_requirements(candidate: Candidate, req: Requirements) -> tuple[bool, list[str]]:
    """Gate on what the APIs can actually measure (ilvl / role / region).

    The role gate only applies when both the candidate's role and the open-role
    list are known; an unknown candidate role is not rejected (we can't measure
    it), matching the ilvl gate's skip-when-unknown behaviour.
    """
    reasons: list[str] = []
    if candidate.item_level is not None and candidate.item_level < req.min_item_level:
        reasons.append(f"ilvl {candidate.item_level:.0f} < {req.min_item_level:.0f}")
    if candidate.region.lower() != req.region.lower():
        reasons.append(f"region {candidate.region.upper()}")
    if req.roles_open and candidate.role:
        role = candidate.role.upper()
        if role not in {r.upper() for r in req.roles_open}:
            reasons.append(f"role {role} not open")
    return (not reasons, reasons)


def _candidate_strength(candidate: Candidate, *, parse_ref: float = 100.0, mplus_ref: float = 3500.0) -> float:
    """Absolute 0..1 strength so a candidate can be compared to an incumbent."""
    parts: list[tuple[float, float]] = []
    if candidate.current_parse is not None:
        parts.append((clamp01(candidate.current_parse / parse_ref), 0.6))
    if candidate.current_mplus is not None:
        parts.append((clamp01(candidate.current_mplus / mplus_ref), 0.4))
    weight = sum(w for _, w in parts)
    return sum(v * w for v, w in parts) / weight if weight else 0.0


def poachability(candidate: Candidate, *, our_strength: float | None) -> float:
    """0..1 — how realistic landing this player is.

    Intent (LFG profile) > unguilded > guild-strength gap. An actively LFG
    player is always the most realistic candidate regardless of their guild.
    """
    if candidate.lfg_active:
        return 1.0  # actively seeking — intent confirmed
    if candidate.guild_name is None:
        return 1.0  # unguilded — most realistic without explicit intent
    if candidate.guild_strength is None or our_strength is None:
        return 0.5  # unknown — neutral
    # Their guild weaker than us → very poachable; far stronger → unlikely.
    gap = our_strength - candidate.guild_strength
    return clamp01(0.5 + gap)


def score_candidate(
    candidate: Candidate,
    slot: WeakSlot,
    *,
    our_strength: float | None = None,
) -> ScoutMatch:
    """Blend current form, multi-season track record, and upgrade delta."""
    parse_traj = trajectory(candidate.parse_seasons)
    mplus_traj = trajectory(candidate.mplus_seasons)

    strength = _candidate_strength(candidate)
    current = strength

    # Trajectory component: reward an improving slope (normalise gently).
    slopes = [t.slope for t in (parse_traj, mplus_traj) if t.seasons >= 2]
    slope_signal = clamp01(0.5 + (mean(slopes) / 20 if slopes else 0.0))

    # Consistency component across whichever curves we have.
    cons = [t.consistency for t in (parse_traj, mplus_traj) if t.seasons >= 2]
    consistency = mean(cons) if cons else 0.4  # thin history → modest, not zero

    upgrade_delta = round(strength - slot.incumbent_strength, 3)
    upgrade_signal = clamp01(upgrade_delta + 0.5)  # delta of 0 → neutral 0.5

    flags: list[str] = []
    if candidate.lfg_active:
        flags.append("🔍 actively LFG on Raider.IO")
    if parse_traj.spike or mplus_traj.spike:
        flags.append("⚠️ single-season spike — thin track record")
    if parse_traj.trend == "declining" or mplus_traj.trend == "declining":
        flags.append("🟡 recent decline")
    if max(parse_traj.seasons, mplus_traj.seasons) >= 3 and consistency >= 0.8:
        flags.append("🟢 consistent multi-season record")
    if candidate.guild_name is None:
        flags.append("🔓 unguilded")

    score = 100 * (
        SCORE_WEIGHTS["current"] * current
        + SCORE_WEIGHTS["trajectory"] * slope_signal
        + SCORE_WEIGHTS["consistency"] * consistency
        + SCORE_WEIGHTS["upgrade"] * upgrade_signal
    )
    return ScoutMatch(
        candidate=candidate,
        score=round(score, 1),
        upgrade_delta=upgrade_delta,
        poachability=round(poachability(candidate, our_strength=our_strength), 3),
        parse_trajectory=parse_traj,
        mplus_trajectory=mplus_traj,
        flags=flags,
    )


def rank_candidates(
    candidates: list[Candidate],
    slot: WeakSlot,
    req: Requirements,
    *,
    our_strength: float | None = None,
    upgrades_only: bool = True,
) -> list[ScoutMatch]:
    """Score, gate, and rank candidates for one weak slot (best first).

    Only players who clear the hard requirements *and* out-score the incumbent
    are returned (when ``upgrades_only``), so the shortlist is actionable.
    """
    matches: list[ScoutMatch] = []
    for c in candidates:
        if c.class_name.lower() != slot.class_name.lower():
            continue  # like-for-like only
        ok, _reasons = meets_requirements(c, req)
        if not ok:
            continue
        match = score_candidate(c, slot, our_strength=our_strength)
        if upgrades_only and match.upgrade_delta <= 0:
            continue
        matches.append(match)
    # Rank by recommendation score, then realistic-to-land, then raw upgrade.
    matches.sort(key=lambda m: (m.score, m.poachability, m.upgrade_delta), reverse=True)
    return matches
