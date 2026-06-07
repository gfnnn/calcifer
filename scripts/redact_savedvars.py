"""Sanitise a real SavedVariables ``.lua`` into a committable test fixture.

A real guild dump is the only way to confirm the spine's *inferred* addon shapes
(see ``docs/data-spine-validation.md``), but it carries guild PII. This rewrites
one in place of the data, not the structure:

* character names  -> stable pseudonyms (``Player1-Redacted``), kept consistent
  across every reference (a main, its alts, its loot rows all rename together);
* GUIDs            -> a hash, so they stay distinct but reveal nothing;
* free-text notes  -> emptied.

The table *shape* is preserved, so the output is still valid Lua the parsers
ingest — drop a redacted dump into ``tests/fixtures/`` to lock the real shape in.

Usage::

    python -m scripts.redact_savedvars Guild_Roster_Manager.lua > fixture.lua
"""

from __future__ import annotations

import hashlib
import re
import sys
from typing import Any

from ftabot.services.savedvars import dump, parse_savedvariables

_NAME_KEYS = {"name", "Name", "mainName", "main"}
_NOTE_KEYS = {"customNote", "officerNote", "note", "Note", "officerNoteLabel"}
_GUID_KEYS = {"GUID", "guid"}
_ALT_KEYS = {"alts", "removedAlts"}
_GUID_RE = re.compile(r"^Player-\d+-[0-9A-Fa-f]+$")


def _hash_guid(guid: str) -> str:
    digest = hashlib.sha1(guid.encode()).hexdigest()[:8].upper()
    return f"Player-0-{digest}"


def collect_names(parsed: Any, names: set[str] | None = None) -> set[str]:
    """Every character name that appears in name-bearing positions.

    Walks name fields, RCLootCouncil's name-keyed loot table, and the first
    (name) slot of positional alt entries, so all of a person's occurrences are
    found and can be renamed together.
    """
    names = set() if names is None else names
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if key in _NAME_KEYS and isinstance(value, str):
                names.add(value)
            if key in _ALT_KEYS and isinstance(value, dict):
                first = value.get(1)
                if isinstance(first, str):  # positional {1=name, 2=class}
                    names.add(first)
            # RCLootCouncil keys its awards by "Name-Realm".
            if key == "RCLootCouncilLootDB" and isinstance(value, dict):
                names.update(k for k in value if isinstance(k, str))
            collect_names(value, names)
    elif isinstance(parsed, (list, tuple)):
        for item in parsed:
            collect_names(item, names)
    return names


def _name_map(names: set[str]) -> dict[str, str]:
    return {
        name: f"Player{i}-Redacted" for i, name in enumerate(sorted(names), start=1)
    }


def _redact(node: Any, names: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out: dict[Any, Any] = {}
        for key, value in node.items():
            new_key = names.get(key, key) if isinstance(key, str) else key
            if key in _NOTE_KEYS and isinstance(value, str):
                out[new_key] = ""
            elif key in _GUID_KEYS and isinstance(value, str):
                out[new_key] = _hash_guid(value)
            else:
                out[new_key] = _redact(value, names)
        return out
    if isinstance(node, list):
        return [_redact(v, names) for v in node]
    if isinstance(node, str):
        if node in names:
            return names[node]
        if _GUID_RE.match(node):
            return _hash_guid(node)
        return node
    return node


def redact(parsed: dict[str, Any]) -> dict[str, Any]:
    """Return a PII-free copy of a parsed SavedVariables mapping."""
    names = _name_map(collect_names(parsed))
    return _redact(parsed, names)


def redact_text(raw: str) -> str:
    """Parse, redact and re-serialise a SavedVariables ``.lua`` string."""
    return dump(redact(parse_savedvariables(raw)))


def residual_names(redacted_text: str, originals: set[str]) -> set[str]:
    """Any original name still present in the output — a redaction leak."""
    return {name for name in originals if name and name in redacted_text}


def main(argv: list[str]) -> int:  # pragma: no cover - thin file/IO wrapper
    if len(argv) != 1:
        print("usage: python -m scripts.redact_savedvars <file.lua>", file=sys.stderr)
        return 2
    raw = open(argv[0], encoding="utf-8").read()
    originals = collect_names(parse_savedvariables(raw))
    out = redact_text(raw)
    leaked = residual_names(out, originals)
    if leaked:
        print(f"WARNING: {len(leaked)} name(s) still present after redaction: "
              f"{sorted(leaked)[:5]}", file=sys.stderr)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
