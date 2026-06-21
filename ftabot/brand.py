"""Calcifer's brand + voice layer: one source of truth for colour, icon and
persona that every surface (boards, feed alerts, command replies) renders
through, so thirty different messages read as one product.

Pure and offline-testable: no I/O, no Discord objects. The design-system rules
live here as code:

* **Never an em dash** in anything Calcifer generates (``sanitize``); an en dash
  may appear once or twice but is never produced here.
* **Snark targets situations, never people** — a guideline for line authors,
  enforced by curation rather than code.
* **Domain pacing** — fast and excited for Mythic+, measured for raid & logs —
  is carried by which bank a line comes from.

Tone (``off`` / ``light`` / ``full``) gates *flavour* garnish, never the
functional structural responses, which always return a line.
"""

from __future__ import annotations

import random
from enum import Enum

# --- colour -----------------------------------------------------------------
# The guild's own fire is ember; Calcifer's own flame is blue. Severity warms
# from blue (info) through amber (warning) to ember-red (act now).
GUILD_EMBER = 0xCC4422
BLUE_FLAME = 0x2EA3FF
AMBER = 0xF5A623
EMBER_RED = 0xCC2222

_SEVERITY_COLORS = {"info": BLUE_FLAME, "warning": AMBER, "critical": EMBER_RED}


def color_for(severity: str = "info") -> int:
    """Embed accent colour for a severity (defaults to Calcifer's blue flame)."""
    return _SEVERITY_COLORS.get(severity, BLUE_FLAME)


# --- iconography ------------------------------------------------------------
# Unicode fallbacks. Once the custom emoji are uploaded the cog/board layer
# resolves the matching ``:name:`` from the guild and overrides these by key.
ICONS: dict[str, str] = {
    "mplus": "🔑",
    "kill": "💀",
    "progress": "🔥",
    "events": "📅",
    "news": "📰",
    "bluepost": "🔵",
    "vault": "🎁",
    "recruit": "🔎",
    "tank": "🛡️",
    "healer": "💚",
    "dps": "⚔️",
    "flame": "🔥",
    "up": "▲",
    "down": "▼",
}


def icon(key: str) -> str:
    """A domain glyph by key (unicode fallback)."""
    return ICONS.get(key, "")


# Custom-emoji names per key: the single source of truth for the ``:name:`` the
# bot looks up in the guild's emoji list at runtime (design-system §4a). Where a
# matching custom emoji is uploaded, the cog/board layer overrides the unicode
# ``ICONS`` fallback above by resolving the name here; anything not uploaded just
# keeps its unicode glyph. The role keys use the guild's *existing* emoji names
# (``roletank``/``roleheal``/``roledps``) rather than the underscore variants, so
# the already-uploaded role emoji resolve without a re-upload.
EMOJI_NAMES: dict[str, str] = {
    # Tier 2 — domain glyphs
    "mplus": "fta_keystone",
    "kill": "fta_skull",
    "progress": "fta_flame",
    "flame": "fta_flame",
    "events": "fta_calendar",
    "news": "fta_scroll",
    "vault": "fta_vault",
    "recruit": "fta_magnify",
    "bluepost": "fta_bluepost",
    # Tier 3 — roles (existing server names, no underscore)
    "tank": "roletank",
    "healer": "roleheal",
    "dps": "roledps",
}


def emoji_name(key: str) -> str | None:
    """The custom-emoji ``:name:`` for a key, or ``None`` to use the unicode fallback."""
    return EMOJI_NAMES.get(key)


# Class crest emoji (design-system §4a, Tier 4): the ``:class_*:`` set uploaded
# to the guild, keyed by the class display name as it appears in
# ``models.CLASSES``/``CLASS_COLORS`` (title case). Used on member cards and
# leaderboard rows. Evoker has no crest in the uploaded set, so it resolves to
# ``None`` and falls back to the class colour with no icon.
CLASS_EMOJI: dict[str, str] = {
    "Death Knight": "class_dk",
    "Demon Hunter": "class_dh",
    "Druid": "class_druid",
    "Hunter": "class_hunter",
    "Mage": "class_mage",
    "Monk": "class_monk",
    "Paladin": "class_paladin",
    "Priest": "class_priest",
    "Rogue": "class_rogue",
    "Shaman": "class_shaman",
    "Warlock": "class_warlock",
    "Warrior": "class_warrior",
}


