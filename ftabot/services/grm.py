"""Map Guild Roster Manager (GRM) SavedVariables into identity records.

GRM is the officer-maintained *identity* source the public APIs cannot give us:
alt<->main groupings, join dates, ranks and notes (see ``docs/data-sources.md``
§5). It persists to ``GRM_GuildMemberHistory_Save``, nested by faction then
guild, with auxiliary tables. The exact nesting and field names vary across GRM
releases, so this parser is deliberately **shape-agnostic**: it walks the
structure and pulls out anything that looks like a member record (a table
carrying GRM's strong member markers), tolerating numeric-array *and*
name-keyed guild buckets, and named *and* positional alt entries. Every field
is optional; an unrecognised member is skipped, never fatal.

Validate against a real guild ``Guild_Roster_Manager.lua`` and tighten the
aliases below if a field is named differently in that release.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import IdentityRecord, split_character_key

_NAME_KEYS = ("name", "Name")
# A member record carries the player name *and* at least one of these. Alt
# sub-records (just name+class) lack them, so we never mistake an alt for a
# top-level member while walking the tree.
_STRONG_MARKERS = (
    "GUID", "guid", "rankName", "rank", "rankIndex", "joinDateUnix", "joinDate",
    "joinDateHist", "isMain", "alts", "removedAlts", "nameHistory", "leftGuildDate",
)


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d.get(k)
    return None


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _iso_from_epoch(epoch: float) -> str | None:
    # Guard against junk: only accept plausible post-2000 second timestamps.
    if not (946_684_800 <= epoch <= 4_102_444_800):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()


def _find_epoch(node: Any, depth: int = 0) -> float | None:
    """Earliest plausible unix-seconds value nested anywhere in ``node``.

    Used for join dates: ``joinDateHist`` is a *history* of join/leave events in
    unspecified order, so the earliest timestamp is the original join — take the
    minimum rather than the first one we happen to encounter.
    """
    if depth > 4:
        return None
    if isinstance(node, bool):  # bools are ints in Python; never an epoch
        return None
    if isinstance(node, (int, float)) and 946_684_800 <= node <= 4_102_444_800:
        return float(node)
    children: Any = None
    if isinstance(node, dict):
        children = node.values()
    elif isinstance(node, (list, tuple)):
        children = node
    if children is None:
        return None
    found = [e for v in children if (e := _find_epoch(v, depth + 1)) is not None]
    return min(found) if found else None


def _is_member_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(_first(value, _NAME_KEYS), str):
        return False
    return any(marker in value for marker in _STRONG_MARKERS)


def _collect_members(node: Any, out: list[dict], depth: int = 0) -> None:
    """Walk faction/guild nesting and gather member records (not their alts)."""
    if depth > 8:
        return
    if _is_member_record(node):
        out.append(node)
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect_members(value, out, depth + 1)


def _join_date(member: dict) -> str | None:
    unix = _first(member, ("joinDateUnix",))
    if isinstance(unix, str):  # SavedVariables often quote numeric values
        try:
            unix = float(unix.strip())
        except ValueError:
            unix = None
    if isinstance(unix, (int, float)) and not isinstance(unix, bool):
        iso = _iso_from_epoch(float(unix))
        if iso:
            return iso
    explicit = _str(member.get("joinDate"))
    if explicit:
        return explicit
    epoch = _find_epoch(member.get("joinDateHist"))
    return _iso_from_epoch(epoch) if epoch else None


def _main_name(member: dict) -> str | None:
    if _bool(member.get("isMain")):
        return None
    for key in ("mainName", "main"):
        v = _str(member.get(key))
        if v and "-" in v:
            return v
    mtl = member.get("mainAtTimeOfLeaving")
    if isinstance(mtl, dict):
        return _str(mtl.get(1))
    return None


def _alt_name(alt: Any) -> str | None:
    if isinstance(alt, str):
        return _str(alt)
    if isinstance(alt, dict):
        return _str(_first(alt, _NAME_KEYS)) or _str(alt.get(1))
    return None


def _alts(member: dict) -> list[str]:
    alts = member.get("alts")
    if not isinstance(alts, dict):
        return []
    out: list[str] = []
    for _, alt in sorted(alts.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else 0):
        name = _alt_name(alt)
        if name:
            out.append(name)
    return out


def _record(member: dict, default_region: str, default_realm: str) -> IdentityRecord | None:
    raw_name = _first(member, _NAME_KEYS)
    if not isinstance(raw_name, str):
        return None
    name, realm = split_character_key(raw_name, default_realm)
    if not name:
        return None
    return IdentityRecord(
        character_name=name,
        realm_slug=realm,
        region=default_region,
        guid=_str(_first(member, ("GUID", "guid"))),
        class_name=_str(member.get("class")),
        rank_name=_str(_first(member, ("rankName", "rank"))),
        join_date=_join_date(member),
        is_main=_bool(member.get("isMain")),
        main_name=_main_name(member),
        alts=_alts(member),
        note=_str(_first(member, ("customNote", "note"))),
        officer_note=_str(member.get("officerNote")),
        raw=member,
    )


def identities_from_savedvars(
    data: dict, *, default_region: str = "eu", default_realm: str = "hellfire"
) -> list[IdentityRecord]:
    """Pull identity records out of a parsed GRM SavedVariables dict.

    Accepts the whole file (with ``GRM_GuildMemberHistory_Save``) or that table
    directly. Records are de-duplicated on ``(name, realm)``.
    """
    history: Any = data
    if isinstance(data, dict) and "GRM_GuildMemberHistory_Save" in data:
        history = data["GRM_GuildMemberHistory_Save"]
    members: list[dict] = []
    _collect_members(history, members)

    records: list[IdentityRecord] = []
    seen: set[tuple[str, str]] = set()
    for member in members:
        rec = _record(member, default_region, default_realm)
        if rec is None or rec.key in seen:
            continue
        seen.add(rec.key)
        records.append(rec)
    return records


def parse_identities(
    text: str, *, default_region: str = "eu", default_realm: str = "hellfire"
) -> list[IdentityRecord]:
    """Parse GRM ``.lua`` text straight into identity records."""
    from .savedvars import parse_savedvariables

    return identities_from_savedvars(
        parse_savedvariables(text), default_region=default_region, default_realm=default_realm
    )
