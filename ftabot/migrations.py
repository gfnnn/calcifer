"""Forward-only schema migrations, run on every ``Database.connect``.

Older databases predate columns the current schema declares. Rather than a pile
of bespoke ``ALTER`` blocks, migrations are an ordered, recorded list: each has a
unique ``version`` and an idempotent ``apply`` step, and a ``schema_migrations``
table records which have run so the next connect skips them.

The steps stay *idempotent* (check-before-alter) on purpose: a database that
already had the old ad-hoc migration applied has no ``schema_migrations`` row,
so on the first connect after upgrading, every migration re-runs once — and must
be a safe no-op when its column already exists. SQLite can't drop/alter columns,
so migrations only ever add columns or backfill data, never remove.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiosqlite

from .db_schema import MIGRATION_COLUMNS


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[aiosqlite.Connection], Awaitable[None]]


async def _column_exists(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in await cur.fetchall())


async def _add_column_if_missing(
    conn: aiosqlite.Connection, table: str, column: str, decl: str
) -> None:
    if not await _column_exists(conn, table, column):
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def _m1_member_columns(conn: aiosqlite.Connection) -> None:
    """The forward-only API columns added to ``members`` over time."""
    for column, sql_type in MIGRATION_COLUMNS.items():
        await _add_column_if_missing(conn, "members", column, sql_type)


async def _m2_boards_promoted(conn: aiosqlite.Connection) -> None:
    """The posting-guardrail ``promoted`` flag on boards."""
    await _add_column_if_missing(
        conn, "boards", "promoted", "INTEGER NOT NULL DEFAULT 0"
    )


async def _m3_boards_posted_channel(conn: aiosqlite.Connection) -> None:
    """``posted_channel_id`` so a POSTING_MODE flip re-posts into the right channel.

    Backfill existing live messages to their stored ``channel_id`` (the steady
    state for a promoted board) so the upgrade doesn't needlessly re-post a board
    already sitting in its real channel.
    """
    if not await _column_exists(conn, "boards", "posted_channel_id"):
        await conn.execute("ALTER TABLE boards ADD COLUMN posted_channel_id TEXT")
        await conn.execute(
            "UPDATE boards SET posted_channel_id = channel_id "
            "WHERE message_id IS NOT NULL"
        )


async def _m4_outbox_visibility(conn: aiosqlite.Connection) -> None:
    """The outbox visibility-timeout delivery columns."""
    await _add_column_if_missing(conn, "feed_outbox", "claimed_at", "TEXT")
    await _add_column_if_missing(
        conn, "feed_outbox", "attempts", "INTEGER NOT NULL DEFAULT 0"
    )


async def _m5_settings_table(conn: aiosqlite.Connection) -> None:
    """The runtime settings overlay the dashboard edits (posting mode/channels)."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        "key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT)"
    )


async def _m6_wowaudit_team(conn: aiosqlite.Connection) -> None:
    """Add the wowaudit_team column and its index to existing databases.

    ``_m1`` adds the ``migrate=True`` members columns, but it runs only once and
    on an established prod DB it already ran *before* wowaudit_team existed — so a
    new migrate-column needs its own migration to reach that DB. Add the column
    here (idempotent) and its index. The index lives here, not in SCHEMA, because
    SCHEMA runs before migrations: an index in SCHEMA on a not-yet-migrated column
    crashes startup with "no such column".
    """
    await _add_column_if_missing(conn, "members", "wowaudit_team", "TEXT")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_members_wa_team ON members (wowaudit_team)"
    )


# Ordered, append-only. Never renumber or reuse a version.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "members: forward-only API columns", _m1_member_columns),
    Migration(2, "boards: promoted flag", _m2_boards_promoted),
    Migration(3, "boards: posted_channel_id + backfill", _m3_boards_posted_channel),
    Migration(4, "feed_outbox: visibility-timeout columns", _m4_outbox_visibility),
    Migration(5, "settings: runtime override table", _m5_settings_table),
    Migration(6, "members: wowaudit_team column + index", _m6_wowaudit_team),
)


async def run_migrations(conn: aiosqlite.Connection, *, now: str) -> list[int]:
    """Apply every migration not yet recorded; return the versions applied now."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)"
    )
    cur = await conn.execute("SELECT version FROM schema_migrations")
    applied = {row["version"] for row in await cur.fetchall()}

    just_applied: list[int] = []
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        await migration.apply(conn)
        await conn.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) "
            "VALUES (?, ?, ?)",
            (migration.version, migration.description, now),
        )
        just_applied.append(migration.version)
    await conn.commit()
    return just_applied
