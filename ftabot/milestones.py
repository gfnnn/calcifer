"""Guild milestones: spot the moments worth a cheer in data we already hold.

No network and no API key: everything here reads what the sync has stored (the
roster's Mythic+ scores and levels, the guild's raid kill counts) and compares
it with the last value seen. Three kinds of milestone come out:

* ``raid_kill``: the guild's Heroic or Mythic kill count in a current raid
  went up (a new boss down, or the raid cleared);
* ``mplus``: a member's Mythic+ score crossed a round threshold (1,500,
  then every 500 above it);
* ``level_cap``: a member who was levelling reached the level cap.

Detection is *edge-triggered* against stored marks rather than read off the
current state, which is what keeps it honest: a member with no mark yet is
recorded silently, so somebody who joins the guild already at 3,000 is not
announced as having just got there, and the very first run announces nothing.

``detect`` is pure (and unit-tested directly); ``collect`` is the thin
DB-backed wrapper the milestones feed calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import MAX_LEVEL, Member

if TYPE_CHECKING:
    from .db import Database

# Mythic+ score thresholds: the first one worth announcing, then every STEP.
MPLUS_FLOOR = 1500
MPLUS_STEP = 500

# The entity that owns the guild-wide raid marks.
GUILD_ENTITY = "guild"
# Difficulties whose kills are announced (Normal would only be noise).
RAID_DIFFICULTIES: tuple[str, ...] = ("heroic", "mythic")

Marks = dict[tuple[str, str], float]


def mplus_tier(score: float | None) -> int:
    """The highest threshold a score has reached (0 below the floor)."""
    if not score or score < MPLUS_FLOOR:
        return 0
    return int(score // MPLUS_STEP) * MPLUS_STEP


def member_entity(member: Member) -> str:
    """The stable mark key for a character (name, realm, region)."""
    return f"{member.character_name}|{member.realm_slug}|{member.region}".lower()


def detect(
    marks: Marks, members: list[Member], raids: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], Marks]:
    """Compare the current state with ``marks``.

    Returns ``(milestones, current_marks)``: the milestones crossed since the
    marks were taken, and the marks to store for next time. ``raids`` is
    ``Database.guild_raids()`` rows.
    """
    found: list[dict[str, Any]] = []
    current: Marks = {}

    for member in members:
        entity = member_entity(member)

        if member.mythic_plus_score is not None:
            score = float(member.mythic_plus_score)
            current[(entity, "mplus")] = score
            previous = marks.get((entity, "mplus"))
            tier = mplus_tier(score)
            if previous is not None and tier > mplus_tier(previous):
                found.append(
                    {
                        "kind": "mplus",
                        "entity_key": entity,
                        "subject": member.character_name,
                        "value": tier,
                        "detail": {
                            "score": score,
                            "class_name": member.class_name,
                            "profile_url": member.profile_url,
                        },
                    }
                )

        if member.level:
            level = int(member.level)
            current[(entity, "level")] = level
            previous = marks.get((entity, "level"))
            if previous is not None and previous < MAX_LEVEL <= level:
                found.append(
                    {
                        "kind": "level_cap",
                        "entity_key": entity,
                        "subject": member.character_name,
                        "value": MAX_LEVEL,
                        "detail": {
                            "class_name": member.class_name,
                            "profile_url": member.profile_url,
                        },
                    }
                )

    # Raid marks. Once any guild mark exists the feed is primed, and a raid we
    # have never marked is a newly opened one: its kills count from zero, so
    # the first boss in a new raid is announced. Before that (the first run)
    # everything is recorded silently.
    primed = any(entity == GUILD_ENTITY for entity, _metric in marks)
    for raid in raids:
        if not raid.get("is_current"):
            continue
        for difficulty in RAID_DIFFICULTIES:
            killed = int(raid.get(f"{difficulty}_killed") or 0)
            metric = f"raid:{raid['raid_slug']}:{difficulty}"
            current[(GUILD_ENTITY, metric)] = killed
            previous = marks.get((GUILD_ENTITY, metric))
            if previous is None and primed:
                previous = 0
            if previous is not None and killed > previous:
                found.append(
                    {
                        "kind": "raid_kill",
                        "entity_key": f"{GUILD_ENTITY}|{metric}",
                        "subject": raid["name"],
                        "value": killed,
                        "detail": {
                            "raid_slug": raid["raid_slug"],
                            "difficulty": difficulty,
                            "total_bosses": int(raid.get("total_bosses") or 0),
                        },
                    }
                )

    return found, current


async def collect(db: "Database") -> int:
    """Detect new milestones from the stored data and persist them.

    Returns how many were found. Marks are advanced in the same pass, so a
    milestone is detected exactly once; it then lives in the ``milestones``
    table until the feed has posted it.
    """
    marks = await db.milestone_marks()
    found, current = detect(marks, await db.all_members(), await db.guild_raids())
    changed = {key: value for key, value in current.items() if marks.get(key) != value}
    await db.set_milestone_marks(changed)
    if found:
        await db.add_milestones(found)
    return len(found)
