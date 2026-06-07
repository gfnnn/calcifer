"""Calcifer — the persona + branding layer for Calcifer's feed alerts.

A little blue flame in the spirit of Calcifer from *Howl's Moving Castle*:
sardonic, a touch lazy, and literally the hearth the guild runs on. This is a
thin voice layer — a flavour-line bank wrapped around otherwise-structured
embeds — plus the colour palette that distinguishes "Calcifer relayed a feed"
(blue flame) from "guild data" (the ember tone the rest of the bot uses).
"""

from __future__ import annotations

import random

# --- colours ----------------------------------------------------------------

GUILD_EMBER = 0xCC4422  # the existing guild-data tone (rosters, readiness…)
BLUE_FLAME = 0x2EA3FF  # Calcifer talking: info-level feed alerts
AMBER = 0xF5A623  # warning: needs attention soon
EMBER_RED = 0xCC2222  # critical: act now

_SEVERITY_COLORS = {
    "info": BLUE_FLAME,
    "warning": AMBER,
    "critical": EMBER_RED,
}


def color_for(severity: str = "info") -> int:
    """Embed colour for a feed alert's severity."""
    return _SEVERITY_COLORS.get(severity, BLUE_FLAME)


# --- voice ------------------------------------------------------------------

# Tone dial: "off" silences flavour, "light" is occasional, "full" is chatty.
_TONE_PROB = {"off": 0.0, "light": 0.5, "full": 1.0}

# Flavour openers keyed by event type, with a generic fallback. Kept short so
# they garnish an alert without burying the actual content.
_LINES: dict[str, list[str]] = {
    "wowhead.news": [
        "Fresh off the forge.",
        "Ooh, gossip from the wider world.",
        "Don't tell Howl I'm reading the news again.",
    ],
    "wowhead.blue-post": [
        "A blue flicker. The developers spoke.",
        "Word from on high, still warm.",
    ],
    "wowhead.patch-notes": [
        "Big logs on the fire today: patch notes.",
        "Everything's changing again. Typical.",
    ],
    "digest": [
        "A week's worth of embers, swept up neatly.",
        "Here's everything I held back. You're welcome.",
        "I've been keeping a list.",
    ],
    "_default": [
        "Something's crackling.",
        "Mind the sparks.",
    ],
}


def flavour(event_type: str, *, tone: str = "light", rng: random.Random | None = None) -> str | None:
    """A Calcifer one-liner for an event type, or ``None`` if the tone is off.

    ``rng`` is injectable so tests are deterministic.
    """
    prob = _TONE_PROB.get(tone, 0.5)
    if prob <= 0:
        return None
    r = rng or random
    if prob < 1.0 and r.random() > prob:
        return None
    lines = _LINES.get(event_type) or _LINES["_default"]
    return r.choice(lines)
