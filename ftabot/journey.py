"""The onboarding journey: People + an append-only Events log.

This is the foundation other features read from. A row in ``people`` is one
Discord account in the server (present or recently left); ``journey_events`` is
the append-only record of what happened to them and who did it. Views (the
welcome card, ``/journey``, the daily nudge, the Front door board) are derived
from those two tables and never keep state of their own beyond a message id.

Layering follows the rest of the repo:

* **pure** helpers with no I/O: :func:`attribute_invite` (which invite a join
  used) and :func:`match_roster` (a Discord name to a roster character);
* **DB-backed** recorders (:func:`record_join`, :func:`record_leave`, ...) that
  take a ``Database`` and plain values, so they are tested offline without a
  Discord object in sight.

The Discord listeners that call these live in ``cogs/journey.py``.

Privacy: only what the journey needs is stored (id, display name, join/leave
times, the invite used, and the events). Nothing here logs a member's name or
id at INFO; counts only.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .models import now_iso

if TYPE_CHECKING:
    from .db import Database
    from .models import Member

log = logging.getLogger("ftabot.journey")

# -- event vocabulary ---------------------------------------------------------

JOINED = "joined"
LEFT = "left"
REJOINED = "rejoined"
CAME_VIA = "came_via"
ROLES_CHANGED = "roles_changed"
GREETED = "greeted"
PLACED = "placed"
CHECKED_IN = "checked_in"
CHARACTER_LINKED = "character_linked"
LEAD_JOINED = "lead_joined"  # a scouted recruitment lead turned up as a member

EVENT_TYPES: tuple[str, ...] = (
    JOINED, LEFT, REJOINED, CAME_VIA, ROLES_CHANGED,
    GREETED, PLACED, CHECKED_IN, CHARACTER_LINKED, LEAD_JOINED,
)

# ``people.came_via`` holds an invite code or one of these.
VIA_UNKNOWN = "unknown"
VIA_VANITY = "vanity"
VIA_BASELINE = "baseline"

# The settings key that records the one-off baseline backfill.
BASELINE_SETTING = "journey_baseline_at"


def iso(moment: datetime | None) -> str:
    """A datetime as the UTC ISO string every ``*_at`` column uses."""
    if moment is None:
        return now_iso()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def character_key(member: "Member") -> str:
    """The stable string key of a roster character: ``name|realm|region``."""
    return "|".join(member.key)


# -- invite attribution (pure) ------------------------------------------------


def attribute_invite(
    before: Mapping[str, int] | None, after: Mapping[str, int] | None
) -> str:
    """Which invite a join used, from the use counts before and after it.

    Exactly one code whose count went up is the answer. None (a cache we never
    managed to fill, a one-use invite Discord already deleted) or more than one
    (two people joined at once) cannot be attributed honestly, so both read
    ``unknown`` rather than a guess.
    """
    if before is None or after is None:
        return VIA_UNKNOWN
    changed = [code for code, uses in after.items() if uses > before.get(code, 0)]
    return changed[0] if len(changed) == 1 else VIA_UNKNOWN


# -- roster match (pure) ------------------------------------------------------

EXACT = "exact"  # the whole display name is a character name
LIKELY = "likely"  # one part of a "Character (Name)" style nickname is
POSSIBLE = "possible"  # only a word inside the name is

_SEPARATORS = ("(", ")", "[", "]", "/", "|", "~", ",", " - ", " aka ", " a.k.a. ")
_MIN_TOKEN = 3


@dataclass(frozen=True)
class RosterMatch:
    """A roster character a Discord name points at, and how sure we are."""

    member: "Member"
    rank_name: str | None
    confidence: str


def fold(text: str | None) -> str:
    """Case- and accent-insensitive form of a name ("Çalcifer" == "calcifer")."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().strip()


def _parts(name: str) -> list[str]:
    """The pieces of a structured nickname: "Brann (Dave)" -> brann, dave."""
    text = f" {fold(name)} "
    for sep in _SEPARATORS:
        text = text.replace(sep, "\n")
    return [p.strip() for p in text.split("\n") if p.strip()]


