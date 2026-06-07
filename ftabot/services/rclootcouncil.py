"""Map RCLootCouncil's loot history (a SavedVariables dump) into ``LootEntry``.

RCLootCouncil persists its council ledger to ``RCLootCouncilLootDB`` in
``WTF/.../SavedVariables/RCLootCouncil.lua``::

    RCLootCouncilLootDB = {
        ["Player-Realm"] = {
            { lootWon = "<item link>", date = "2026/06/01", time = "20:15:30",
              instance = "Manaforge Omega-Mythic", boss = "Nexus-King",
              response = "BiS", responseID = 1, votes = 5, difficultyID = 16,
              itemReplaced1 = "<item link>", ... },
            ...
        },
    }

This is the real loot ledger the WoW Audit API does not expose (see
``docs/data-sources.md`` §1/§5). The mapper is defensive: every field is
optional, date formats (old ``d/m/y`` and current ``!%Y/%m/%d``) are both
handled, and a malformed award is skipped rather than aborting the import.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import LootEntry, normalize_realm

# RCLootCouncil stores the Blizzard ``difficultyID``; map the ones that show up
# on raid loot to a readable name. Unknown ids fall back to the raw number.
_DIFFICULTY: dict[int, str] = {
    1: "Normal",
    2: "Heroic",
    7: "LFR",
    8: "Mythic Keystone",
    14: "Normal",
    15: "Heroic",
    16: "Mythic",
    17: "LFR",
    23: "Mythic",
    33: "Timewalking",
}

_ITEM_ID = re.compile(r"Hitem:(\d+)")
_ITEM_NAME = re.compile(r"\|h\[(.*?)\]\|h")


def _split_player(key: str) -> tuple[str, str]:
    """Split an RCLootCouncil ``"Name-Realm"`` key into (name, realm_slug)."""
    name, _, realm = key.partition("-")
    return name.strip(), normalize_realm(realm) if realm.strip() else ""


def _parse_item_link(link: Any) -> tuple[int | None, str | None]:
    """Pull (item_id, item_name) out of a WoW item link, tolerating junk."""
    if not isinstance(link, str):
        return None, None
    id_match = _ITEM_ID.search(link)
    name_match = _ITEM_NAME.search(link)
    item_id = int(id_match.group(1)) if id_match else None
    return item_id, (name_match.group(1) if name_match else None)


def _normalize_when(date: Any, time: Any) -> str | None:
    """Combine RCLootCouncil's date + time into a sortable ISO-8601 stamp.

    Handles the current ``YYYY/MM/DD`` form and the older European ``DD/MM/YY``;
    returns ``None`` (rather than guessing) when the shape is unrecognised, so
    the dedup key and "most recent" ordering stay trustworthy.
    """
    if not isinstance(date, str) or not date.strip():
        return None
    parts = re.split(r"[/-]", date.strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    if len(parts[0]) == 4:  # YYYY/MM/DD (current default)
        year, month, day = parts
    else:  # DD/MM/YY (older default); two-digit year -> 2000s
        day, month, year = parts
        if len(year) == 2:
            year = f"20{year}"
    try:
        iso = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None
    clock = str(time).strip() if isinstance(time, (str, int)) else ""
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", clock):
        bits = [int(b) for b in clock.split(":")]
        while len(bits) < 3:
            bits.append(0)
        return f"{iso}T{bits[0]:02d}:{bits[1]:02d}:{bits[2]:02d}"
    return iso


def _difficulty_name(award: dict) -> str | None:
    """Readable difficulty from ``difficultyID``, else the instance suffix."""
    diff_id = award.get("difficultyID")
    if isinstance(diff_id, int):
        return _DIFFICULTY.get(diff_id, str(diff_id))
    instance = award.get("instance")
    if isinstance(instance, str) and "-" in instance:
        return instance.rsplit("-", 1)[1].strip() or None
    return None


def _award_to_entry(name: str, realm: str, award: dict, default_region: str) -> LootEntry | None:
    if not isinstance(award, dict):
        return None
    item_id, item_name = _parse_item_link(award.get("lootWon"))
    votes = award.get("votes")
    return LootEntry(
        character_name=name,
        realm_slug=realm or "hellfire",
        region=default_region,
        item_id=item_id,
        item_name=item_name,
        instance=award.get("instance") if isinstance(award.get("instance"), str) else None,
        boss=award.get("boss") if isinstance(award.get("boss"), str) else None,
        difficulty=_difficulty_name(award),
        response=award.get("response") if isinstance(award.get("response"), str) else None,
        votes=votes if isinstance(votes, int) else None,
        awarded_at=_normalize_when(award.get("date"), award.get("time")),
        source="rclc",
        raw=award,
    )


def loot_from_savedvars(data: dict, *, default_region: str = "eu") -> list[LootEntry]:
    """Turn a parsed SavedVariables dict into de-duplicated ``LootEntry`` rows.

    Accepts either the whole file dict (with a ``RCLootCouncilLootDB`` key) or
    the loot table itself. Awards lacking both an item id and a timestamp are
    dropped, since they cannot be de-duplicated or ordered meaningfully.
    """
    table = data.get("RCLootCouncilLootDB", data) if isinstance(data, dict) else {}
    entries: list[LootEntry] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for player_key, awards in (table or {}).items():
        if not isinstance(player_key, str) or not isinstance(awards, dict):
            continue
        name, realm = _split_player(player_key)
        if not name:
            continue
        # Lua array tables parse to ``{1: ..., 2: ...}``; iterate values in order.
        for _, award in sorted(awards.items(), key=lambda kv: kv[0] if isinstance(kv[0], int) else 0):
            entry = _award_to_entry(name, realm, award, default_region)
            if entry is None or (entry.item_id is None and entry.awarded_at is None):
                continue
            if entry.dedup_key in seen:
                continue
            seen.add(entry.dedup_key)
            entries.append(entry)
    return entries


def parse_loot(text: str, *, default_region: str = "eu") -> list[LootEntry]:
    """Parse RCLootCouncil ``.lua`` text straight into ``LootEntry`` rows."""
    from .savedvars import parse_savedvariables

    return loot_from_savedvars(parse_savedvariables(text), default_region=default_region)
