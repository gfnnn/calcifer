"""Domain types and WoW reference data shared across the project."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- WoW reference data -----------------------------------------------------

# Playable classes (The War Within / Midnight era).
CLASSES: tuple[str, ...] = (
    "Death Knight",
    "Demon Hunter",
    "Druid",
    "Evoker",
    "Hunter",
    "Mage",
    "Monk",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
)

# The three combat roles. Raider.IO reports specs as one of these.
ROLES: tuple[str, ...] = ("TANK", "HEALER", "DPS")

# Map the many ways a source spells a role onto our canonical TANK/HEALER/DPS.
# WoW Audit in particular uses "Heal"/"Melee"/"Ranged"; Raider.IO uses
# "tank"/"healer"/"dps". Anything unrecognised falls through to spec inference.
_ROLE_ALIASES: dict[str, str] = {
    "tank": "TANK",
    "healer": "HEALER",
    "heal": "HEALER",
    "heals": "HEALER",
    "healing": "HEALER",
    "dps": "DPS",
    "damage": "DPS",
    "melee": "DPS",
    "ranged": "DPS",
    "mdps": "DPS",
    "rdps": "DPS",
}

# Healer and tank specs are a small fixed set (everything else is DPS), so a
# missing/garbled role can be recovered from the spec name alone. Spec names are
# unambiguous across classes (e.g. "Restoration" is always a healer), so this
# needs only the spec, not the class.
_HEALER_SPECS = frozenset(
    {"discipline", "holy", "restoration", "mistweaver", "preservation"}
)
_TANK_SPECS = frozenset(
    {"blood", "vengeance", "guardian", "brewmaster", "protection"}
)


def role_from_spec(spec: str | None) -> str | None:
    """Infer TANK/HEALER/DPS from a spec name, or ``None`` when there's no spec."""
    if not spec:
        return None
    key = spec.strip().lower()
    if key in _HEALER_SPECS:
        return "HEALER"
    if key in _TANK_SPECS:
        return "TANK"
    return "DPS"


def effective_role(role: str | None, spec: str | None = None) -> str | None:
    """The canonical role for a character: a recognised ``role`` value wins,
    otherwise it's inferred from the spec. This recovers the role icon for
    members whose stored role is missing or in a source-specific spelling."""
    canon = _ROLE_ALIASES.get((role or "").strip().lower())
    return canon or role_from_spec(spec)

# The current character level cap (Midnight). A character below this is still
# levelling, so surfaces show their level; at the cap the level is hidden and
# the slot is reused for their Mythic+ score (a sub-cap character has none yet).
MAX_LEVEL: int = 90

# Class colours (hex) — handy for Discord embeds. Sourced from Blizzard's
# canonical class colour palette.
CLASS_COLORS: dict[str, int] = {
    "Death Knight": 0xC41E3A,
    "Demon Hunter": 0xA330C9,
    "Druid": 0xFF7C0A,
    "Evoker": 0x33937F,
    "Hunter": 0xAAD372,
    "Mage": 0x3FC7EB,
    "Monk": 0x00FF98,
    "Paladin": 0xF48CBA,
    "Priest": 0xFFFFFF,
    "Rogue": 0xFFF468,
    "Shaman": 0x0070DD,
    "Warlock": 0x8788EE,
    "Warrior": 0xC69B6D,
}

DEFAULT_EMBED_COLOR = 0xCC4422  # an ember/ash tone for "From the Ashes"


def class_color(class_name: str | None) -> int:
    """Return the Discord embed colour for a class, with a sane fallback."""
    if not class_name:
        return DEFAULT_EMBED_COLOR
    return CLASS_COLORS.get(class_name.title(), DEFAULT_EMBED_COLOR)


def normalize_realm(realm: str) -> str:
    """Turn a realm display name into Blizzard/Raider.IO's slug form.

    e.g. "Argent Dawn" -> "argent-dawn", "Hellfire" -> "hellfire".
    """
    return "-".join(realm.strip().lower().replace("'", "").split())


