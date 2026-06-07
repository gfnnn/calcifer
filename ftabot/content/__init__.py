"""GM-editable content for Calcifer's managed posts (guides, rules/info).

Per the vision (§3), static prose and link lists live in the repo as plain JSON
the GM edits via a GitHub change request; Calcifer *owns* the posted message and
renders it. This loader reads ``ftabot/content/<name>.json`` and falls back to a
bundled default when the file is missing or malformed, so a board never crashes
on a typo in a hand-edited file.
"""

from __future__ import annotations

import json
import os

_DIR = os.path.dirname(__file__)


def load(name: str, default: dict | None = None) -> dict:
    """Load ``<name>.json`` from this package, or ``default`` (or {}) on error."""
    path = os.path.join(_DIR, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else (default or {})
    except (OSError, json.JSONDecodeError):
        return default or {}