def _candidates(names: Iterable[str | None]) -> list[tuple[str, str]]:
    """Every (folded text, confidence) to try, strongest first, de-duplicated."""
    seen: dict[str, str] = {}
    cleaned = [n for n in names if n and n.strip()]
    tiers: list[tuple[str, str]] = [(fold(n), EXACT) for n in cleaned]
    for n in cleaned:
        tiers += [(p, LIKELY) for p in _parts(n)]
    for n in cleaned:
        for part in _parts(n):
            tiers += [(t, POSSIBLE) for t in part.split() if len(t) >= _MIN_TOKEN]
    for text, confidence in tiers:
        if text and text not in seen:
            seen[text] = confidence
    return list(seen.items())


def _preference(member: "Member") -> tuple:
    """Order characters sharing a name: mains first, then the senior rank."""
    rank = member.guild_rank if member.guild_rank is not None else 99
    return (0 if member.is_main else 1, rank, member.realm_slug)


def match_roster(
    names: Iterable[str | None],
    roster: Sequence["Member"],
    rank_names: Mapping[tuple[str, str], str] | None = None,
) -> RosterMatch | None:
    """Match a member's nickname / display name to a roster character.

    ``names`` are the member's names in preference order (server nickname, then
    global display name, then username). Matching ignores case and accents and
    understands "Character (Name)" and "Character / Name" nicknames. Returns the
    best character with its in-game rank name (from the GRM identity table via
    ``rank_names``, keyed ``(lower(name), realm)``; else "Rank N") and a
    confidence, or ``None``. Pure: no I/O, no side effects.
    """
    by_name: dict[str, list["Member"]] = {}
    for member in roster:
        by_name.setdefault(fold(member.character_name), []).append(member)
    for text, confidence in _candidates(names):
        found = by_name.get(text)
        if not found:
            continue
        best = min(found, key=_preference)
        rank = (rank_names or {}).get(
            (best.character_name.lower(), best.realm_slug.lower())
        )
        if rank is None and best.guild_rank is not None:
            rank = f"Rank {best.guild_rank}"
        return RosterMatch(member=best, rank_name=rank, confidence=confidence)
    return None


async def roster_match_for(db: "Database", names: Iterable[str | None]) -> RosterMatch | None:
    """:func:`match_roster` against the stored roster and GRM rank names."""
    roster = await db.all_members()
    if not roster:
        return None
    return match_roster(names, roster, await db.identity_rank_names())


# -- recorders (DB-backed) ----------------------------------------------------


@dataclass(frozen=True)
class JoinResult:
    """What a recorded join turned out to be."""

    rejoin: bool
    previous_joined_at: str | None = None


async def record_join(
    db: "Database",
    *,
    discord_id: str,
    display_name: str | None,
    joined_at: str | None = None,
    came_via: str = VIA_UNKNOWN,
) -> JoinResult:
    """Record a member arriving: the person row plus ``joined``/``rejoined``
    and ``came_via`` events. Someone we already know is a rejoin, and keeps the
    date of their previous join in the event."""
    at = joined_at or now_iso()
    known = await db.get_person(discord_id)
    previous = known["joined_at"] if known else None
    await db.upsert_person(
        discord_id, display_name=display_name, joined_at=at, came_via=came_via
    )
    if known:
        await db.add_journey_event(
            discord_id, REJOINED, at=at, data={"previous_joined_at": previous}
        )
    else:
        await db.add_journey_event(discord_id, JOINED, at=at)
    await db.add_journey_event(discord_id, CAME_VIA, at=at, data={"code": came_via})
    return JoinResult(rejoin=bool(known), previous_joined_at=previous)


async def record_leave(db: "Database", discord_id: str, at: str | None = None) -> bool:
    """Record a member leaving. False if we never knew them or they are already
    marked as gone (so a repeated event logs nothing twice)."""
    person = await db.get_person(discord_id)
    if person is None or person["left_at"]:
        return False
    at = at or now_iso()
    await db.set_person_left(discord_id, at)
    await db.add_journey_event(discord_id, LEFT, at=at)
    return True


async def record_roles_changed(
    db: "Database",
    discord_id: str,
    *,
    added: Sequence[str],
    removed: Sequence[str],
) -> bool:
    """Log a role diff (names only). Nothing is written for an empty diff or a
    person outside the journey."""
    if not (added or removed) or await db.get_person(discord_id) is None:
        return False
    await db.add_journey_event(
        discord_id, ROLES_CHANGED,
        data={"added": sorted(added), "removed": sorted(removed)},
    )
    return True


