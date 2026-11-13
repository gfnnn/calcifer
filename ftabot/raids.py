"""Raid-tier knowledge: which raids exist, which are current, how to label them.

Pure (no I/O), like ``metrics.py``. Raider.IO keys every progression payload by
raid *slug* and gives no hint which raid is the current one: the order of the
``raid_progression`` dict is not chronological (a one-boss side raid can come
first, and an ended tier can come before the live one). Its static-data
endpoint is the missing piece: the raid's display name, its boss count and the
dates it opens and closes per region. ``parse_catalog`` turns that payload into
``RaidInfo`` rows, and the helpers below answer the two questions every surface
asks: "which raid is the headline right now?" and "what is it called?".

Every helper degrades to the old behaviour when no catalog is available (the
endpoint is down, or a test runs offline), so a missing catalog can only cost
accuracy, never a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Difficulties in the order a guild meets them, with the letter Raider.IO and
# the rest of the bot use for each.
DIFFICULTIES: tuple[tuple[str, str], ...] = (
    ("normal", "N"),
    ("heroic", "H"),
    ("mythic", "M"),
)


@dataclass(frozen=True)
class RaidInfo:
    """One raid from Raider.IO's static data, narrowed to a single region."""

    slug: str
    name: str
    short_name: str | None = None
    total_bosses: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        """Open and not yet retired at ``now`` (unknown dates count as open)."""
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now >= self.ends_at:
            return False
        return True


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_catalog(data: Any, region: str) -> list[RaidInfo]:
    """Turn a Raider.IO ``/raiding/static-data`` payload into ``RaidInfo`` rows.

    Defensive throughout: anything that is not shaped like a raid is skipped,
    so API drift yields a shorter (or empty) catalog rather than an exception.
    """
    raids = data.get("raids") if isinstance(data, dict) else None
    if not isinstance(raids, list):
        return []
    region = region.lower()
    out: list[RaidInfo] = []
    for raw in raids:
        if not isinstance(raw, dict) or not raw.get("slug"):
            continue
        starts = raw.get("starts") if isinstance(raw.get("starts"), dict) else {}
        ends = raw.get("ends") if isinstance(raw.get("ends"), dict) else {}
        encounters = raw.get("encounters")
        out.append(
            RaidInfo(
                slug=str(raw["slug"]),
                name=str(raw.get("name") or slug_title(str(raw["slug"]))),
                short_name=raw.get("short_name") or None,
                total_bosses=len(encounters) if isinstance(encounters, list) else 0,
                starts_at=_parse_dt(starts.get(region)),
                ends_at=_parse_dt(ends.get(region)),
            )
        )
    return out


def slug_title(slug: str) -> str:
    """The fallback label for a raid we have no catalog entry for."""
    return slug.replace("-", " ").title()


def raid_name(slug: str, catalog: list[RaidInfo] | None) -> str:
    """The raid's display name: the catalog's, else a title-cased slug."""
    for raid in catalog or ():
        if raid.slug == slug:
            return raid.name
    return slug_title(slug)


def current_raids(
    catalog: list[RaidInfo] | None, now: datetime | None = None
) -> list[RaidInfo]:
    """The raids open right now, headline first.

    "Headline" means the raid with the most bosses (the tier's main raid, ahead
    of its one-boss side raids), newest first on a tie.
    """
    now = now or datetime.now(timezone.utc)
    active = [r for r in catalog or () if r.is_active(now)]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        active,
        key=lambda r: (r.total_bosses, r.starts_at or epoch),
        reverse=True,
    )


def headline_slug(
    progression: dict[str, Any],
    catalog: list[RaidInfo] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Which raid in a ``raid_progression`` payload to treat as "current".

    With a catalog: the biggest raid that is open now and present in the
    payload. Without one (or when none of the open raids appear): the first
    key, which is what Raider.IO's ordering used to mean.
    """
    if not progression:
        return None
    for raid in current_raids(catalog, now):
        if isinstance(progression.get(raid.slug), dict):
            return raid.slug
    return next(
        (slug for slug, raid in progression.items() if isinstance(raid, dict)), None
    )


def order_progression(
    progression: dict[str, Any],
    catalog: list[RaidInfo] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The payload re-ordered with the open raids first (headline leading).

    Raids the catalog does not list as open keep their original relative order
    after them, so a display that walks the dict shows the live tier on top.
    """
    lead = [r.slug for r in current_raids(catalog, now) if r.slug in progression]
    rest = [slug for slug in progression if slug not in lead]
    return {slug: progression[slug] for slug in (*lead, *rest)}


def kills(raid: dict[str, Any], difficulty: str) -> int:
    """Bosses down on a difficulty, from one raid's progression dict."""
    try:
        return int(raid.get(f"{difficulty}_bosses_killed") or 0)
    except (TypeError, ValueError):
        return 0


def summarize(
    progression: dict[str, Any],
    catalog: list[RaidInfo] | None = None,
    now: datetime | None = None,
) -> str | None:
    """A compact one-line label for the current raid, e.g. ``"Nerub-ar Palace:
    8/8 N, 6/8 H"``. ``None`` when there is no progression to describe."""
    slug = headline_slug(progression, catalog, now)
    if slug is None:
        return None
    raid = progression[slug]
    title = raid_name(slug, catalog)
    if raid.get("summary"):
        return f"{title}: {raid['summary']}"
    total = raid.get("total_bosses", 0)
    parts = [f"{kills(raid, key)}/{total} {letter}" for key, letter in DIFFICULTIES]
    return f"{title}: {', '.join(parts)}"


def best_difficulty(raid: dict[str, Any]) -> tuple[str, str] | None:
    """The hardest difficulty with at least one kill, as ``(key, letter)``."""
    for key, letter in reversed(DIFFICULTIES):
        if kills(raid, key) > 0:
            return key, letter
    return None


def guild_raid_rows(
    overview: dict[str, Any],
    catalog: list[RaidInfo] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Flatten a guild's progression + standings into ``guild_raids`` rows.

    ``overview`` is ``{"raid_progression": {...}, "raid_rankings": {...}}`` as
    Raider.IO returns them. Rows come back in display order: the open raids
    first (headline leading), then the rest. Without a catalog every raid is
    treated as current, since there is nothing to say otherwise.
    """
    progression = overview.get("raid_progression")
    if not isinstance(progression, dict):
        return []
    rankings = overview.get("raid_rankings")
    rankings = rankings if isinstance(rankings, dict) else {}
    open_slugs = {r.slug for r in current_raids(catalog, now)}
    rows: list[dict[str, Any]] = []
    for slug, raid in order_progression(progression, catalog, now).items():
        if not isinstance(raid, dict):
            continue
        ranks = rankings.get(slug)
        rows.append(
            {
                "raid_slug": slug,
                "name": raid_name(slug, catalog),
                "total_bosses": int(raid.get("total_bosses") or 0),
                "normal_killed": kills(raid, "normal"),
                "heroic_killed": kills(raid, "heroic"),
                "mythic_killed": kills(raid, "mythic"),
                "summary": raid.get("summary"),
                "ranks": ranks if isinstance(ranks, dict) else {},
                "is_current": slug in open_slugs if catalog else True,
            }
        )
    return rows
