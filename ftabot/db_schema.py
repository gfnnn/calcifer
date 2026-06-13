"""Database DDL and column manifests for the SQLite layer.

Kept separate from db.py so the large schema string and the sync/migration
column lists are easy to scan and diff without scrolling past the query
methods. db.py imports these back under their original internal names.

The members table's column ownership — the codebase's central invariant that a
roster sync must never overwrite a human-entered field — is declared once, in
:data:`MEMBER_COLUMNS`. The ``SYNC_COLUMNS`` / ``MANUAL_COLUMNS`` /
``MIGRATION_COLUMNS`` partitions are *derived* from it, so a new column is
classified in exactly one place and a misclassification is caught by the guard
tests in ``tests/test_columns.py`` rather than by a silent data bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    character_name    TEXT    NOT NULL,
    realm_slug        TEXT    NOT NULL DEFAULT 'hellfire',
    region            TEXT    NOT NULL DEFAULT 'eu',

    class_name        TEXT,
    active_spec       TEXT,
    role              TEXT,
    race              TEXT,
    level             INTEGER,
    faction           TEXT,

    guild_rank        INTEGER,
    item_level        REAL,
    average_item_level REAL,
    mythic_plus_score REAL,
    raid_progression  TEXT,
    best_parse_avg    REAL,

    last_login         TEXT,
    missing_enchants   INTEGER,
    missing_gems       INTEGER,
    vault_slots_filled INTEGER,
    wishlist_pct       REAL,

    thumbnail_url     TEXT,
    profile_url       TEXT,
    source            TEXT,
    blizzard_id       INTEGER,
    details_json      TEXT,

    discord_id        TEXT,
    is_main           INTEGER NOT NULL DEFAULT 1,
    main_character_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    notes             TEXT,
    raid_team         TEXT,
    officer_role      TEXT,

    last_synced       TEXT,
    created_at        TEXT,
    updated_at        TEXT,

    UNIQUE (character_name, realm_slug, region)
);

CREATE INDEX IF NOT EXISTS idx_members_discord  ON members (discord_id);
CREATE INDEX IF NOT EXISTS idx_members_class     ON members (class_name);
CREATE INDEX IF NOT EXISTS idx_members_mainid    ON members (main_character_id);
CREATE INDEX IF NOT EXISTS idx_members_team       ON members (raid_team);

-- RCLootCouncil loot awards (via WoW Audit). De-duplicated on import.
CREATE TABLE IF NOT EXISTS loot_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    character_name TEXT NOT NULL,
    realm_slug     TEXT,
    region         TEXT,
    item_id        INTEGER,
    item_name      TEXT,
    item_level     REAL,
    slot           TEXT,
    instance       TEXT,
    boss           TEXT,
    difficulty     TEXT,
    response       TEXT,
    votes          INTEGER,
    awarded_by     TEXT,
    awarded_at     TEXT,
    source         TEXT,
    raw_json       TEXT,
    UNIQUE (character_name, item_id, awarded_at)
);
CREATE INDEX IF NOT EXISTS idx_loot_char ON loot_history (character_name);
CREATE INDEX IF NOT EXISTS idx_loot_when ON loot_history (awarded_at);

-- GRM identity ground truth: the officer-maintained who-is-who, ingested from
-- the desktop addon and overlaid onto members' alt<->main grouping.
CREATE TABLE IF NOT EXISTS identity_members (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    character_name TEXT NOT NULL,
    realm_slug     TEXT NOT NULL DEFAULT 'hellfire',
    region         TEXT NOT NULL DEFAULT 'eu',
    guid           TEXT,
    class_name     TEXT,
    rank_name      TEXT,
    join_date      TEXT,
    is_main        INTEGER,
    main_name      TEXT,
    alts_json      TEXT,
    note           TEXT,
    officer_note   TEXT,
    source         TEXT DEFAULT 'grm',
    updated_at     TEXT,
    UNIQUE (character_name, realm_slug, region)
);
CREATE INDEX IF NOT EXISTS idx_identity_main ON identity_members (main_name);

-- Droptimizer / QELive wishlist (BiS tracking) per character.
CREATE TABLE IF NOT EXISTS wishlist_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    character_name TEXT NOT NULL,
    realm_slug     TEXT,
    region         TEXT,
    item_id        INTEGER,
    item_name      TEXT,
    slot           TEXT,
    boss           TEXT,
    difficulty     TEXT,
    upgrade_pct    REAL,
    is_bis         INTEGER NOT NULL DEFAULT 0,
    obtained       INTEGER NOT NULL DEFAULT 0,
    source         TEXT,
    raw_json       TEXT,
    UNIQUE (character_name, item_id, difficulty)
);
CREATE INDEX IF NOT EXISTS idx_wishlist_char ON wishlist_items (character_name);

-- Raid nights and per-character attendance.
CREATE TABLE IF NOT EXISTS raid_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    instance    TEXT,
    difficulty  TEXT,
    source      TEXT,
    raw_json    TEXT,
    UNIQUE (started_at, instance, difficulty)
);
CREATE TABLE IF NOT EXISTS attendance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_event_id  INTEGER REFERENCES raid_events(id) ON DELETE CASCADE,
    character_name TEXT NOT NULL,
    realm_slug     TEXT,
    status         TEXT,
    UNIQUE (raid_event_id, character_name)
);
CREATE INDEX IF NOT EXISTS idx_attendance_char ON attendance (character_name);

-- Guild-wide daily snapshots for trend lines.
CREATE TABLE IF NOT EXISTS guild_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at            TEXT,
    member_count        INTEGER,
    active_count        INTEGER,
    avg_item_level      REAL,
    avg_mythic_plus     REAL,
    progression_summary TEXT,
    role_counts_json    TEXT,
    class_counts_json   TEXT,
    health_score        REAL
);

-- Integration hub: which feed items we've already posted (dedup across restarts).
CREATE TABLE IF NOT EXISTS seen_items (
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    first_seen_at TEXT,
    PRIMARY KEY (source, external_id)
);

-- Integration hub: routing rules (which feed posts where, in what style).
-- Editable live via the /feed officer commands; read by the router.
CREATE TABLE IF NOT EXISTS feed_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    event_type  TEXT,
    match_json  TEXT,
    channel_id  TEXT NOT NULL,
    style_json  TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_by  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_feed_rules_source ON feed_rules (source);

-- Integration hub: durable queue for push events (e.g. Raid-Helper webhooks)
-- so a web-received alert is posted by the bot even across a restart.
CREATE TABLE IF NOT EXISTS feed_outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    style_json TEXT,
    created_at TEXT,
    -- 0 = pending, 1 = claimed/in-flight (claimed_at set), 2 = delivered.
    posted     INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_feed_outbox_pending ON feed_outbox (posted);

-- Integration hub: sources an officer has temporarily silenced.
CREATE TABLE IF NOT EXISTS feed_mutes (
    source   TEXT PRIMARY KEY,
    muted_at TEXT
);

-- Living boards: bot-owned messages edited in place (one row per board).
-- message_id is null until first post; content_hash lets the loop skip an edit
-- when nothing changed (diff-before-edit); a vanished message self-heals.
-- channel_id is the *production* channel; an un-promoted board renders into
-- the posting guardrail's test channel until it is explicitly promoted.
-- posted_channel_id records where the live message actually went, so flipping
-- POSTING_MODE (test -> live) re-posts the board into the now-correct channel
-- instead of believing it is "unchanged" while the only copy sits in the sandbox.
CREATE TABLE IF NOT EXISTS boards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    board_type        TEXT NOT NULL,
    channel_id        TEXT NOT NULL,
    message_id        TEXT,
    posted_channel_id TEXT,
    config_json       TEXT,
    content_hash      TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    promoted          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT,
    updated_at        TEXT,
    UNIQUE (board_type, channel_id)
);

-- Per-board weekly baseline so a board can show rank movement (▲/▼) "since last
-- reset". Rolled forward roughly weekly by the board that owns it.
CREATE TABLE IF NOT EXISTS board_baselines (
    board_type  TEXT NOT NULL,
    entity_key  TEXT NOT NULL,
    rank        INTEGER,
    captured_at TEXT,
    PRIMARY KEY (board_type, entity_key)
);

-- Raider.IO M+ affixes per week per region.
CREATE TABLE IF NOT EXISTS mythic_affixes (
    week_key     TEXT NOT NULL,
    region       TEXT NOT NULL,
    affix_names  TEXT NOT NULL,
    details_json TEXT,
    recorded_at  TEXT DEFAULT (datetime('now', 'utc')),
    PRIMARY KEY (week_key, region)
);

-- Raid guild standings on the home realm (populated by the realm-progress feed).
CREATE TABLE IF NOT EXISTS realm_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_name    TEXT NOT NULL,
    realm_slug    TEXT NOT NULL,
    region        TEXT NOT NULL,
    raid_slug     TEXT NOT NULL,
    difficulty    TEXT NOT NULL DEFAULT 'mythic',
    mythic_kills  INTEGER NOT NULL DEFAULT 0,
    summary       TEXT,
    realm_rank    INTEGER,
    first_seen_at TEXT DEFAULT (datetime('now', 'utc')),
    updated_at    TEXT DEFAULT (datetime('now', 'utc')),
    UNIQUE (guild_name, realm_slug, region, raid_slug, difficulty)
);
CREATE INDEX IF NOT EXISTS idx_realm_progress_lookup
    ON realm_progress (realm_slug, region, raid_slug, difficulty);

-- News archive (populated by the Wowhead feed) so the News Highlights board can
-- render a live two-zone page from the DB without any network I/O of its own.
-- tier is 'high' for patch notes / blue posts / PTR (board highlight zone) and
-- 'low' for general filler (the latest-list zone).
CREATE TABLE IF NOT EXISTS news_items (
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    event_type   TEXT,
    title        TEXT NOT NULL,
    url          TEXT,
    summary      TEXT,
    tier         TEXT NOT NULL DEFAULT 'low',
    published_at TEXT,
    recorded_at  TEXT DEFAULT (datetime('now', 'utc')),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items (published_at);

-- Upcoming Raid-Helper events (upserted by the webhook receiver) so the Weekly
-- Events board can render the week at a glance from the DB. status flips to
-- 'cancelled' on a delete webhook; start_epoch is null when the payload had no
-- parseable time (such events are hidden until an update supplies one).
CREATE TABLE IF NOT EXISTS calendar_events (
    event_id     TEXT PRIMARY KEY,
    title        TEXT,
    start_epoch  INTEGER,
    url          TEXT,
    leader       TEXT,
    signup_count INTEGER,
    channel_id   TEXT,
    status       TEXT NOT NULL DEFAULT 'scheduled',
    raw_json     TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_calendar_start ON calendar_events (start_epoch);

-- Feed digest queue: events buffered for periodic roundup posts.  A routing
-- rule with style_json containing {"mode": "digest"} lands events here instead
-- of posting immediately; flush_digests() sweeps the queue and posts summaries.
CREATE TABLE IF NOT EXISTS feed_digest_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id    INTEGER,
    channel_id TEXT NOT NULL,
    source     TEXT NOT NULL,
    event_json TEXT NOT NULL,
    style_json TEXT,
    queued_at  TEXT DEFAULT (datetime('now', 'utc')),
    flushed    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_digest_pending ON feed_digest_queue (flushed, channel_id);

-- M+ personal-best key level per character.  Updated when the feed sees a
-- new high; drives the PB announcement path in raiderio_mplus.
CREATE TABLE IF NOT EXISTS mplus_bests (
    character_name TEXT NOT NULL,
    realm_slug     TEXT NOT NULL DEFAULT 'hellfire',
    region         TEXT NOT NULL DEFAULT 'eu',
    best_level     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT,
    PRIMARY KEY (character_name, realm_slug, region)
);

-- Opt-in feed alert subscriptions.  Members use /alerts subscribe to receive
-- pings when a source fires; the runner appends their mentions to the content.
CREATE TABLE IF NOT EXISTS feed_subscriptions (
    discord_user_id TEXT NOT NULL,
    source          TEXT NOT NULL,
    subscribed_at   TEXT DEFAULT (datetime('now', 'utc')),
    PRIMARY KEY (discord_user_id, source)
);

-- Recruitment board: officer-configured state (open slots per role, trial
-- rules, pitch override) kept as one JSON document.  Edited via the /recruit
-- commands; the recruitment board renders from it on every refresh.
CREATE TABLE IF NOT EXISTS recruitment_config (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    state_json TEXT,
    updated_at TEXT
);

-- Sticky pages: multi-message, GM-authored markdown pages (Rules & Info etc.)
-- placed with /page post.  channel_id is the officer-requested destination;
-- posted_channel_id + message_ids_json record where the live copy actually
-- sits (ordered, one id per message) so a channel move deletes the old copy
-- before re-posting, and content_hash lets a refresh skip unchanged copy.
CREATE TABLE IF NOT EXISTS sticky_pages (
    page_name         TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL,
    posted_channel_id TEXT,
    message_ids_json  TEXT,
    content_hash      TEXT,
    created_at        TEXT,
    updated_at        TEXT
);

-- Dashboard-edited copy: overrides for the bundled ftabot/content/*.json
-- documents, one JSON body per document name.  A row here beats the bundled
-- file; deleting the row reverts to the shipped copy.
CREATE TABLE IF NOT EXISTS content_docs (
    name       TEXT PRIMARY KEY,
    body_json  TEXT NOT NULL,
    updated_at TEXT
);
"""