async def link_character(
    db: "Database",
    discord_id: str,
    member: "Member",
    *,
    actor_id: str | None = None,
) -> bool:
    """Note an officer's character link on the person and log it. The link
    itself stays in ``members.discord_id``; this is the journey's view of it."""
    person = await db.get_person(discord_id)
    if person is None:
        return False
    key = character_key(member)
    if person["linked_main"] is None or member.is_main:
        await db.set_person_linked_main(discord_id, key)
    await db.add_journey_event(
        discord_id, CHARACTER_LINKED, actor_id=actor_id, data={"character": key}
    )
    # A link is the moment a scouted lead can be recognised as having joined.
    await reconcile_scout_leads(db)
    return True


async def unlink_character(db: "Database", member: "Member") -> None:
    """Forget a character link the officer removed (no event: nothing happened
    to the person, a record was corrected)."""
    await db.clear_linked_main(character_key(member))


# -- baseline + reconcile -----------------------------------------------------


@dataclass(frozen=True)
class Present:
    """One member currently in the server, as the listeners see them."""

    discord_id: str
    display_name: str | None
    joined_at: str | None


@dataclass(frozen=True)
class Reconciled:
    """What a startup pass did (counts only: safe to log)."""

    baselined: int = 0
    missed_joins: int = 0
    missed_leaves: int = 0


async def _existing_links(db: "Database") -> dict[str, str]:
    """discord_id -> character key from the officer links already on the roster."""
    best: dict[str, "Member"] = {}
    for member in await db.all_members():
        if not member.discord_id:
            continue
        current = best.get(member.discord_id)
        if current is None or _preference(member) < _preference(current):
            best[member.discord_id] = member
    return {did: character_key(m) for did, m in best.items()}


async def reconcile(db: "Database", present: Sequence[Present]) -> Reconciled:
    """Bring ``people`` in line with who is in the server right now.

    **First run:** everyone present is inserted with ``came_via = baseline`` and
    *no events*, so the first week of nudges and cards is not flooded with the
    existing membership. The baseline time is recorded and never repeats.

    **Every later run** (a restart, a gateway reconnect) repairs what was missed
    while the bot was away: a present member we have no row for joined during
    the gap (logged as a join via ``unknown``), and a person we think is here
    but is not left during it. Idempotent: a second pass changes nothing.
    """
    if not await db.journey_baseline_at():
        count = await db.insert_baseline_people(
            [(p.discord_id, p.display_name, p.joined_at) for p in present]
        )
        links = await _existing_links(db)
        for person in present:
            if person.discord_id in links:
                await db.set_person_linked_main(person.discord_id, links[person.discord_id])
        await db.set_journey_baseline_at(now_iso())
        return Reconciled(baselined=count)

    present_ids = {p.discord_id for p in present}
    known = {p["discord_id"]: p for p in await db.people()}
    joins = leaves = 0
    for person in present:
        row = known.get(person.discord_id)
        if row is None or row["left_at"]:
            await record_join(
                db, discord_id=person.discord_id, display_name=person.display_name,
                joined_at=person.joined_at, came_via=VIA_UNKNOWN,
            )
            joins += 1
    for discord_id, row in known.items():
        if not row["left_at"] and discord_id not in present_ids:
            leaves += await record_leave(db, discord_id)
    return Reconciled(missed_joins=joins, missed_leaves=leaves)


# -- retention ----------------------------------------------------------------


async def prune(
    db: "Database", retention_days: int, *, now: datetime | None = None
) -> int:
    """Forget people who left more than ``retention_days`` ago: their events and
    their person row. Returns how many people were forgotten. 0 days = keep."""
    if retention_days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    return await db.prune_journey(iso(cutoff))


# -- the view-model every journey surface renders from ------------------------