def split_character_key(value: str, default_realm: str = "hellfire") -> tuple[str, str]:
    """Split a ``"Name-Realm"`` token into ``(name, realm_slug)``.

    WoW character names never contain ``-`` or spaces, so the first hyphen
    separates the name from the realm (whose display form may itself contain
    spaces, e.g. ``"Emberling-Argent Dawn"``). A bare ``"Name"`` (same-realm
    shorthand GRM sometimes stores) falls back to ``default_realm``.
    """
    name, _, realm = value.strip().partition("-")
    realm = realm.strip()
    return name.strip(), (normalize_realm(realm) if realm else normalize_realm(default_realm))


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (used for *_at columns)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Member:
    """A single guild character.

    Mirrors the ``members`` table. ``None`` means "unknown / not yet synced".
    """

    character_name: str
    realm_slug: str = "hellfire"
    region: str = "eu"

    id: int | None = None
    class_name: str | None = None
    active_spec: str | None = None
    role: str | None = None
    race: str | None = None
    level: int | None = None
    faction: str | None = None

    guild_rank: int | None = None
    item_level: float | None = None  # equipped item level
    average_item_level: float | None = None
    mythic_plus_score: float | None = None
    raid_progression: str | None = None
    best_parse_avg: float | None = None

    # Engagement / preparedness signals (sync-managed).
    last_login: str | None = None
    missing_enchants: int | None = None
    missing_gems: int | None = None
    vault_slots_filled: int | None = None
    wishlist_pct: float | None = None

    thumbnail_url: str | None = None
    profile_url: str | None = None

    # The raid team tagged by the sync from WoW Audit (sync-owned). The manual
    # ``raid_team`` below overrides it for grouping; see ``effective_team``.
    wowaudit_team: str | None = None

    # Provenance + a flexible blob for rich nested data (gear, Great Vault,
    # wishlist, per-boss parses). Persisted as the ``details_json`` column.
    source: str | None = None
    blizzard_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    discord_id: str | None = None
    is_main: bool = True
    main_character_id: int | None = None
    notes: str | None = None

    # Human-managed organisation fields (never overwritten by a sync).
    raid_team: str | None = None
    officer_role: str | None = None

    last_synced: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def effective_team(self) -> str | None:
        """Team to group by: a manual ``raid_team`` wins, else the WoW Audit tag."""
        manual = (self.raid_team or "").strip()
        return manual or self.wowaudit_team

    @property
    def key(self) -> tuple[str, str, str]:
        """Unique identity of a character: (name, realm, region), lowercased."""
        return (self.character_name.lower(), self.realm_slug.lower(), self.region.lower())

    def display(self) -> str:
        """Human-friendly "Name-Realm" label."""
        return f"{self.character_name}-{self.realm_slug.title()}"

    def to_dict(self, *, include_details: bool = False) -> dict[str, Any]:
        """JSON-serialisable view of this member (for the web/API layer)."""
        data: dict[str, Any] = {
            "id": self.id,
            "character_name": self.character_name,
            "realm_slug": self.realm_slug,
            "region": self.region,
            "display": self.display(),
            "class": self.class_name,
            "spec": self.active_spec,
            "role": self.role,
            "race": self.race,
            "level": self.level,
            "faction": self.faction,
            "guild_rank": self.guild_rank,
            "item_level": self.item_level,
            "average_item_level": self.average_item_level,
            "mythic_plus_score": self.mythic_plus_score,
            "raid_progression": self.raid_progression,
            "best_parse_avg": self.best_parse_avg,
            "last_login": self.last_login,
            "missing_enchants": self.missing_enchants,
            "missing_gems": self.missing_gems,
            "vault_slots_filled": self.vault_slots_filled,
            "wishlist_pct": self.wishlist_pct,
            "thumbnail_url": self.thumbnail_url,
            "profile_url": self.profile_url,
            "source": self.source,
            "discord_id": self.discord_id,
            "is_main": self.is_main,
            "main_character_id": self.main_character_id,
            "raid_team": self.raid_team,
            "wowaudit_team": self.wowaudit_team,
            "effective_team": self.effective_team,
            "officer_role": self.officer_role,
            "notes": self.notes,
            "last_synced": self.last_synced,
        }
        if include_details:
            data["details"] = self.details
        return data


@dataclass
class LootEntry:
    """A single loot award, sourced from RCLootCouncil via WoW Audit."""

    character_name: str
    realm_slug: str = "hellfire"
    region: str = "eu"
    id: int | None = None
    item_id: int | None = None
    item_name: str | None = None
    item_level: float | None = None
    slot: str | None = None
    instance: str | None = None
    boss: str | None = None
    difficulty: str | None = None
    response: str | None = None  # BiS / Upgrade / Mainspec / Offspec / Transmog…
    votes: int | None = None
    awarded_by: str | None = None
    awarded_at: str | None = None
    source: str = "rclc"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, int | None, str | None]:
        """Natural key used to avoid importing the same award twice."""
        return (self.character_name.lower(), self.item_id, self.awarded_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "character_name": self.character_name,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "item_level": self.item_level,
            "slot": self.slot,
            "instance": self.instance,
            "boss": self.boss,
            "difficulty": self.difficulty,
            "response": self.response,
            "votes": self.votes,
            "awarded_by": self.awarded_by,
            "awarded_at": self.awarded_at,
            "source": self.source,
        }


@dataclass
class WishlistItem:
    """A Droptimizer/QELive wishlist entry for a character (BiS tracking)."""

    character_name: str
    realm_slug: str = "hellfire"
    region: str = "eu"
    id: int | None = None
    item_id: int | None = None
    item_name: str | None = None
    slot: str | None = None
    boss: str | None = None
    difficulty: str | None = None
    upgrade_pct: float | None = None  # Droptimizer DPS gain
    is_bis: bool = False
    obtained: bool = False
    source: str | None = None  # 'droptimizer' / 'qelive'
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, int | None, str | None]:
        return (self.character_name.lower(), self.item_id, self.difficulty)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "character_name": self.character_name,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "slot": self.slot,
            "boss": self.boss,
            "difficulty": self.difficulty,
            "upgrade_pct": self.upgrade_pct,
            "is_bis": self.is_bis,
            "obtained": self.obtained,
            "source": self.source,
        }


@dataclass
class IdentityRecord:
    """One character as GRM (officer-maintained identity) records it.

    This is the data-spine's *who is who* row: the alt<->main grouping, join
    date, rank and notes that no public API holds. ``main_name`` and ``alts``
    carry ``"Name-Realm"`` references to other characters in the same account;
    the identity grouping resolves them into one person.
    """

    character_name: str
    realm_slug: str = "hellfire"
    region: str = "eu"
    guid: str | None = None
    class_name: str | None = None
    rank_name: str | None = None
    join_date: str | None = None  # ISO date (YYYY-MM-DD)
    is_main: bool | None = None
    main_name: str | None = None  # "Name-Realm" of the declared main, if an alt
    alts: list[str] = field(default_factory=list)  # "Name-Realm" each
    note: str | None = None
    officer_note: str | None = None
    source: str = "grm"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        """Account-graph node id: ``(lower(name), realm_slug)``."""
        return (self.character_name.lower(), self.realm_slug)