def class_emoji_name(class_name: str | None) -> str | None:
    """The ``:class_*:`` crest name for a class, or ``None`` if none is uploaded."""
    if not class_name:
        return None
    return CLASS_EMOJI.get(class_name.title())


# --- emoji rendering (the reusable bot-wide layer) --------------------------
# A render takes an optional ``emoji`` map (custom-emoji ``name`` -> its
# ``<:name:id>`` markup, built from the guild at the bot/board layer). When the
# uploaded art is present these helpers return the custom emoji; otherwise they
# fall back to the unicode glyph (roles) or to nothing (class crests, which have
# no unicode equivalent). Pure: the map is injected, never looked up here.
EmojiMap = dict[str, str]


def _custom(name: str | None, emoji: EmojiMap | None) -> str | None:
    return emoji.get(name) if (name and emoji) else None


def role_icon(role: str | None, emoji: EmojiMap | None = None) -> str:
    """The role glyph (``:roletank:``/``:roleheal:``/``:roledps:`` when uploaded,
    else the unicode shield/heart/swords). ``role`` is matched case-insensitively."""
    key = (role or "").lower()
    return _custom(EMOJI_NAMES.get(key), emoji) or ICONS.get(key, "")


def class_icon(class_name: str | None, emoji: EmojiMap | None = None) -> str:
    """The class crest (``:class_*:``) markup, or ``""`` when none is uploaded.

    The crest *is* the class colour made visible (Discord can't colour embed
    text), so it carries the per-class identity on rows and cards.
    """
    return _custom(class_emoji_name(class_name), emoji) or ""


def class_label(class_name: str | None, emoji: EmojiMap | None = None) -> str:
    """A class name prefixed with its colour crest when available: the canonical
    way to *post a class* so it always carries its colour. Falls back to the bare
    name (no crest) when the art isn't uploaded or the class is unknown."""
    name = class_name or "?"
    crest = class_icon(class_name, emoji)
    return f"{crest} {name}" if crest else name


# --- Mythic+ score colour scale (Raider.IO) ---------------------------------
# Raider.IO's score ramp as (min_score, hex) bands, highest first. ``score_color``
# returns the *exact* hex for surfaces where real colour is possible (the web
# dashboard, an embed accent). Discord embed *text* can't be coloured, so the
# board leaves the score plain rather than approximating the ramp with emoji.
SCORE_TIERS: tuple[tuple[int, int], ...] = (
    (4275, 0xFF8000), (4120, 0xF9753F), (4000, 0xF16961), (3880, 0xE75E7F),
    (3760, 0xDB529C), (3640, 0xCC47B9), (3520, 0xB83DD6), (3385, 0x9C3DED),
    (3265, 0x7659E6), (3145, 0x3E6AE0), (3000, 0x3A7FCE), (2880, 0x5597B3),
    (2760, 0x5FB198), (2640, 0x5DCA79), (2520, 0x4EE554), (2400, 0x1EFF00),
    (2275, 0x43FF2A), (2150, 0x5AFF3F), (2025, 0x6CFF50), (1900, 0x7BFF5E),
    (1775, 0x88FF6C), (1650, 0x94FF79), (1525, 0xA0FF85), (1400, 0xABFF91),
    (1275, 0xB5FF9D), (1150, 0xBFFFA9), (1025, 0xC8FFB4), (900, 0xD1FFBF),
    (775, 0xDAFFCB), (650, 0xE2FFD6), (525, 0xEAFFE2), (400, 0xF3FFED),
    (275, 0xFAFFF8), (200, 0xFFFFFF),
)