class ColumnOwner(Enum):
    """Who is allowed to write a ``members`` column.

    SYNC   — the API roster sync owns it; a human never edits it.
    MANUAL — a human owns it (officer command / GRM identity overlay); a sync
             must never overwrite it. This split is the codebase's central
             invariant.
    SYSTEM — identity (the natural key), timestamps and the details blob,
             managed by the DB layer itself.
    """

    SYNC = "sync"
    MANUAL = "manual"
    SYSTEM = "system"


@dataclass(frozen=True)
class MemberColumn:
    """One ``members`` column and who owns it — the single source of truth."""

    name: str  # SQL column name
    sql_type: str  # SQL type, used when the migration ADDs it to an old DB
    owner: ColumnOwner
    member_attr: str  # the Member dataclass attribute it maps to
    in_sync_upsert: bool = False  # written by upsert_synced's column loop
    migrate: bool = False  # add to pre-existing DBs on connect (forward-only)


# The whole ownership story for the members table, declared once. SYNC_COLUMNS,
# MANUAL_COLUMNS and MIGRATION_COLUMNS are derived below — classify a new column
# here and nowhere else. ``details_json`` is sync-owned but (de)serialised
# separately, so it is not part of the upsert column loop.
MEMBER_COLUMNS: tuple[MemberColumn, ...] = (
    # identity / system
    MemberColumn("id", "INTEGER", ColumnOwner.SYSTEM, "id"),
    MemberColumn("character_name", "TEXT", ColumnOwner.SYSTEM, "character_name"),
    MemberColumn("realm_slug", "TEXT", ColumnOwner.SYSTEM, "realm_slug"),
    MemberColumn("region", "TEXT", ColumnOwner.SYSTEM, "region"),
    # API-sourced (sync owns; upsert_synced writes these)
    MemberColumn("class_name", "TEXT", ColumnOwner.SYNC, "class_name", in_sync_upsert=True),
    MemberColumn("active_spec", "TEXT", ColumnOwner.SYNC, "active_spec", in_sync_upsert=True),
    MemberColumn("role", "TEXT", ColumnOwner.SYNC, "role", in_sync_upsert=True),
    MemberColumn("race", "TEXT", ColumnOwner.SYNC, "race", in_sync_upsert=True),
    MemberColumn("level", "INTEGER", ColumnOwner.SYNC, "level", in_sync_upsert=True),
    MemberColumn("faction", "TEXT", ColumnOwner.SYNC, "faction", in_sync_upsert=True),
    MemberColumn("guild_rank", "INTEGER", ColumnOwner.SYNC, "guild_rank", in_sync_upsert=True),
    MemberColumn("item_level", "REAL", ColumnOwner.SYNC, "item_level", in_sync_upsert=True),
    MemberColumn("average_item_level", "REAL", ColumnOwner.SYNC, "average_item_level",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("mythic_plus_score", "REAL", ColumnOwner.SYNC, "mythic_plus_score", in_sync_upsert=True),
    MemberColumn("raid_progression", "TEXT", ColumnOwner.SYNC, "raid_progression", in_sync_upsert=True),
    MemberColumn("best_parse_avg", "REAL", ColumnOwner.SYNC, "best_parse_avg", in_sync_upsert=True),
    MemberColumn("last_login", "TEXT", ColumnOwner.SYNC, "last_login",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("missing_enchants", "INTEGER", ColumnOwner.SYNC, "missing_enchants",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("missing_gems", "INTEGER", ColumnOwner.SYNC, "missing_gems",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("vault_slots_filled", "INTEGER", ColumnOwner.SYNC, "vault_slots_filled",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("wishlist_pct", "REAL", ColumnOwner.SYNC, "wishlist_pct",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("thumbnail_url", "TEXT", ColumnOwner.SYNC, "thumbnail_url", in_sync_upsert=True),
    MemberColumn("profile_url", "TEXT", ColumnOwner.SYNC, "profile_url", in_sync_upsert=True),
    MemberColumn("source", "TEXT", ColumnOwner.SYNC, "source",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("blizzard_id", "INTEGER", ColumnOwner.SYNC, "blizzard_id",
                 in_sync_upsert=True, migrate=True),
    MemberColumn("details_json", "TEXT", ColumnOwner.SYNC, "details", migrate=True),
    # human-entered (manual owns; a sync must never overwrite)
    MemberColumn("discord_id", "TEXT", ColumnOwner.MANUAL, "discord_id"),
    MemberColumn("is_main", "INTEGER", ColumnOwner.MANUAL, "is_main"),
    MemberColumn("main_character_id", "INTEGER", ColumnOwner.MANUAL, "main_character_id"),
    MemberColumn("notes", "TEXT", ColumnOwner.MANUAL, "notes"),
    MemberColumn("raid_team", "TEXT", ColumnOwner.MANUAL, "raid_team", migrate=True),
    MemberColumn("officer_role", "TEXT", ColumnOwner.MANUAL, "officer_role", migrate=True),
    # timestamps
    MemberColumn("last_synced", "TEXT", ColumnOwner.SYSTEM, "last_synced"),
    MemberColumn("created_at", "TEXT", ColumnOwner.SYSTEM, "created_at"),
    MemberColumn("updated_at", "TEXT", ColumnOwner.SYSTEM, "updated_at"),
)

# Derived partitions — do not hand-edit; classify columns in MEMBER_COLUMNS.
SYNC_COLUMNS: tuple[str, ...] = tuple(c.name for c in MEMBER_COLUMNS if c.in_sync_upsert)
MANUAL_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in MEMBER_COLUMNS if c.owner is ColumnOwner.MANUAL
)
# Columns we add to pre-existing databases on connect (name -> SQL type).
MIGRATION_COLUMNS: dict[str, str] = {
    c.name: c.sql_type for c in MEMBER_COLUMNS if c.migrate
}
