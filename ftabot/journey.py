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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

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

EVENT_TYPES: tuple[str, ...] = (
    JOINED, LEFT, REJOINED, CAME_VIA, ROLES_CHANGED,
    GREETED, PLACED, CHECKED_IN, CHARACTER_LINKED,
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