def score_color(score: float | None) -> int:
    """The Raider.IO scale colour (exact hex) for a Mythic+ score; white at the floor."""
    value = score or 0
    for threshold, hex_value in SCORE_TIERS:
        if value >= threshold:
            return hex_value
    return 0xFFFFFF


# --- moods ------------------------------------------------------------------
class Mood(str, Enum):
    """How Calcifer is feeling, expressed per-message via author icon + emoji."""

    THINKING = "thinking"
    SMUG = "smug"
    GRUMPY = "grumpy"
    DELIGHTED = "delighted"
    ALARMED = "alarmed"
    SLEEPY = "sleepy"
    SCHEMING = "scheming"
    DISMAL = "dismal"
    NEUTRAL = "neutral"


MOOD_EMOJI: dict[Mood, str] = {
    Mood.THINKING: "🤔",
    Mood.SMUG: "😏",
    Mood.GRUMPY: "😤",
    Mood.DELIGHTED: "🔥",
    Mood.ALARMED: "😱",
    Mood.SLEEPY: "😴",
    Mood.SCHEMING: "🤝",
    Mood.DISMAL: "💀",
    Mood.NEUTRAL: "",
}


def mood_emoji(mood: Mood) -> str:
    return MOOD_EMOJI.get(mood, "")


# Custom-emoji name per mood (design-system §4a, Tier 1). Same contract as
# ``EMOJI_NAMES``: resolved from the guild at runtime, unicode ``MOOD_EMOJI``
# fallback until the ``:calcifer_*:`` art is uploaded.
MOOD_EMOJI_NAMES: dict[Mood, str] = {
    Mood.THINKING: "calcifer_think",
    Mood.SMUG: "calcifer_smug",
    Mood.GRUMPY: "calcifer_grump",
    Mood.DELIGHTED: "calcifer_blaze",
    Mood.ALARMED: "calcifer_shock",
    Mood.SLEEPY: "calcifer_sleepy",
    Mood.SCHEMING: "calcifer_deal",
    Mood.DISMAL: "calcifer_dead",
}


def mood_emoji_name(mood: Mood) -> str | None:
    """The ``:calcifer_*:`` name for a mood, or ``None`` for the unicode fallback."""
    return MOOD_EMOJI_NAMES.get(mood)


# --- house style: no long dashes -------------------------------------------
_EM_DASH = "—"  # —


def has_em_dash(text: str | None) -> bool:
    return bool(text) and _EM_DASH in text


def sanitize(text: str | None) -> str | None:
    """Enforce the house style on generated copy: never an em dash.

    A safety net under the authored banks. Em dashes become a comma (the most
    neutral mid-sentence swap); spacing/punctuation is tidied. En dashes are
    left alone (the rule allows one or two).
    """
    if not text:
        return text
    out = text.replace(f" {_EM_DASH} ", ", ").replace(_EM_DASH, ", ")
    out = out.replace(" ,", ",").replace(",,", ",")
    while "  " in out:
        out = out.replace("  ", " ")
    return out.strip(" ,")


# --- voice ------------------------------------------------------------------
_TONE_PROB = {"off": 0.0, "light": 0.5, "full": 1.0}