@dataclass(frozen=True)
class Status:
    """Where one person is on the journey, derived from the log (pure data).

    Only events from their current stay count: a rejoin starts a clean slate,
    and carries the date of the previous join.
    """

    discord_id: str
    display_name: str | None
    joined_at: str | None
    left_at: str | None
    came_via: str
    rejoin: bool = False
    previous_joined_at: str | None = None
    greeted_by: str | None = None
    greeted_at: str | None = None
    placed_role: str | None = None
    placed_by: str | None = None
    placed_at: str | None = None
    checked_in_by: str | None = None
    checked_in_at: str | None = None
    picked: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.left_at is None

    @property
    def greeted(self) -> bool:
        return self.greeted_at is not None

    @property
    def placed(self) -> bool:
        return self.placed_role is not None

    @property
    def handled(self) -> bool:
        """Somebody on the welcome team has done something for them."""
        return self.greeted or self.placed


def _in(name: str, names: Iterable[str]) -> bool:
    return name.casefold() in {n.casefold() for n in names}


def status_from(
    person: Mapping,
    events: Sequence[Mapping],
    *,
    rank_roles: Sequence[str] = (),
    interest_roles: Sequence[str] = (),
) -> Status:
    """Fold a person's events into their :class:`Status`. Pure.

    A rank role given by hand in Discord counts as placed too (it arrives as a
    ``roles_changed`` event with nobody to credit), so the nudges never chase
    someone a Flamekeeper already sorted out the old way.
    """
    start = 0
    for index, event in enumerate(events):
        if event["type"] in (JOINED, REJOINED):
            start = index
    current = list(events[start:])

    fields: dict = {}
    picked: list[str] = []
    for event in current:
        kind, data = event["type"], event.get("data") or {}
        if kind == REJOINED:
            fields["rejoin"] = True
            fields["previous_joined_at"] = data.get("previous_joined_at")
        elif kind == GREETED and "greeted_at" not in fields:
            fields["greeted_by"], fields["greeted_at"] = event["actor_id"], event["at"]
        elif kind == CHECKED_IN and "checked_in_at" not in fields:
            fields["checked_in_by"] = event["actor_id"]
            fields["checked_in_at"] = event["at"]
        elif kind == PLACED:
            fields["placed_role"] = data.get("role")
            fields["placed_by"], fields["placed_at"] = event["actor_id"], event["at"]
        elif kind == ROLES_CHANGED:
            added, removed = data.get("added") or [], data.get("removed") or []
            for role in added:
                if _in(role, interest_roles) and role not in picked:
                    picked.append(role)
            picked = [r for r in picked if r not in removed]
            ranks = [r for r in added if _in(r, rank_roles)]
            held = fields.get("placed_role")
            if ranks and not (held and _in(held, ranks)):
                fields["placed_role"], fields["placed_by"] = ranks[-1], None
                fields["placed_at"] = event["at"]
            elif held and not ranks and _in(held, removed):
                fields["placed_role"] = fields["placed_by"] = fields["placed_at"] = None
    return Status(
        discord_id=person["discord_id"],
        display_name=person["display_name"],
        joined_at=person["joined_at"],
        left_at=person["left_at"],
        came_via=person["came_via"],
        picked=tuple(picked),
        **fields,
    )


async def status_for(db: "Database", config, discord_id: str) -> Status | None:
    """A person's current :class:`Status`, or ``None`` if we do not know them."""
    person = await db.get_person(discord_id)
    if person is None:
        return None
    return status_from(
        person,
        await db.journey_events(discord_id=discord_id),
        rank_roles=config.journey_rank_roles,
        interest_roles=config.journey_interest_roles,
    )


_VIA_WORDS = {
    VIA_UNKNOWN: "unknown",
    VIA_VANITY: "the vanity link",
    VIA_BASELINE: "here before the journey began",
}


def invite_label(came_via: str | None, labels: Mapping[str, str]) -> str:
    """How to show where someone came from: the friendly label for an invite
    code when one is configured, plain words for the special values, and the
    bare code otherwise (so an unlabelled invite is still traceable)."""
    if not came_via:
        return _VIA_WORDS[VIA_UNKNOWN]
    if came_via in labels:
        return labels[came_via]
    return _VIA_WORDS.get(came_via, f"`{came_via}`")


# -- staff actions (each one appends an event, once) ---------------------------


async def record_greeted(
    db: "Database", config, discord_id: str, actor_id: str
) -> Status | None:
    """Log that ``actor_id`` said hi. Idempotent: the first greeting stands and
    a second click logs nothing. Returns the status after, or ``None`` for a
    stranger."""
    status = await status_for(db, config, discord_id)
    if status is None:
        return None
    if not status.greeted:
        await db.add_journey_event(discord_id, GREETED, actor_id=actor_id)
        status = await status_for(db, config, discord_id)
    return status


