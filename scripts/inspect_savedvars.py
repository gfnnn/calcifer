"""Print the shape of a SavedVariables ``.lua`` so addon assumptions can be
confirmed or fixed fast (see ``docs/data-spine-validation.md``).

Reports which globals/addons are present, the top-level structure, and a couple
of sample member/loot records, formalising the eyeball commands in the doc.

Usage::

    python -m scripts.inspect_savedvars Guild_Roster_Manager.lua
"""

from __future__ import annotations

import sys
from typing import Any

from ftabot.services.grm import alt_groups_from_savedvars, identities_from_savedvars
from ftabot.services.rclootcouncil import loot_from_savedvars
from ftabot.services.savedvars import parse_savedvariables

_KNOWN = {
    "GRM_GuildMemberHistory_Save": "GRM (Guild Roster Manager) identity",
    "RCLootCouncilLootDB": "RCLootCouncil loot history",
    "GRM_PlayerListOfAlts_Save": "GRM alt list (alt<->main grouping)",
}


def _shape(value: Any, depth: int = 0, max_depth: int = 2) -> str:
    """A compact, type-only sketch of a nested value (no data, no PII)."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        if depth >= max_depth:
            return f"{{…{len(value)} keys…}}"
        sample_key = next(iter(value))
        kind = "int-keyed" if isinstance(sample_key, int) else "str-keyed"
        return f"{{{kind}, {len(value)} entries; e.g. {sample_key!r}: " \
               f"{_shape(value[sample_key], depth + 1, max_depth)}}}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    return type(value).__name__


def summarize(parsed: dict[str, Any]) -> str:
    """A human-readable report of a parsed SavedVariables mapping."""
    lines: list[str] = []
    lines.append(f"Globals: {len(parsed)}")
    for name, value in parsed.items():
        label = _KNOWN.get(name, "(unrecognised)")
        lines.append(f"  • {name} — {label}")
        lines.append(f"      shape: {_shape(value)}")

    if "GRM_GuildMemberHistory_Save" in parsed:
        records = identities_from_savedvars(parsed)
        lines.append(f"GRM identities parsed: {len(records)}")
        for rec in records[:3]:
            alts = ", ".join(a[0] for a in rec.alts) or "none"
            lines.append(
                f"  • {rec.character_name}-{rec.realm_slug} "
                f"(main={rec.is_main}, joined={rec.join_date or '?'}, alts: {alts})"
            )
    if "GRM_PlayerListOfAlts_Save" in parsed:
        groups = alt_groups_from_savedvars(parsed)
        lines.append(f"GRM alt groups parsed: {len(groups)}")
        for group in groups[:3]:
            lines.append(f"  • {' = '.join(group)}")
    if "RCLootCouncilLootDB" in parsed:
        loot = loot_from_savedvars(parsed)
        lines.append(f"RCLootCouncil loot rows parsed: {len(loot)}")
        for entry in loot[:3]:
            lines.append(
                f"  • {entry.character_name}: {entry.item_name or entry.item_id} "
                f"({entry.difficulty or '?'})"
            )
    return "\n".join(lines)


def main(argv: list[str]) -> int:  # pragma: no cover - thin file/IO wrapper
    if len(argv) != 1:
        print("usage: python -m scripts.inspect_savedvars <file.lua>", file=sys.stderr)
        return 2
    raw = open(argv[0], encoding="utf-8").read()
    print(summarize(parse_savedvariables(raw)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
