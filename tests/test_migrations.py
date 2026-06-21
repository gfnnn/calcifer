"""The versioned, forward-only migration runner."""

from __future__ import annotations

import aiosqlite

from ftabot.db import Database
from ftabot.db_schema import MIGRATION_COLUMNS
from ftabot.migrations import MIGRATIONS, run_migrations
from ftabot.models import Member


def test_migration_versions_are_unique_and_sorted():
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


async def test_connect_records_every_migration(db):
    cur = await db.conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    recorded = [r["version"] for r in await cur.fetchall()]
    assert recorded == [m.version for m in MIGRATIONS]


async def test_a_fresh_db_already_has_every_migrated_column(db):
    async def cols(table):
        cur = await db.conn.execute(f"PRAGMA table_info({table})")
        return {r["name"] for r in await cur.fetchall()}

    members = await cols("members")
    assert set(MIGRATION_COLUMNS).issubset(members)
    boards = await cols("boards")
    assert {"promoted", "posted_channel_id"}.issubset(boards)
    outbox = await cols("feed_outbox")
    assert {"claimed_at", "attempts"}.issubset(outbox)


async def test_migrations_are_idempotent(db):
    # The fixture already ran them once; a second pass applies nothing and the
    # ledger stays intact (no duplicate-column error, no duplicate rows).
    applied_again = await run_migrations(db.conn, now="2026-01-01T00:00:00+00:00")
    assert applied_again == []
    cur = await db.conn.execute("SELECT count(*) AS c FROM schema_migrations")
    assert (await cur.fetchone())["c"] == len(MIGRATIONS)


async def test_old_db_is_upgraded_and_backfilled(tmp_path):
    path = str(tmp_path / "old.db")
    # Simulate a pre-migration database: a boards table that predates the
    # promoted / posted_channel_id columns, with one live message.
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE boards (id INTEGER PRIMARY KEY, board_type TEXT, "
            "channel_id TEXT, message_id TEXT)"
        )
        await conn.execute(
            "INSERT INTO boards (board_type, channel_id, message_id) "
            "VALUES ('hot20', '123', '456')"
        )
        await conn.commit()

    # Opening it runs SCHEMA (CREATE IF NOT EXISTS — leaves the old boards as-is)
    # then the migrations, which add the missing columns and backfill.
    async with Database(path) as db:
        cur = await db.conn.execute("PRAGMA table_info(boards)")
        cols = {r["name"] for r in await cur.fetchall()}
        assert {"promoted", "posted_channel_id"}.issubset(cols)

        cur = await db.conn.execute(
            "SELECT promoted, posted_channel_id FROM boards WHERE message_id = '456'"
        )
        row = await cur.fetchone()
        assert row["promoted"] == 0  # default
        assert row["posted_channel_id"] == "123"  # backfilled from channel_id

        cur = await db.conn.execute("SELECT version FROM schema_migrations")
        assert {r["version"] for r in await cur.fetchall()} == {
            m.version for m in MIGRATIONS
        }


async def test_old_members_db_without_wowaudit_team_upgrades_cleanly(tmp_path):
    # Regression for the prod startup crash. Two real-world facts must hold:
    #   1. SCHEMA runs before migrations, so it must NOT index a column that only
    #      a migration adds, or connect() dies with "no such column".
    #   2. _m1 runs once; an established prod DB already ran it before
    #      wowaudit_team existed, so the *column itself* must be added by the new
    #      migration, not relied upon from _m1.
    # Simulate that DB: the full schema minus wowaudit_team, with migrations 1-5
    # already recorded so _m1 is skipped (exactly as on prod).
    from ftabot.db_schema import SCHEMA

    old_schema = "\n".join(ln for ln in SCHEMA.splitlines() if "wowaudit_team" not in ln)
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(old_schema)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)"
        )
        for v in (1, 2, 3, 4, 5):
            await conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) "
                "VALUES (?, 'pre-existing', '2026-01-01T00:00:00+00:00')",
                (v,),
            )
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(members)")
        assert "wowaudit_team" not in {r["name"] for r in await cur.fetchall()}

    # Opening must not raise; migration 6 adds the column and its index even
    # though _m1 (version 1) is already recorded and won't re-run.
    async with Database(path) as db:
        cur = await db.conn.execute("PRAGMA table_info(members)")
        assert "wowaudit_team" in {r["name"] for r in await cur.fetchall()}
        cur = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_members_wa_team'"
        )
        assert await cur.fetchone() is not None  # index created by the migration
        # And the team filter works end to end through the new column.
        await db.upsert_synced(Member("New", wowaudit_team="Progression"))
        teamed = [m.character_name for m in await db.team_members("Progression")]
        assert teamed == ["New"]