# Starter banks (Claude-drafted, GM-curated). Two kinds of key:
#   * structural  — functional responses; always return a line.
#   * flavour     — optional garnish; tone-gated, may return nothing.
# Kept short and dry; situations never people; no em dashes. Banks grow in the
# charm phase.
_BANKS: dict[str, list[str]] = {
    # structural responses (high-frequency, freshened)
    "thinking": [
        "Hold on, stoking the embers...",
        "Give me a moment, I'm reading the coals.",
        "Working. The things I do for this contract.",
        "Patience. Even a fire needs a breath.",
    ],
    "not_found": [
        "No such name in my ashes.",
        "I don't see them on the roster. Spelled it right?",
        "Nobody by that name has darkened my hearth.",
        "Can't find them. They're not on my books.",
    ],
    "denied": [
        "Officers only. Take it up with a Flamekeeper.",
        "Not for you, I'm afraid. Flamekeepers keep this flame.",
        "That door's locked unless you keep the flame.",
    ],
    "empty": [
        "Nothing to show. The grate's cold here.",
        "Empty-handed this time. No embers to report.",
    ],
    "board_signoff": [
        "Calcifer, keeping the hearth lit.",
        "Tended by Calcifer. Mind the sparks.",
        "Kept warm by Calcifer.",
    ],
    # flavour garnish, by domain (mplus = fast/excited, raid/logs = measured)
    "mplus": [
        "The fire's roaring tonight.",
        "Go go go.",
        "Hot keys, hotter pace.",
    ],
    "raid": [
        "Steady. I've burned longer than this.",
        "Patient work. The good kind.",
    ],
    "logs": [
        "Let's see what the embers remember.",
        "The logs don't lie. Pity.",
    ],
    # recruitment + talent context
    "recruiting": [
        "Come stake your claim.",
        "We're looking for your kind.",
        "New blood warms the hearth.",
        "Prove yourself.",
    ],
    # loot + distribution context
    "loot": [
        "The spoils are waiting.",
        "Fair share to those who earn it.",
        "The vault remembers.",
        "Good loot makes for warm nights.",
    ],
    # attendance + signup context
    "attendance": [
        "Every hand at the hearth counts.",
        "The bench grows cold.",
        "Fresh faces keep the fire burning.",
    ],
    # warnings: something needs attention soon
    "alert_warning": [
        "Things are getting thin.",
        "Watch the embers here.",
        "This one needs eyes on it.",
    ],
    # critical: urgent action needed
    "alert_critical": [
        "The moment's here.",
        "This changes things.",
        "Mark this well.",
    ],
    # celebration: achievement/milestone
    "success": [
        "Well earned.",
        "The fire blazes tonight.",
        "That's the stuff legends are made of.",
    ],
    # empty recruitment board
    "empty_recruiting": [
        "The guild's full. For now.",
        "All hands on deck here.",
        "Room to spare later, maybe.",
    ],
    # empty event signups
    "empty_signups": [
        "The bench is waiting.",
        "No takers yet.",
        "Quieter than usual.",
    ],
    # errors: something broke
    "error": [
        "The flames flicker. Something's not right.",
        "My sight's clouded. Try again?",
        "The embers won't tell me. The forge is troubled.",
    ],
    "_default": [
        "Something's crackling.",
        "Mind the sparks.",
    ],
}


class Voice:
    """Picks lines from the banks with no-immediate-repeat memory.

    ``rng`` is injectable so tests are deterministic. ``tone`` gates *flavour*
    only; structural ``line()`` always returns something.
    """

    def __init__(
        self,
        banks: dict[str, list[str]] | None = None,
        *,
        tone: str = "light",
        rng: random.Random | None = None,
        memory: int = 2,
    ):
        self._banks = banks if banks is not None else _BANKS
        self._tone = tone
        self._rng = rng or random
        self._memory = max(0, memory)
        self._recent: dict[str, list[str]] = {}

    def _pick(self, key: str) -> str | None:
        pool = self._banks.get(key) or self._banks.get("_default") or []
        if not pool:
            return None
        recent = self._recent.get(key, [])
        candidates = [line for line in pool if line not in recent] or pool
        choice = self._rng.choice(candidates)
        # remember it so we don't immediately repeat
        history = (recent + [choice])[-self._memory :] if self._memory else []
        self._recent[key] = history
        return sanitize(choice)

    def line(self, key: str) -> str | None:
        """A structural response line (never tone-gated)."""
        return self._pick(key)

    def flavour(self, key: str, *, tone: str | None = None) -> str | None:
        """An optional garnish line; returns ``None`` when the tone says so."""
        prob = _TONE_PROB.get(tone or self._tone, 0.5)
        if prob <= 0:
            return None
        if prob < 1.0 and self._rng.random() > prob:
            return None
        return self._pick(key)
