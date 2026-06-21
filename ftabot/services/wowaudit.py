"""WoW Audit API client — the primary source of truth when a key is set.

WoW Audit (https://wowaudit.com) already aggregates Blizzard, Warcraft Logs,
Raider.IO and Droptimizer for a team, so one ``/characters`` call gives a rich,
pre-joined view of the roster. Access uses a per-team API key (a paid feature)
sent in the ``Authorization`` header.

Docs: https://wowaudit.com/api-docs

Because the exact JSON keys can shift between WoW Audit releases / subscription
tiers, the mapper here is deliberately defensive: it tries several candidate
keys for each field and always stashes the full raw object in ``Member.details``
so nothing is lost even if a key we don't yet map appears.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

import aiohttp

from ..models import Member, WishlistItem, normalize_realm
from ._http import BaseHTTPClient

DEFAULT_BASE_URL = "https://wowaudit.com/v1"


class WowAuditError(RuntimeError):
    pass


def _first(data: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value among ``keys``."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _count_like(value: Any) -> int | None:
    """Interpret a "missing X" field whether it's a count, list or flag."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return _as_int(value)


def _as_iso(value: Any) -> str | None:
    """Normalise a timestamp (ISO string or epoch seconds) to an ISO string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


class WowAuditClient(BaseHTTPClient):
    """Async WoW Audit client. Use as an async context manager."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        default_region: str = "eu",
        default_realm: str = "hellfire",
        session: aiohttp.ClientSession | None = None,
    ):
        super().__init__(session=session)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_region = default_region.lower()
        self.default_realm = default_realm

    def _session_headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key, "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.session.get(url, params=params) as resp:
            if resp.status == 401:
                raise WowAuditError("unauthorized — check WOWAUDIT_API_KEY")
            if resp.status == 404:
                raise WowAuditError(f"not found: {path}")
            if resp.status != 200:
                body = await resp.text()
                raise WowAuditError(f"HTTP {resp.status}: {body[:200]}")
            return await resp.json()

    # -- endpoints -----------------------------------------------------------

    async def team(self) -> dict[str, Any]:
        data = await self._get("team")
        return data if isinstance(data, dict) else {}

    async def raw_characters(self) -> list[dict[str, Any]]:
        """Return the raw character objects exactly as WoW Audit sends them."""
        data = await self._get("characters")
        # Accept either a bare list or an envelope like {"characters": [...]}.
        if isinstance(data, dict):
            for key in ("characters", "data", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    async def characters(self) -> list[Member]:
        """Return the tracked roster mapped onto Member objects."""
        return [self.to_member(raw) for raw in await self.raw_characters()]

    # -- mapping -------------------------------------------------------------

    def to_member(self, raw: dict[str, Any]) -> Member:
        """Map one WoW Audit character object onto a Member (defensively)."""
        name = _first(raw, "name", "character_name") or "Unknown"

        realm_value = _first(raw, "realm", "realm_slug", "realm_name")
        if isinstance(realm_value, dict):
            realm_value = _first(realm_value, "slug", "name")
        realm_slug = normalize_realm(str(realm_value)) if realm_value else self.default_realm

        region = (_first(raw, "region", "realm_region") or self.default_region)
        region = str(region).lower()

        class_value = _first(raw, "class", "wow_class", "character_class", "class_name")
        if isinstance(class_value, dict):
            class_value = _first(class_value, "name")

        spec = _first(raw, "spec", "current_spec_name", "active_spec", "spec_name")
        role = _first(raw, "role", "current_role")
        if isinstance(role, str):
            role = role.upper() or None

        # raid_progression maps to a TEXT column, so only keep it if it's a
        # plain string; structured progress stays available under details.
        progression = _first(raw, "raid_progression", "progress")
        if not isinstance(progression, str):
            progression = None

        member = Member(
            character_name=str(name),
            realm_slug=realm_slug,
            region=region,
            class_name=str(class_value) if class_value else None,
            active_spec=str(spec) if spec else None,
            role=role,
            race=_first(raw, "race", "race_name"),
            guild_rank=_as_int(_first(raw, "rank", "guild_rank")),
            blizzard_id=_as_int(_first(raw, "blizzard_id", "id", "character_id")),
            item_level=_as_float(
                _first(raw, "equipped_item_level", "item_level", "ilvl")
            ),
            average_item_level=_as_float(
                _first(raw, "average_item_level", "average_ilvl", "avg_item_level")
            ),
            mythic_plus_score=_as_float(
                _first(raw, "mythic_plus_score", "rio_score", "m_plus_score")
            ),
            best_parse_avg=_as_float(
                _first(raw, "best_parse_average", "best_parse", "parse_average")
            ),
            raid_progression=progression,
            last_login=_as_iso(
                _first(raw, "last_login", "last_login_at", "last_seen", "last_seen_at")
            ),
            missing_enchants=_count_like(
                _first(raw, "missing_enchants", "enchants_missing")
            ),
            missing_gems=_count_like(_first(raw, "missing_gems", "gems_missing")),
            vault_slots_filled=_as_int(
                _first(raw, "vault_slots_filled", "vault_slots", "great_vault_slots")
            ),
            profile_url=_first(raw, "profile_url", "url"),
            thumbnail_url=_first(raw, "thumbnail_url", "avatar_url", "media_avatar"),
            source="wowaudit",
            details=_curate_details(raw),
        )
        return member

    # -- team / vault / wishlists --------------------------------------------

    async def team_name(self) -> str | None:
        """The WoW Audit team name (used to tag raiders' raid_team)."""
        data = await self._get("team")
        return data.get("name") if isinstance(data, dict) else None

    async def vault_slots(self) -> dict[str, int]:
        """Map lower(character name) -> Great Vault slots filled (0..9).

        Source: /historical_data, where data.vault_options.{raids,dungeons,world}
        each hold option_1..3 (a non-null value means that slot is unlocked).
        """
        data = await self._get("historical_data")
        out: dict[str, int] = {}
        for c in _as_list(data, "characters"):
            name = _first(c, "name")
            if not name:
                continue
            vo = (c.get("data") or {}).get("vault_options") or {}
            filled = 0
            for group in ("raids", "dungeons", "world"):
                opts = vo.get(group)
                if isinstance(opts, dict):
                    filled += sum(1 for v in opts.values() if v is not None)
            out[str(name).lower()] = filled
        return out

    async def wishlists(self) -> list[WishlistItem]:
        """Parse the nested /wishlists response into flat WishlistItem rows."""
        data = await self._get("wishlists")
        return parse_wishlists(data, self.default_realm, self.default_region)

    async def recent_attendance(self, *, limit: int = 8) -> list[dict]:
        """Per-player attendance for the most recent past raids.

        Each item: {id, date, instance, difficulty, signups: [(name, status)]}.
        Raids dated in the future are skipped (WoW Audit keeps status "Planned"
        on past raids too, so we filter by date, not status). One /raids/{id}
        call per included raid, so we cap with ``limit``.
        """
        data = await self._get("raids")
        raids = data.get("raids") if isinstance(data, dict) else None
        today = datetime.now(timezone.utc).date().isoformat()
        past = select_recent_raids(raids or [], today, limit)

        out: list[dict] = []
        for raid in past:
            rid = raid.get("id")
            if rid is None:
                continue
            try:
                detail = await self._get(f"raids/{rid}")
            except WowAuditError:
                continue
            out.append(
                {
                    "id": rid,
                    "date": raid.get("date"),
                    "instance": raid.get("instance"),
                    "difficulty": raid.get("difficulty"),
                    "signups": parse_signups(detail),
                }
            )
        return out

    async def upcoming_raids(
        self, *, within_hours: float = 6.0, tz: tzinfo = timezone.utc, now: datetime | None = None
    ) -> list[dict]:
        """Raids starting within ``within_hours`` (for raid-night reminders).

        Each item: {date, time, start, instance, difficulty, present, total}.
        ``tz`` is the team's timezone, used to read WoW Audit's wall-clock times
        onto UTC (DST-correct when it is an IANA ``ZoneInfo``).
        """
        data = await self._get("raids")
        raids = data.get("raids") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        now = now or datetime.now(timezone.utc)
        due = select_upcoming_raids(raids or [], now, within_hours, tz)
        out: list[dict] = []
        for raid, start in due:
            out.append(
                {
                    "date": raid.get("date"),
                    "time": _first(raid, "start_time", "time"),
                    "start": start.isoformat(),
                    "instance": _first(raid, "instance", "title", "name"),
                    "difficulty": _first(raid, "difficulty"),
                    "present": _as_int(_first(raid, "present_size", "present", "signups_count")),
                    "total": _as_int(_first(raid, "total_size", "total", "roster_size")),
                }
            )
        return out

    async def upcoming_raid_signups(
        self, *, tz: tzinfo = timezone.utc, now: datetime | None = None
    ) -> dict | None:
        """The next scheduled raid plus its per-character sign-up statuses.

        Returns ``{date, time, start, instance, difficulty, signups: [(name,
        status)]}`` for the soonest raid starting at/after ``now`` (one
        ``/raids/{id}`` call), or ``None`` when nothing is scheduled. This backs
        the team-chemistry roster view (confirmed / benched / absent groupings).
        """
        data = await self._get("raids")
        raids = data.get("raids") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        now = now or datetime.now(timezone.utc)
        chosen = select_next_raid(raids or [], now, tz)
        if chosen is None:
            return None
        raid, start = chosen
        rid = raid.get("id")
        signups: list[tuple[str, str]] = []
        if rid is not None:
            try:
                detail = await self._get(f"raids/{rid}")
                signups = parse_signups(detail)
            except WowAuditError:
                signups = []
        return {
            "date": raid.get("date"),
            "time": _first(raid, "start_time", "time"),
            "start": start.isoformat(),
            "instance": _first(raid, "instance", "title", "name"),
            "difficulty": _first(raid, "difficulty"),
            "signups": signups,
        }


# Nested structures we surface in the browser/Discord, if WoW Audit provides
# them. Anything not listed is still kept under details["raw"].
_DETAIL_KEYS = (
    "gear",
    "items",
    "enchants",
    "gems",
    "missing_enchants",
    "missing_gems",
    "vault",
    "great_vault",
    "weekly_vault",
    "wishlist",
    "wishlists",
    "best_in_slot",
    "parses",
    "parse",
    "renown",
    "reputation",
    "professions",
    "track",
    "note",
)


def _curate_details(raw: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {
        key: raw[key] for key in _DETAIL_KEYS if key in raw and raw[key] is not None
    }
    # Always keep the full payload so we never silently drop data.
    details["raw"] = raw
    return details


def _as_list(data: Any, *envelope_keys: str) -> list[dict[str, Any]]:
    """Coerce a response into a list of dicts, unwrapping common envelopes."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in envelope_keys:
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
    return []


def _iter_encounter_items(wishlist: Any):
    """Yield (boss_name, item_dict) from a WoW Audit difficulty wishlist.

    The wishlist holds one or more lists of encounters; each encounter has a
    ``name`` and an ``items`` list. We scan generically so we don't depend on
    the exact key holding the encounter list.
    """
    if not isinstance(wishlist, dict):
        return
    for value in wishlist.values():
        if not isinstance(value, list):
            continue
        for encounter in value:
            if isinstance(encounter, dict) and isinstance(encounter.get("items"), list):
                boss = encounter.get("name")
                for item in encounter["items"]:
                    if isinstance(item, dict):
                        yield boss, item


# WoW Audit reports the upgrade as either a numeric Droptimizer percentage or,
# when no sim numbers exist, an ordinal word grade. Map the words onto a 0-100
# scale so the wishlist/loot views can sort by upgrade either way.
_GRADE_SCALE = {"huge": 100.0, "big": 60.0, "small": 30.0, "tiny": 10.0}


def _grade_value(value: Any) -> float | None:
    """Coerce a numeric percentage or a word grade ("huge"/"big"/…) to a float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _GRADE_SCALE.get(value.strip().lower())
    return None


def _wishlist_upgrade(item: dict[str, Any]) -> float | None:
    """Best upgrade value for an item (score_by_spec / wishes), if any.

    Prefers a real numeric percentage, then falls back to the word grade so an
    item still gets a sortable priority when WoW Audit ships grades not numbers.
    """
    for sc in (item.get("score_by_spec") or {}).values():
        if isinstance(sc, dict):
            val = _grade_value(sc.get("percentage"))
            if val is None:
                val = _grade_value(sc.get("score"))
            if val is not None:
                return val
    for wish in item.get("wishes") or []:
        if isinstance(wish, dict):
            val = _grade_value(wish.get("percentage"))
            if val is None:
                val = _grade_value(wish.get("upgrade"))
            if val is not None:
                return val
    return None


def _wishlist_is_bis(item: dict[str, Any]) -> bool:
    """Treat a top-bucket ("huge"/"bis") score as a best-in-slot flag."""
    for sc in (item.get("score_by_spec") or {}).values():
        if isinstance(sc, dict) and str(sc.get("score", "")).lower() in ("huge", "bis"):
            return True
    return False


def select_recent_raids(raids: list, today: str, limit: int) -> list[dict]:
    """The most relevant raids to ingest sign-ups/attendance for.

    Prefers raids on/before ``today`` (real attendance, newest first). If the
    team only has upcoming raids scheduled (WoW Audit's /raids is
    forward-looking), fall back to the soonest upcoming ones so we still capture
    current raid sign-ups.
    """
    valid = [r for r in raids if isinstance(r, dict) and r.get("date")]
    past = sorted(
        (r for r in valid if str(r["date"])[:10] <= today),
        key=lambda r: str(r["date"])[:10],
        reverse=True,
    )
    if past:
        return past[:limit]
    upcoming = sorted(
        (r for r in valid if str(r["date"])[:10] > today), key=lambda r: str(r["date"])[:10]
    )
    return upcoming[:limit]


def _norm_time(value: Any) -> str:
    """Normalise a time-of-day to HH:MM:SS (defaulting to midnight)."""
    if not value:
        return "00:00:00"
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 2:
        return f"{text}:00"
    return text


def raid_start_dt(raid: dict, tz: tzinfo = timezone.utc) -> datetime | None:
    """Combine a raid's date + time into a UTC datetime, or None.

    WoW Audit reports times in the team's local timezone; ``tz`` is that zone.
    The naive wall-clock time is interpreted *in* ``tz`` and converted to UTC,
    so an IANA ``ZoneInfo`` (e.g. Europe/Paris) tracks DST correctly across the
    CET<->CEST switch rather than drifting an hour for half the year. See
    ``Config.raid_timezone`` / ``RAID_TZ`` in .env.example.
    """
    date = raid.get("date")
    if not date:
        return None
    time = _first(raid, "start_time", "time")
    try:
        naive = datetime.fromisoformat(f"{str(date)[:10]}T{_norm_time(time)}")
    except ValueError:
        return None
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def select_upcoming_raids(
    raids: list, now: datetime, within_hours: float, tz: tzinfo = timezone.utc
) -> list[tuple[dict, datetime]]:
    """Raids starting in (now, now + within_hours], soonest first."""
    horizon = now + timedelta(hours=within_hours)
    out: list[tuple[dict, datetime]] = []
    for raid in raids:
        if not isinstance(raid, dict):
            continue
        start = raid_start_dt(raid, tz)
        if start is not None and now <= start <= horizon:
            out.append((raid, start))
    out.sort(key=lambda pair: pair[1])
    return out


def select_next_raid(
    raids: list, now: datetime, tz: tzinfo = timezone.utc
) -> tuple[dict, datetime] | None:
    """The soonest raid starting at/after ``now`` (with its UTC start), or None.

    Unlike :func:`select_upcoming_raids` (which bounds by a reminder window),
    this looks ahead with no horizon so the roster always reflects the genuine
    next raid night, however far out it is scheduled.
    """
    candidates: list[tuple[dict, datetime]] = []
    for raid in raids:
        if not isinstance(raid, dict):
            continue
        start = raid_start_dt(raid, tz)
        if start is not None and start >= now:
            candidates.append((raid, start))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[1])


def parse_signups(detail: Any) -> list[tuple[str, str]]:
    """Extract (character_name, status) pairs from a /raids/{id} response."""
    out: list[tuple[str, str]] = []
    if not isinstance(detail, dict):
        return out
    for signup in detail.get("signups") or []:
        if not isinstance(signup, dict):
            continue
        char = signup.get("character") or {}
        name = char.get("name")
        if name:
            out.append((str(name), str(signup.get("status") or "").lower()))
    return out


def parse_wishlists(data: Any, realm: str, region: str) -> list[WishlistItem]:
    """Flatten /wishlists (characters -> instances -> difficulties) into rows."""
    items: list[WishlistItem] = []
    for char in _as_list(data, "characters"):
        name = _first(char, "name") or "Unknown"
        crealm = _first(char, "realm")
        realm_slug = normalize_realm(str(crealm)) if crealm else realm
        for instance in char.get("instances") or []:
            if not isinstance(instance, dict):
                continue
            for diff in instance.get("difficulties") or []:
                if not isinstance(diff, dict):
                    continue
                difficulty = diff.get("difficulty")
                for boss, item in _iter_encounter_items(diff.get("wishlist")):
                    items.append(
                        WishlistItem(
                            character_name=str(name),
                            realm_slug=realm_slug,
                            region=region,
                            item_id=_as_int(item.get("id")),
                            item_name=item.get("name"),
                            slot=item.get("slot"),
                            boss=boss,
                            difficulty=difficulty,
                            upgrade_pct=_wishlist_upgrade(item),
                            is_bis=_wishlist_is_bis(item),
                            obtained=False,
                            source="wowaudit",
                            raw=item,
                        )
                    )
    return items
