"""Pure account grouping over identity records — the heart of the data spine.

Turns a flat list of GRM ``IdentityRecord``s into **accounts**: connected
components of the alt<->main graph. Each character is a node keyed by
``(lower(name), realm_slug)``; a record links itself to every alt it lists and
to its declared main. Each component is one person, and we pick its **main**
from GRM's ``isMain`` flag first, then the node others point at as their main,
then the earliest join date, then a stable name order.

No I/O — like ``metrics.py`` and ``router.py``, this is unit-tested directly so
the overlay onto the roster can trust it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import IdentityRecord, split_character_key

Key = tuple[str, str]


@dataclass
class Account:
    """One person: the chosen main character and their alts (by key)."""

    main: Key
    alts: list[Key] = field(default_factory=list)

    @property
    def members(self) -> list[Key]:
        return [self.main, *self.alts]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Key, Key] = {}

    def add(self, key: Key) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: Key) -> Key:
        self.add(key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        # Path-compress so repeated lookups stay cheap.
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: Key, b: Key) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _ref_key(name_realm: str, default_realm: str) -> Key:
    name, realm = split_character_key(name_realm, default_realm)
    return (name.lower(), realm)


def build_accounts(
    records: list[IdentityRecord], *, default_realm: str = "hellfire"
) -> list[Account]:
    """Group identity records into accounts (connected alt<->main components)."""
    uf = _UnionFind()
    main_votes: dict[Key, int] = {}
    join_dates: dict[Key, str] = {}
    declared_main: set[Key] = set()

    for rec in records:
        key = rec.key
        uf.add(key)
        if rec.join_date and key not in join_dates:
            join_dates[key] = rec.join_date
        if rec.is_main:
            declared_main.add(key)
        for alt in rec.alts:
            uf.union(key, _ref_key(alt, default_realm))
        if rec.main_name:
            main_key = _ref_key(rec.main_name, default_realm)
            uf.union(key, main_key)
            main_votes[main_key] = main_votes.get(main_key, 0) + 1

    components: dict[Key, list[Key]] = {}
    for key in list(uf.parent):
        components.setdefault(uf.find(key), []).append(key)

    accounts: list[Account] = []
    for members in components.values():
        main = _choose_main(members, declared_main, main_votes, join_dates)
        alts = sorted(k for k in members if k != main)
        accounts.append(Account(main=main, alts=alts))
    accounts.sort(key=lambda a: a.main)
    return accounts


def _choose_main(
    members: list[Key],
    declared_main: set[Key],
    main_votes: dict[Key, int],
    join_dates: dict[Key, str],
) -> Key:
    """Pick the account's main: GRM flag > most-pointed-at > earliest join.

    Ties always break toward the earlier join date (an unknown date sorts last)
    and then the key, so the choice is deterministic.
    """
    def by_join(key: Key) -> tuple[str, Key]:
        return (join_dates.get(key, "9999-99-99"), key)

    flagged = [k for k in members if k in declared_main]
    if flagged:
        return min(flagged, key=by_join)
    voted = [k for k in members if k in main_votes]
    if voted:
        return min(voted, key=lambda k: (-main_votes[k], *by_join(k)))
    dated = [k for k in members if k in join_dates]
    if dated:
        return min(dated, key=by_join)
    return min(members)