async def record_checked_in(
    db: "Database", config, discord_id: str, actor_id: str
) -> Status | None:
    """Log the day-7 check-in, once per stay."""
    status = await status_for(db, config, discord_id)
    if status is None:
        return None
    if status.checked_in_at is None:
        await db.add_journey_event(discord_id, CHECKED_IN, actor_id=actor_id)
        status = await status_for(db, config, discord_id)
    return status


async def record_placed(
    db: "Database", config, discord_id: str, role: str, actor_id: str
) -> Status | None:
    """Log that ``actor_id`` placed the person as ``role``. Idempotent per
    role: clicking the same role twice logs once; a different role is a new
    placement (a correction)."""
    status = await status_for(db, config, discord_id)
    if status is None:
        return None
    same = status.placed_role and status.placed_role.casefold() == role.casefold()
    if not (same and status.placed_by):
        await db.add_journey_event(
            discord_id, PLACED, actor_id=actor_id, data={"role": role}
        )
        status = await status_for(db, config, discord_id)
    return status


class RoleNotAllowed(Exception):
    """Calcifer was asked to touch a role that is not on the allow-list."""


def check_role_allowed(role: str, config) -> None:
    """Raise :class:`RoleNotAllowed` unless ``role`` is on the explicit
    allow-list (plus the configured interest roles). This is the code-level
    guard: it refuses even where Discord's role order would permit the change."""
    allowed = (*config.journey_role_allowlist, *config.journey_interest_roles)
    if not _in(role, allowed):
        raise RoleNotAllowed(role)


# -- the unplaced sweep ---------------------------------------------------------


def unplaced(members: Iterable, rank_roles: Sequence[str]) -> list:
    """The members holding none of the rank roles, longest-waiting first. Pure.

    ``members`` are Discord members (anything with ``bot``, ``roles`` and
    ``joined_at``); bots are skipped. Someone with no join date sorts last.
    """
    ranks = {r.casefold() for r in rank_roles}
    waiting = [
        m for m in members
        if not m.bot and not any(r.name.casefold() in ranks for r in m.roles)
    ]
    far_future = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(waiting, key=lambda m: (m.joined_at or far_future, m.id))


# -- the daily nudge --------------------------------------------------------------

# Hard-coded on purpose (thresholds and schedules are change requests): the
# nudge runs on UK time because that is when the Flamekeepers are about.
NUDGE_TZ = ZoneInfo("Europe/London")
NUDGE_DEFAULT_TIME = time(18, 0)
NUDGE_LAST_SETTING = "journey_nudge_last"
GREET_AFTER = timedelta(hours=24)
PLACE_AFTER = timedelta(hours=48)
CHECK_IN_AFTER = timedelta(days=7)
# Past this, somebody is no longer a daily reminder: they are backlog, and
# `/journey unplaced` is the tool for backlog.
NUDGE_HORIZON = timedelta(days=14)


def parse_nudge_time(text: str | None) -> time:
    """``HH:MM`` as a time of day; anything unreadable is the 18:00 default."""
    try:
        hours, minutes = (text or "").strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        return NUDGE_DEFAULT_TIME


def nudge_due(now: datetime, at: time, last: str | None) -> date | None:
    """The UK date whose nudge should go out now, or ``None``.

    Due once the UK wall clock has reached ``at`` today and today's has not
    gone yet (``last`` is the ISO date of the previous one). Working on the
    wall clock is what makes it DST-safe: 18:00 is 18:00 in January and July.
    """
    local = now.astimezone(NUDGE_TZ)
    if local.time() < at or last == local.date().isoformat():
        return None
    return local.date()


@dataclass(frozen=True)
class NudgeLists:
    """Who needs a nudge today, each list longest-waiting first."""

    not_greeted: tuple[Status, ...] = ()
    not_placed: tuple[Status, ...] = ()
    check_in: tuple[Status, ...] = ()

    @property
    def quiet(self) -> bool:
        return not (self.not_greeted or self.not_placed or self.check_in)


