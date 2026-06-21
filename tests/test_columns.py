"""The members column manifest — ownership by construction.

These lock the single source of truth (``MEMBER_COLUMNS``) against drift: the
derived sync/manual/migration partitions, the DDL, and the Member dataclass must
all stay in agreement, so a new column has to be classified in exactly one place
and a misclassification fails here instead of as a silent data bug.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from ftabot.db_schema import (
    MANUAL_COLUMNS,
    MEMBER_COLUMNS,
    MIGRATION_COLUMNS,
    SCHEMA,
    SYNC_COLUMNS,
    ColumnOwner,
)
from ftabot.models import Member


def _members_ddl_columns(schema: str) -> list[str]:
    """The column names declared in the ``members`` CREATE TABLE, in order."""
    body = re.search(r"CREATE TABLE IF NOT EXISTS members \((.*?)\n\);", schema, re.S)
    assert body, "members table not found in SCHEMA"
    constraints = {"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT"}
    columns: list[str] = []
    for line in body.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        first = line.split()[0]
        if first.upper() in constraints:
            continue
        columns.append(first)
    return columns


# --- derived partitions are exactly the historical hand-written values ------


def test_sync_columns_derived_exactly():
    assert SYNC_COLUMNS == (
        "class_name", "active_spec", "role", "race", "level", "faction",
        "guild_rank", "item_level", "average_item_level", "mythic_plus_score",
        "raid_progression", "best_parse_avg", "last_login", "missing_enchants",
        "missing_gems", "vault_slots_filled", "wishlist_pct", "thumbnail_url",
        "profile_url", "source", "blizzard_id", "wowaudit_team",
    )


def test_manual_columns_derived_exactly():
    assert set(MANUAL_COLUMNS) == {
        "discord_id", "is_main", "main_character_id", "notes", "raid_team",
        "officer_role",
    }


def test_migration_columns_derived_exactly():
    assert MIGRATION_COLUMNS == {
        "average_item_level": "REAL", "source": "TEXT", "blizzard_id": "INTEGER",
        "details_json": "TEXT", "last_login": "TEXT", "missing_enchants": "INTEGER",
        "missing_gems": "INTEGER", "vault_slots_filled": "INTEGER",
        "wishlist_pct": "REAL", "raid_team": "TEXT", "officer_role": "TEXT",
        "wowaudit_team": "TEXT",
    }


# --- structural invariants --------------------------------------------------


def test_sync_and_manual_columns_are_disjoint():
    # The central invariant, made explicit: nothing is both API- and human-owned.
    assert not (set(SYNC_COLUMNS) & set(MANUAL_COLUMNS))


def test_every_member_field_is_classified():
    # A new Member attribute with no manifest entry (or vice versa) fails here,
    # rather than silently never syncing / never persisting.
    fields = {f.name for f in dataclasses.fields(Member)}
    mapped = {c.member_attr for c in MEMBER_COLUMNS}
    assert fields == mapped, f"unclassified or orphaned: {fields ^ mapped}"


def test_manifest_matches_the_members_ddl():
    # The manifest and the CREATE TABLE can't drift apart.
    assert _members_ddl_columns(SCHEMA) == [c.name for c in MEMBER_COLUMNS]


def test_details_json_is_sync_owned_but_outside_the_upsert_loop():
    details = next(c for c in MEMBER_COLUMNS if c.name == "details_json")
    assert details.owner is ColumnOwner.SYNC
    assert details.in_sync_upsert is False  # (de)serialised separately
    assert details.member_attr == "details"


# --- the runtime guard ------------------------------------------------------


async def test_write_manual_rejects_a_sync_owned_column(db):
    mid = await db.upsert_synced(Member("Guarded"))
    with pytest.raises(ValueError):
        await db._write_manual(mid, item_level=600.0)  # SYNC-owned, not manual


async def test_write_manual_accepts_manual_columns(db):
    mid = await db.upsert_synced(Member("Guarded"))
    await db._write_manual(mid, notes="ok", raid_team="Cinder")
    m = await db.get_member("Guarded", "hellfire", "eu")
    assert m.notes == "ok"
    assert m.raid_team == "Cinder"