def nudge_lists(statuses: Iterable[Status], now: datetime) -> NudgeLists:
    """Sort people into the three nudge lists. Pure.

    Everything is read from the log, so a person leaves a list the moment the
    thing is done and stays on it, day after day, only while it is still
    outstanding. People who were here before the journey (``baseline``) and
    people who have left are never nudged about.
    """
    greet, place, check = [], [], []
    for status in statuses:
        if not status.present or status.came_via == VIA_BASELINE or not status.joined_at:
            continue
        age = now - datetime.fromisoformat(status.joined_at)
        if age >= NUDGE_HORIZON:
            continue
        if not status.greeted and age >= GREET_AFTER:
            greet.append(status)
        if not status.placed and age >= PLACE_AFTER:
            place.append(status)
        if status.checked_in_at is None and age >= CHECK_IN_AFTER:
            check.append(status)

    def oldest(items: list[Status]) -> tuple[Status, ...]:
        return tuple(sorted(items, key=lambda s: (s.joined_at, s.discord_id)))

    return NudgeLists(oldest(greet), oldest(place), oldest(check))


async def all_statuses(db: "Database", config) -> list[Status]:
    """The :class:`Status` of everyone currently in the server (two queries)."""
    by_person: dict[str, list] = {}
    for event in await db.journey_events():
        by_person.setdefault(event["discord_id"], []).append(event)
    return [
        status_from(
            person, by_person.get(person["discord_id"], []),
            rank_roles=config.journey_rank_roles,
            interest_roles=config.journey_interest_roles,
        )
        for person in await db.people(present_only=True)
    ]


async def nudge_for(db: "Database", config, now: datetime | None = None) -> NudgeLists:
    return nudge_lists(
        await all_statuses(db, config), now or datetime.now(timezone.utc)
    )


# -- the Front door board's view-model ---------------------------------------------

RETENTION_AFTER = timedelta(days=30)
RETENTION_COHORT = timedelta(days=7)


@dataclass
class Stay:
    """One visit by one person: from a join to a leave (or to now)."""

    discord_id: str
    joined_at: str
    came_via: str = VIA_UNKNOWN
    greeted_at: str | None = None
    placed_at: str | None = None
    left_at: str | None = None
    picked: list[str] = field(default_factory=list)


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def stays(
    events: Iterable[Mapping],
    *,
    rank_roles: Sequence[str] = (),
    interest_roles: Sequence[str] = (),
) -> list[Stay]:
    """Cut the log into stays (a rejoin is a new stay). Pure.

    Only joins the journey saw count: someone who was here before it began
    (``baseline``) has no join event and so no stay.
    """
    out: list[Stay] = []
    current: dict[str, Stay] = {}
    for event in events:
        who, kind, data = event["discord_id"], event["type"], event.get("data") or {}
        if kind in (JOINED, REJOINED):
            current[who] = Stay(discord_id=who, joined_at=event["at"])
            out.append(current[who])
            continue
        stay = current.get(who)
        if stay is None:
            continue
        if kind == CAME_VIA:
            stay.came_via = data.get("code") or VIA_UNKNOWN
        elif kind == GREETED and stay.greeted_at is None:
            stay.greeted_at = event["at"]
        elif kind == PLACED and stay.placed_at is None:
            stay.placed_at = event["at"]
        elif kind == ROLES_CHANGED:
            added = data.get("added") or []
            if stay.placed_at is None and any(_in(r, rank_roles) for r in added):
                stay.placed_at = event["at"]
            for role in added:
                if _in(role, interest_roles) and role not in stay.picked:
                    stay.picked.append(role)
        elif kind == LEFT:
            stay.left_at = event["at"]
            current.pop(who, None)
    return out


@dataclass(frozen=True)
class Rate:
    """``done`` out of ``of`` (a cohort); no percentage when nobody is due."""

    done: int = 0
    of: int = 0

    @property
    def percent(self) -> int | None:
        return round(100 * self.done / self.of) if self.of else None


@dataclass(frozen=True)
class Window:
    """The front door over one look-back window."""

    days: int
    joins: int = 0
    by_source: tuple[tuple[str, int], ...] = ()
    greeted: Rate = Rate()
    placed: Rate = Rate()
    leavers: int = 0
    picked: tuple[tuple[str, int], ...] = ()
    leads: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrontDoor:
    windows: tuple[Window, ...]
    still_here: Rate


def _tally(items: Iterable[str]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold())))


def _within(start: str, end: str | None, limit: timedelta) -> bool:
    return end is not None and _when(end) - _when(start) <= limit


def front_door(
    events: Sequence[Mapping],
    now: datetime,
    *,
    labels: Mapping[str, str],
    rank_roles: Sequence[str] = (),
    interest_roles: Sequence[str] = (),
    windows: Sequence[int] = (7, 30),
) -> FrontDoor:
    """How the front door is doing, from the log alone. Pure.

    The two response rates only count people who have been here long enough
    for the deadline to have passed (a member who joined an hour ago is not a
    missed greeting yet), so the percentage never dips just because someone
    arrived. "Still here" follows the cohort that joined 30 to 37 days ago.
    """
    all_stays = stays(events, rank_roles=rank_roles, interest_roles=interest_roles)
    out = []
    for days in windows:
        since = now - timedelta(days=days)
        recent = [s for s in all_stays if _when(s.joined_at) >= since]
        greet_due = [s for s in recent if now - _when(s.joined_at) >= GREET_AFTER]
        place_due = [s for s in recent if now - _when(s.joined_at) >= PLACE_AFTER]
        out.append(Window(
            days=days,
            joins=len(recent),
            by_source=_tally(invite_label(s.came_via, labels) for s in recent),
            greeted=Rate(
                sum(_within(s.joined_at, s.greeted_at, GREET_AFTER) for s in greet_due),
                len(greet_due),
            ),
            placed=Rate(
                sum(_within(s.joined_at, s.placed_at, PLACE_AFTER) for s in place_due),
                len(place_due),
            ),
            leavers=sum(
                1 for e in events if e["type"] == LEFT and _when(e["at"]) >= since
            ),
            picked=_tally(role for s in recent for role in s.picked),
            leads=tuple(
                (e.get("data") or {}).get("name") or "a scout lead"
                for e in events
                if e["type"] == LEAD_JOINED and _when(e["at"]) >= since
            ),
        ))
    cohort = [
        s for s in all_stays
        if RETENTION_AFTER <= now - _when(s.joined_at) < RETENTION_AFTER + RETENTION_COHORT
    ]
    stayed = sum(
        1 for s in cohort
        if s.left_at is None or _when(s.left_at) - _when(s.joined_at) >= RETENTION_AFTER
    )
    return FrontDoor(windows=tuple(out), still_here=Rate(stayed, len(cohort)))


# -- scout leads who joined ---------------------------------------------------------

_OPEN_LEAD_STATES = ("new", "contacted", "trialing")


async def reconcile_scout_leads(db: "Database") -> int:
    """Close the loop on recruitment: a scouted lead whose character is now
    linked to someone who joined through the journey is moved to ``joined`` and
    a ``lead_joined`` event is logged on that person. Returns how many moved.

    Characters come from the officer links (``members.discord_id`` and
    ``people.linked_main``); people who were here before the journey are not
    "new" and are skipped. Idempotent: a lead already ``joined`` (or one an
    officer marked ``passed``) is left alone.
    """
    leads = {
        (fold(lead["name"]), lead["realm"].lower()): lead
        for lead in await db.leads(limit=1000)
        if lead["state"] in _OPEN_LEAD_STATES
    }
    if not leads:
        return 0
    newcomers = {
        p["discord_id"]: p for p in await db.people()
        if p["came_via"] != VIA_BASELINE
    }
    owned: dict[tuple[str, str], str] = {}
    for member in await db.all_members():
        if member.discord_id in newcomers:
            owned[(fold(member.character_name), member.realm_slug.lower())] = member.discord_id
    for discord_id, person in newcomers.items():
        if person["linked_main"]:
            name, realm, _region = person["linked_main"].split("|")
            owned.setdefault((fold(name), realm), discord_id)
    moved = 0
    for key, lead in leads.items():
        discord_id = owned.get(key)
        if discord_id is None:
            continue
        await db.set_lead_state(lead["id"], "joined")
        await db.add_journey_event(
            discord_id, LEAD_JOINED,
            data={"lead_id": lead["id"], "name": lead["name"], "realm": lead["realm"]},
        )
        moved += 1
    return moved
