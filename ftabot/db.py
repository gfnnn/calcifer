"""Async SQLite persistence layer for guild members.

A single ``members`` table keyed by (character_name, realm_slug, region) holds
everything we know about a character. Manual fields (Discord link, main/alt,
notes) are kept separate from sync-managed fields so a roster refresh never
stomps on data an officer typed in by hand.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import aiosqlite

# Schema DDL + column manifests live in db_schema (imported under the original
# internal names so the query methods below are unchanged).
from .db_schema import MANUAL_COLUMNS as _MANUAL_COLUMNS
from .db_schema import SCHEMA
from .db_schema import SYNC_COLUMNS as _SYNC_COLUMNS
from .migrations import run_migrations
from .models import IdentityRecord, LootEntry, Member, WishlistItem, now_iso

if TYPE_CHECKING:
    from .identity import Account

# A reserved seen_items external_id used only to record that a feed source has
# been polled at least once. It lets a source that is *usually silent* (raid
# reminders, prog) be marked primed even on a zero-event poll, so its first real
# event is posted rather than mistaken for an initial backfill and swallowed.
_PRIME_SENTINEL = "__calcifer_source_primed__"

# Outbox delivery is at-least-once with a visibility timeout: a claimed row that
# isn't marked delivered within this window is presumed lost and re-delivered,
# up to a bounded number of attempts (so a permanently-failing row dead-letters
# instead of looping forever). This avoids the double-post that a claim/release
# pattern hits when a send raises *after* Discord already accepted the message.
_OUTBOX_VISIBILITY_SECONDS = 300
_OUTBOX_MAX_ATTEMPTS = 5


def _outbox_cutoff() -> str:
    """ISO timestamp before which a claimed-but-undelivered row is retryable."""
    return (
        datetime.now(timezone.utc) - timedelta(seconds=_OUTBOX_VISIBILITY_SECONDS)
    ).isoformat(timespec="seconds")


class Database:
    """Thin async wrapper around an aiosqlite connection."""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call connect() first.")
        return self._conn

    async def connect(self) -> "Database":
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        # WAL lets the web app, Discord bot and sync share the file with one
        # writer + concurrent readers (the all-in-one deploy runs all three).
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.execute("PRAGMA busy_timeout = 5000;")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()
        return self

    async def _migrate(self) -> None:
        """Bring an older database up to the current schema (forward-only)."""
        await run_migrations(self.conn, now=now_iso())

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Database":
        return await self.connect()

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # -- reads ---------------------------------------------------------------

    @staticmethod
    def _row_to_member(row: aiosqlite.Row) -> Member:
        return Member(
            id=row["id"],
            character_name=row["character_name"],
            realm_slug=row["realm_slug"],
            region=row["region"],
            class_name=row["class_name"],
            active_spec=row["active_spec"],
            role=row["role"],
            race=row["race"],
            level=row["level"],
            faction=row["faction"],
            guild_rank=row["guild_rank"],
            item_level=row["item_level"],
            average_item_level=row["average_item_level"],
            mythic_plus_score=row["mythic_plus_score"],
            raid_progression=row["raid_progression"],
            best_parse_avg=row["best_parse_avg"],
            last_login=row["last_login"],
            missing_enchants=row["missing_enchants"],
            missing_gems=row["missing_gems"],
            vault_slots_filled=row["vault_slots_filled"],
            wishlist_pct=row["wishlist_pct"],
            thumbnail_url=row["thumbnail_url"],
            profile_url=row["profile_url"],
            source=row["source"],
            blizzard_id=row["blizzard_id"],
            details=json.loads(row["details_json"]) if row["details_json"] else {},
            discord_id=row["discord_id"],
            is_main=bool(row["is_main"]),
            main_character_id=row["main_character_id"],
            notes=row["notes"],
            raid_team=row["raid_team"],
            officer_role=row["officer_role"],
            last_synced=row["last_synced"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_member(
        self, name: str, realm: str, region: str
    ) -> Member | None:
        cur = await self.conn.execute(
            """SELECT * FROM members
               WHERE lower(character_name) = lower(?)
                 AND lower(realm_slug) = lower(?)
                 AND lower(region) = lower(?)""",
            (name, realm, region),
        )
        row = await cur.fetchone()
        return self._row_to_member(row) if row else None

    async def get_member_by_id(self, member_id: int) -> Member | None:
        cur = await self.conn.execute(
            "SELECT * FROM members WHERE id = ?", (member_id,)
        )
        row = await cur.fetchone()
        return self._row_to_member(row) if row else None

    async def all_members(self, *, order_by: str = "mythic_plus_score") -> list[Member]:
        # Whitelist the sort column to avoid SQL injection via callers. Every sort
        # carries a stable name tie-breaker so equal-scored rows keep a fixed order
        # between queries — otherwise a board ranking by score (e.g. the Hot 20)
        # could reshuffle ties, forcing needless edits and phantom movement arrows.
        allowed = {
            "mythic_plus_score": "mythic_plus_score DESC, character_name COLLATE NOCASE ASC",
            "item_level": "item_level DESC, character_name COLLATE NOCASE ASC",
            "name": "character_name COLLATE NOCASE ASC",
            "guild_rank": "guild_rank ASC, character_name COLLATE NOCASE ASC",
            "best_parse_avg": "best_parse_avg DESC, character_name COLLATE NOCASE ASC",
        }
        order = allowed.get(order_by, allowed["mythic_plus_score"])
        # Keep NULLs at the bottom for the DESC numeric sorts.
        cur = await self.conn.execute(
            f"SELECT * FROM members ORDER BY ({order.split()[0]}) IS NULL, {order}"
        )
        rows = await cur.fetchall()
        return [self._row_to_member(r) for r in rows]

    async def search_members(
        self,
        *,
        name: str | None = None,
        class_name: str | None = None,
        role: str | None = None,
        discord_id: str | None = None,
    ) -> list[Member]:
        clauses: list[str] = []
        params: list[object] = []
        if name:
            clauses.append("character_name LIKE ? COLLATE NOCASE")
            params.append(f"%{name}%")
        if class_name:
            clauses.append("lower(class_name) = lower(?)")
            params.append(class_name)
        if role:
            clauses.append("lower(role) = lower(?)")
            params.append(role)
        if discord_id:
            clauses.append("discord_id = ?")
            params.append(discord_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = await self.conn.execute(
            f"SELECT * FROM members {where} "
            "ORDER BY character_name COLLATE NOCASE ASC",
            params,
        )
        rows = await cur.fetchall()
        return [self._row_to_member(r) for r in rows]

    async def alts_of(self, member_id: int) -> list[Member]:
        cur = await self.conn.execute(
            "SELECT * FROM members WHERE main_character_id = ? "
            "ORDER BY character_name COLLATE NOCASE ASC",
            (member_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_member(r) for r in rows]

    async def count(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM members")
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def officers(self, *, rank_max: int) -> list[Member]:
        """Members at/above the officer rank cutoff, or with a manual role."""
        cur = await self.conn.execute(
            "SELECT * FROM members "
            "WHERE (guild_rank IS NOT NULL AND guild_rank <= ?) "
            "   OR officer_role IS NOT NULL "
            "ORDER BY guild_rank IS NULL, guild_rank ASC, "
            "         character_name COLLATE NOCASE ASC",
            (rank_max,),
        )
        rows = await cur.fetchall()
        return [self._row_to_member(r) for r in rows]

    async def team_members(self, team: str) -> list[Member]:
        cur = await self.conn.execute(
            "SELECT * FROM members WHERE lower(raid_team) = lower(?) "
            "ORDER BY character_name COLLATE NOCASE ASC",
            (team,),
        )
        rows = await cur.fetchall()
        return [self._row_to_member(r) for r in rows]

    # -- writes --------------------------------------------------------------

    async def upsert_synced(self, member: Member) -> int:
        """Insert or update a character using API-sourced fields only.

        Manual fields (discord_id, is_main, main_character_id, notes) are left
        untouched on update. Returns the row id.
        """
        existing = await self.get_member(
            member.character_name, member.realm_slug, member.region
        )
        ts = now_iso()
        details_json = json.dumps(member.details) if member.details else None
        if existing is None:
            cols = [
                "character_name",
                "realm_slug",
                "region",
                *_SYNC_COLUMNS,
                "details_json",
                "last_synced",
                "created_at",
                "updated_at",
            ]
            values = [
                member.character_name,
                member.realm_slug,
                member.region,
                *[getattr(member, c) for c in _SYNC_COLUMNS],
                details_json,
                ts,
                ts,
                ts,
            ]
            placeholders = ", ".join("?" for _ in cols)
            cur = await self.conn.execute(
                f"INSERT INTO members ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            await self.conn.commit()
            return int(cur.lastrowid)

        # Update only the sync columns that the incoming member actually has a
        # value for (so a partial enrichment never nulls out good data).
        assignments: list[str] = []
        params: list[object] = []
        for col in _SYNC_COLUMNS:
            val = getattr(member, col)
            if val is not None:
                assignments.append(f"{col} = ?")
                params.append(val)
        if details_json is not None:  # don't wipe stored detail with an empty blob
            assignments.append("details_json = ?")
            params.append(details_json)
        assignments.append("last_synced = ?")
        params.append(ts)
        assignments.append("updated_at = ?")
        params.append(ts)
        params.append(existing.id)
        await self.conn.execute(
            f"UPDATE members SET {', '.join(assignments)} WHERE id = ?", params
        )
        await self.conn.commit()
        return int(existing.id)  # type: ignore[arg-type]

    async def _write_manual(self, member_id: int, **columns: Any) -> None:
        """Update human-owned columns, refusing anything not MANUAL-owned.

        Every manual edit funnels through here so a column a *sync* owns can
        never be written down a "manual" path by mistake (which is how the
        sync-vs-manual invariant gets violated). Column names are validated
        against the MANUAL_COLUMNS allowlist, so interpolating them is safe.
        """
        bad = set(columns) - set(_MANUAL_COLUMNS)
        if bad:
            raise ValueError(f"_write_manual: not manual-owned columns: {sorted(bad)}")
        assignments = ", ".join(f"{name} = ?" for name in columns)
        await self.conn.execute(
            f"UPDATE members SET {assignments}, updated_at = ? WHERE id = ?",
            (*columns.values(), now_iso(), member_id),
        )
        await self.conn.commit()

    async def set_discord_link(self, member_id: int, discord_id: str | None) -> None:
        await self._write_manual(member_id, discord_id=discord_id)

    async def set_note(self, member_id: int, note: str | None) -> None:
        await self._write_manual(member_id, notes=note)

    async def set_main_alt(
        self, member_id: int, *, is_main: bool, main_character_id: int | None
    ) -> None:
        await self._write_manual(
            member_id,
            is_main=1 if is_main else 0,
            main_character_id=main_character_id,
        )

    # -- data spine: GRM identity ground truth -------------------------------

    async def upsert_identities(self, records: Iterable[IdentityRecord]) -> int:
        """Store GRM identity records (upsert on character key). Returns count."""
        n = 0
        for r in records:
            await self.conn.execute(
                """INSERT INTO identity_members
                       (character_name, realm_slug, region, guid, class_name,
                        rank_name, join_date, is_main, main_name, alts_json,
                        note, officer_note, source, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(character_name, realm_slug, region) DO UPDATE SET
                       guid=excluded.guid, class_name=excluded.class_name,
                       rank_name=excluded.rank_name, join_date=excluded.join_date,
                       is_main=excluded.is_main, main_name=excluded.main_name,
                       alts_json=excluded.alts_json, note=excluded.note,
                       officer_note=excluded.officer_note, source=excluded.source,
                       updated_at=excluded.updated_at""",
                (
                    r.character_name,
                    r.realm_slug,
                    r.region,
                    r.guid,
                    r.class_name,
                    r.rank_name,
                    r.join_date,
                    None if r.is_main is None else (1 if r.is_main else 0),
                    r.main_name,
                    json.dumps(r.alts) if r.alts else None,
                    r.note,
                    r.officer_note,
                    r.source,
                    now_iso(),
                ),
            )
            n += 1
        await self.conn.commit()
        return n

    async def member_id_for(
        self, name: str, realm_slug: str, region: str | None = None
    ) -> int | None:
        """Resolve a character to a ``members.id`` (region-agnostic fallback)."""
        if region:
            cur = await self.conn.execute(
                "SELECT id FROM members WHERE lower(character_name)=lower(?) "
                "AND lower(realm_slug)=lower(?) AND lower(region)=lower(?) LIMIT 1",
                (name, realm_slug, region),
            )
            row = await cur.fetchone()
            if row:
                return row["id"]
        cur = await self.conn.execute(
            "SELECT id FROM members WHERE lower(character_name)=lower(?) "
            "AND lower(realm_slug)=lower(?) LIMIT 1",
            (name, realm_slug),
        )
        row = await cur.fetchone()
        return row["id"] if row else None

    async def apply_accounts(
        self, accounts: Iterable[Account], *, default_region: str = "eu"
    ) -> dict[str, int]:
        """Overlay GRM accounts onto the roster's alt<->main columns.

        Authoritative, human-sourced identity: GRM owns ``is_main`` /
        ``main_character_id``, so this writes them directly (it is not an API
        sync, so the sync-vs-manual invariant is not in play). Only characters
        already in the roster are linked; an account whose main is not tracked
        is skipped rather than anchored to a fabricated row.

        Every GRM-known row is first reset to a standalone-main default, then
        re-linked from the current accounts. That way a link an officer
        *removed* in GRM actually clears, instead of leaving an orphaned
        ``main_character_id`` forever. Rows GRM never mentions (e.g. mains/alts
        set by the ``/main`` / ``/alt`` officer commands) are left untouched.
        """
        accounts_linked = 0
        alts_linked = 0
        # Resolve ids once, building the link plan and the set of GRM-known rows.
        plans: list[tuple[int, list[int]]] = []
        known_ids: set[int] = set()
        for account in accounts:
            main_id = await self.member_id_for(*account.main, default_region)
            if main_id is None:
                continue
            alt_ids: list[int] = []
            for alt_key in account.alts:
                alt_id = await self.member_id_for(*alt_key, default_region)
                if alt_id is None or alt_id == main_id:
                    continue
                alt_ids.append(alt_id)
            plans.append((main_id, alt_ids))
            known_ids.add(main_id)
            known_ids.update(alt_ids)

        # Clear stale links on every GRM-known row first...
        for member_id in known_ids:
            await self.set_main_alt(member_id, is_main=True, main_character_id=None)
        # ...then apply the current account structure.
        for main_id, alt_ids in plans:
            accounts_linked += 1
            for alt_id in alt_ids:
                await self.set_main_alt(alt_id, is_main=False, main_character_id=main_id)
                alts_linked += 1
        return {"accounts_linked": accounts_linked, "alts_linked": alts_linked}

    @staticmethod
    def _row_to_identity(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "character_name": row["character_name"],
            "realm_slug": row["realm_slug"],
            "region": row["region"],
            "guid": row["guid"],
            "class_name": row["class_name"],
            "rank_name": row["rank_name"],
            "join_date": row["join_date"],
            "is_main": None if row["is_main"] is None else bool(row["is_main"]),
            "main_name": row["main_name"],
            "alts": json.loads(row["alts_json"]) if row["alts_json"] else [],
            "note": row["note"],
            "officer_note": row["officer_note"],
            "source": row["source"],
            "updated_at": row["updated_at"],
        }

    async def get_identity(
        self, name: str, realm: str, region: str | None = None
    ) -> dict[str, Any] | None:
        """The stored GRM identity record for a character, or ``None``."""
        if region:
            cur = await self.conn.execute(
                "SELECT * FROM identity_members WHERE lower(character_name)=lower(?) "
                "AND lower(realm_slug)=lower(?) AND lower(region)=lower(?) LIMIT 1",
                (name, realm, region),
            )
            row = await cur.fetchone()
            if row:
                return self._row_to_identity(row)
        cur = await self.conn.execute(
            "SELECT * FROM identity_members WHERE lower(character_name)=lower(?) "
            "AND lower(realm_slug)=lower(?) LIMIT 1",
            (name, realm),
        )
        row = await cur.fetchone()
        return self._row_to_identity(row) if row else None

    async def set_raid_team(self, member_id: int, team: str | None) -> None:
        await self._write_manual(member_id, raid_team=team)

    async def set_officer_role(self, member_id: int, role: str | None) -> None:
        await self._write_manual(member_id, officer_role=role)

    async def set_wishlist_pct(self, member_id: int, pct: float | None) -> None:
        await self.conn.execute(
            "UPDATE members SET wishlist_pct = ?, updated_at = ? WHERE id = ?",
            (pct, now_iso(), member_id),
        )
        await self.conn.commit()

    async def delete_member(self, member_id: int) -> None:
        await self.conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
        await self.conn.commit()

    # -- loot history --------------------------------------------------------

    @staticmethod
    def _row_to_loot(row: aiosqlite.Row) -> LootEntry:
        return LootEntry(
            id=row["id"],
            character_name=row["character_name"],
            realm_slug=row["realm_slug"],
            region=row["region"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            item_level=row["item_level"],
            slot=row["slot"],
            instance=row["instance"],
            boss=row["boss"],
            difficulty=row["difficulty"],
            response=row["response"],
            votes=row["votes"],
            awarded_by=row["awarded_by"],
            awarded_at=row["awarded_at"],
            source=row["source"],
            raw=json.loads(row["raw_json"]) if row["raw_json"] else {},
        )

    async def add_loot(self, entries: Iterable[LootEntry]) -> int:
        """Insert loot awards, skipping duplicates. Returns rows inserted."""
        inserted = 0
        for e in entries:
            cur = await self.conn.execute(
                """INSERT OR IGNORE INTO loot_history (
                       character_name, realm_slug, region, item_id, item_name,
                       item_level, slot, instance, boss, difficulty, response,
                       votes, awarded_by, awarded_at, source, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    e.character_name,
                    e.realm_slug,
                    e.region,
                    e.item_id,
                    e.item_name,
                    e.item_level,
                    e.slot,
                    e.instance,
                    e.boss,
                    e.difficulty,
                    e.response,
                    e.votes,
                    e.awarded_by,
                    e.awarded_at,
                    e.source,
                    json.dumps(e.raw) if e.raw else None,
                ),
            )
            inserted += cur.rowcount or 0
        await self.conn.commit()
        return inserted

    async def get_loot(
        self, *, character: str | None = None, limit: int = 200
    ) -> list[LootEntry]:
        if character:
            cur = await self.conn.execute(
                "SELECT * FROM loot_history WHERE lower(character_name) = lower(?) "
                "ORDER BY awarded_at DESC LIMIT ?",
                (character, limit),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM loot_history ORDER BY awarded_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_loot(r) for r in await cur.fetchall()]

    async def loot_counts(self) -> dict[str, int]:
        """Number of items won per character (lowercased name -> count)."""
        cur = await self.conn.execute(
            "SELECT lower(character_name) AS name, COUNT(*) AS n "
            "FROM loot_history GROUP BY lower(character_name)"
        )
        return {row["name"]: int(row["n"]) for row in await cur.fetchall()}

    # -- wishlists -----------------------------------------------------------

    @staticmethod
    def _row_to_wishlist(row: aiosqlite.Row) -> WishlistItem:
        return WishlistItem(
            id=row["id"],
            character_name=row["character_name"],
            realm_slug=row["realm_slug"],
            region=row["region"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            slot=row["slot"],
            boss=row["boss"],
            difficulty=row["difficulty"],
            upgrade_pct=row["upgrade_pct"],
            is_bis=bool(row["is_bis"]),
            obtained=bool(row["obtained"]),
            source=row["source"],
            raw=json.loads(row["raw_json"]) if row["raw_json"] else {},
        )

    async def replace_wishlist(
        self, character: str, items: Iterable[WishlistItem]
    ) -> int:
        """Replace a character's wishlist wholesale. Returns rows written."""
        await self.conn.execute(
            "DELETE FROM wishlist_items WHERE lower(character_name) = lower(?)",
            (character,),
        )
        written = 0
        for w in items:
            await self.conn.execute(
                """INSERT OR REPLACE INTO wishlist_items (
                       character_name, realm_slug, region, item_id, item_name,
                       slot, boss, difficulty, upgrade_pct, is_bis, obtained,
                       source, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    w.character_name,
                    w.realm_slug,
                    w.region,
                    w.item_id,
                    w.item_name,
                    w.slot,
                    w.boss,
                    w.difficulty,
                    w.upgrade_pct,
                    1 if w.is_bis else 0,
                    1 if w.obtained else 0,
                    w.source,
                    json.dumps(w.raw) if w.raw else None,
                ),
            )
            written += 1
        await self.conn.commit()
        return written

    async def wishlist_for_item(
        self, *, item_id: int | None = None, item_name: str | None = None
    ) -> list[WishlistItem]:
        """Find every character who wishlisted a given item (id or name match)."""
        if item_id is not None:
            cur = await self.conn.execute(
                "SELECT * FROM wishlist_items WHERE item_id = ?", (item_id,)
            )
        elif item_name:
            cur = await self.conn.execute(
                "SELECT * FROM wishlist_items "
                "WHERE item_name LIKE ? COLLATE NOCASE",
                (f"%{item_name}%",),
            )
        else:
            return []
        return [self._row_to_wishlist(r) for r in await cur.fetchall()]

    async def get_wishlist(self, character: str) -> list[WishlistItem]:
        cur = await self.conn.execute(
            "SELECT * FROM wishlist_items WHERE lower(character_name) = lower(?) "
            "ORDER BY obtained ASC, upgrade_pct DESC",
            (character,),
        )
        return [self._row_to_wishlist(r) for r in await cur.fetchall()]

    async def wishlist_progress(self, character: str) -> tuple[int, int, float | None]:
        """Return (obtained, total, pct) for a character's wishlist."""
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN obtained THEN 1 ELSE 0 END) AS got "
            "FROM wishlist_items WHERE lower(character_name) = lower(?)",
            (character,),
        )
        row = await cur.fetchone()
        total = int(row["total"]) if row and row["total"] else 0
        got = int(row["got"]) if row and row["got"] else 0
        pct = (100.0 * got / total) if total else None
        return got, total, pct

    # -- attendance ----------------------------------------------------------

    async def add_raid_event(
        self,
        *,
        started_at: str | None,
        instance: str | None,
        difficulty: str | None,
        source: str | None = None,
        raw: dict | None = None,
    ) -> int:
        await self.conn.execute(
            "INSERT OR IGNORE INTO raid_events "
            "(started_at, instance, difficulty, source, raw_json) "
            "VALUES (?,?,?,?,?)",
            (started_at, instance, difficulty, source, json.dumps(raw) if raw else None),
        )
        cur = await self.conn.execute(
            "SELECT id FROM raid_events WHERE started_at IS ? "
            "AND instance IS ? AND difficulty IS ?",
            (started_at, instance, difficulty),
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return int(row["id"])

    async def add_attendance(
        self,
        raid_event_id: int,
        character: str,
        *,
        realm: str | None = None,
        status: str = "present",
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO attendance "
            "(raid_event_id, character_name, realm_slug, status) VALUES (?,?,?,?)",
            (raid_event_id, character, realm, status),
        )
        await self.conn.commit()

    async def attendance_stats(
        self, character: str, *, last_n: int | None = None
    ) -> tuple[int, int, float | None]:
        """Return (present, scheduled, pct) over the most recent events.

        Bench/late count as available (present) for the percentage.
        """
        cur = await self.conn.execute(
            "SELECT a.status FROM attendance a "
            "JOIN raid_events e ON e.id = a.raid_event_id "
            "WHERE lower(a.character_name) = lower(?) "
            # Order by the date portion (so a bare date and a same-day datetime
            # rank together) with the row id as a stable tiebreaker, rather than
            # a raw lexical sort that mixed timestamp formats could misrank.
            "ORDER BY substr(e.started_at, 1, 10) DESC, e.id DESC"
            + (" LIMIT ?" if last_n is not None else ""),
            (character, last_n) if last_n is not None else (character,),
        )
        rows = await cur.fetchall()
        scheduled = len(rows)
        present = sum(1 for r in rows if (r["status"] or "").lower() != "absent")
        pct = (100.0 * present / scheduled) if scheduled else None
        return present, scheduled, pct

    # -- snapshots -----------------------------------------------------------

    async def add_snapshot(self, snapshot: dict) -> int:
        cur = await self.conn.execute(
            """INSERT INTO guild_snapshots (
                   taken_at, member_count, active_count, avg_item_level,
                   avg_mythic_plus, progression_summary, role_counts_json,
                   class_counts_json, health_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                snapshot.get("taken_at") or now_iso(),
                snapshot.get("member_count"),
                snapshot.get("active_count"),
                snapshot.get("avg_item_level"),
                snapshot.get("avg_mythic_plus"),
                snapshot.get("progression_summary"),
                json.dumps(snapshot.get("role_counts", {})),
                json.dumps(snapshot.get("class_counts", {})),
                snapshot.get("health_score"),
            ),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def snapshots(self, *, limit: int = 30) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM guild_snapshots ORDER BY taken_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        out: list[dict] = []
        for row in await cur.fetchall():
            out.append(
                {
                    "taken_at": row["taken_at"],
                    "member_count": row["member_count"],
                    "active_count": row["active_count"],
                    "avg_item_level": row["avg_item_level"],
                    "avg_mythic_plus": row["avg_mythic_plus"],
                    "progression_summary": row["progression_summary"],
                    "role_counts": json.loads(row["role_counts_json"] or "{}"),
                    "class_counts": json.loads(row["class_counts_json"] or "{}"),
                    "health_score": row["health_score"],
                }
            )
        return out

    async def prune_missing(self, present_ids: Iterable[int]) -> int:
        """Delete members whose id is not in ``present_ids``.

        Used after a full roster sync to drop characters who left the guild.
        Returns the number of rows removed.
        """
        keep = set(present_ids)
        cur = await self.conn.execute("SELECT id FROM members")
        all_ids = {row["id"] for row in await cur.fetchall()}
        stale: Sequence[int] = list(all_ids - keep)
        if not stale:
            return 0
        await self.conn.executemany(
            "DELETE FROM members WHERE id = ?", [(i,) for i in stale]
        )
        await self.conn.commit()
        return len(stale)

    # -- integration hub: dedup + routing rules ------------------------------

    async def filter_unseen(self, events: list) -> list:
        """Return only events we have not posted before (by source+external_id)."""
        unseen = []
        for event in events:
            source, external_id = event.dedup_key
            cur = await self.conn.execute(
                "SELECT 1 FROM seen_items WHERE source = ? AND external_id = ?",
                (source, external_id),
            )
            if await cur.fetchone() is None:
                unseen.append(event)
        return unseen

    async def source_primed(self, source: str) -> bool:
        """True once we have recorded any item for a source.

        Lets the runner suppress a backfill flood the first time a feed is
        polled: prime the dedup table silently, then post only new items after.
        """
        cur = await self.conn.execute(
            "SELECT 1 FROM seen_items WHERE source = ? LIMIT 1", (source,)
        )
        return await cur.fetchone() is not None

    async def mark_seen(self, source: str, external_id: str) -> None:
        """Record that a feed item has been posted (idempotent)."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO seen_items (source, external_id, first_seen_at) "
            "VALUES (?, ?, ?)",
            (source, external_id, now_iso()),
        )
        await self.conn.commit()

    async def prime_source(self, source: str) -> None:
        """Mark a source primed after its first successful poll, even when it
        emitted no items. Without this, a normally-silent source would keep
        ``source_primed`` False until it finally produced an event, then treat
        that first real event as the initial backfill and silently swallow it."""
        await self.mark_seen(source, _PRIME_SENTINEL)

    async def feed_rules(self, source: str | None = None) -> list[dict]:
        """Return enabled routing rules, optionally filtered to one source."""
        if source is None:
            cur = await self.conn.execute(
                "SELECT * FROM feed_rules WHERE enabled = 1 ORDER BY id"
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM feed_rules WHERE enabled = 1 AND source = ? ORDER BY id",
                (source,),
            )
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "source": r["source"],
                "event_type": r["event_type"],
                "match": json.loads(r["match_json"] or "{}"),
                "channel_id": r["channel_id"],
                "style": json.loads(r["style_json"] or "{}"),
            }
            for r in rows
        ]

    async def all_feed_rules(self) -> list[dict]:
        """Every rule (enabled or not) for the management UI."""
        cur = await self.conn.execute("SELECT * FROM feed_rules ORDER BY id")
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "source": r["source"],
                "event_type": r["event_type"],
                "match": json.loads(r["match_json"] or "{}"),
                "channel_id": r["channel_id"],
                "style": json.loads(r["style_json"] or "{}"),
                "enabled": bool(r["enabled"]),
            }
            for r in rows
        ]

    async def delete_feed_rule(self, rule_id: int) -> bool:
        """Remove a routing rule; returns True if one was deleted."""
        cur = await self.conn.execute(
            "DELETE FROM feed_rules WHERE id = ?", (rule_id,)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_feed_rule_enabled(self, rule_id: int, enabled: bool) -> bool:
        """Enable or disable a routing rule without deleting it.

        A disabled rule is kept in the table (and shown by ``all_feed_rules``)
        but skipped by :meth:`feed_rules`, so the router ignores it. Returns
        True if a matching rule was updated.
        """
        cur = await self.conn.execute(
            "UPDATE feed_rules SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, rule_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # -- integration hub: source mutes ---------------------------------------

    async def mute_source(self, source: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO feed_mutes (source, muted_at) VALUES (?, ?)",
            (source, now_iso()),
        )
        await self.conn.commit()

    async def unmute_source(self, source: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM feed_mutes WHERE source = ?", (source,)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def muted_sources(self) -> set[str]:
        cur = await self.conn.execute("SELECT source FROM feed_mutes")
        return {r["source"] for r in await cur.fetchall()}

    async def enqueue_outbox(self, channel_id: str, event_json: str, style_json: str) -> int:
        """Queue a push event (already serialized) for the bot to post."""
        cur = await self.conn.execute(
            "INSERT INTO feed_outbox (channel_id, event_json, style_json, created_at, posted) "
            "VALUES (?, ?, ?, ?, 0)",
            (str(channel_id), event_json, style_json, now_iso()),
        )
        await self.conn.commit()
        return int(cur.lastrowid)

    async def pending_outbox(self) -> list[dict]:
        """Return outbox rows eligible for delivery (oldest first).

        A row is eligible when it is still pending (``posted = 0``) or was
        claimed but never confirmed delivered and has now passed the visibility
        timeout (``posted = 1`` and stale ``claimed_at``). Rows that exhausted
        their attempt budget are dead-lettered (left for inspection, not retried).
        """
        cur = await self.conn.execute(
            "SELECT id, channel_id, event_json, style_json FROM feed_outbox "
            "WHERE attempts < ? "
            "  AND (posted = 0 OR (posted = 1 AND claimed_at < ?)) "
            "ORDER BY id",
            (_OUTBOX_MAX_ATTEMPTS, _outbox_cutoff()),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "channel_id": r["channel_id"],
                "event_json": r["event_json"],
                "style_json": r["style_json"],
            }
            for r in rows
        ]

    async def claim_outbox(self, row_id: int) -> bool:
        """Atomically claim an eligible outbox row for sending.

        Returns True only for the caller that wins the claim, so two drains on
        separate connections (the Raid-Helper webhook handler and the feed loop)
        can never post the same row twice: the winner stamps ``claimed_at = now``
        and bumps ``attempts`` in a single conditional UPDATE, so the loser's
        ``WHERE`` no longer matches and it sees ``rowcount == 0``. A stale claim
        (past the visibility timeout) is reclaimable, which is what re-delivers a
        row whose previous send failed without ever confirming delivery.
        """
        cur = await self.conn.execute(
            "UPDATE feed_outbox SET posted = 1, claimed_at = ?, attempts = attempts + 1 "
            "WHERE id = ? AND attempts < ? "
            "  AND (posted = 0 OR (posted = 1 AND claimed_at < ?))",
            (now_iso(), row_id, _OUTBOX_MAX_ATTEMPTS, _outbox_cutoff()),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def mark_outbox_sent(self, row_id: int) -> None:
        """Mark a claimed row delivered (terminal); it is never re-sent."""
        await self.conn.execute(
            "UPDATE feed_outbox SET posted = 2, claimed_at = NULL WHERE id = ?", (row_id,)
        )
        await self.conn.commit()

    async def release_outbox(self, row_id: int) -> None:
        """Return a claimed row to the queue *without* counting the attempt.

        Used when a send was blocked before it ever reached Discord (the posting
        guardrail), so the row should retry immediately once the gate opens and
        the gated attempt should not burn its delivery budget. A send that raised
        *after* reaching Discord must NOT be released — it stays claimed so the
        visibility timeout, not an instant re-post, governs any retry.
        """
        await self.conn.execute(
            "UPDATE feed_outbox "
            "SET posted = 0, claimed_at = NULL, attempts = MAX(attempts - 1, 0) "
            "WHERE id = ?",
            (row_id,),
        )
        await self.conn.commit()

    async def add_feed_rule(
        self,
        source: str,
        channel_id: str,
        *,
        event_type: str | None = None,
        match: dict | None = None,
        style: dict | None = None,
        updated_by: str | None = None,
    ) -> int:
        """Insert a routing rule; returns its id."""
        cur = await self.conn.execute(
            "INSERT INTO feed_rules "
            "(source, event_type, match_json, channel_id, style_json, enabled, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (
                source,
                event_type,
                json.dumps(match or {}),
                str(channel_id),
                json.dumps(style or {}),
                updated_by,
                now_iso(),
            ),
        )
        await self.conn.commit()
        return cur.lastrowid

    # -- living boards -------------------------------------------------------

    @staticmethod
    def _board_row(r: aiosqlite.Row) -> dict:
        return {
            "id": r["id"],
            "board_type": r["board_type"],
            "channel_id": r["channel_id"],
            "message_id": r["message_id"],
            "posted_channel_id": r["posted_channel_id"],
            "config": json.loads(r["config_json"] or "{}"),
            "content_hash": r["content_hash"],
            "enabled": bool(r["enabled"]),
            "promoted": bool(r["promoted"]),
        }

    async def add_board(
        self, board_type: str, channel_id: str, *, config: dict | None = None
    ) -> int:
        """Register a board (one bot-owned message in a channel); returns its id."""
        cur = await self.conn.execute(
            "INSERT INTO boards "
            "(board_type, channel_id, config_json, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (board_type, str(channel_id), json.dumps(config or {}), now_iso(), now_iso()),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def boards(self, *, enabled_only: bool = True) -> list[dict]:
        """All boards (or just the enabled ones), oldest first."""
        sql = "SELECT * FROM boards"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        cur = await self.conn.execute(sql)
        return [self._board_row(r) for r in await cur.fetchall()]

    async def get_board(self, board_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,))
        row = await cur.fetchone()
        return self._board_row(row) if row else None

    async def set_board_message(
        self,
        board_id: int,
        message_id: str | None,
        content_hash: str | None,
        posted_channel_id: str | None = None,
    ) -> None:
        """Store the posted message id + content hash (and the channel it landed
        in) after a (re)render, so the next pass can tell an in-place edit from a
        channel change."""
        await self.conn.execute(
            "UPDATE boards SET message_id = ?, posted_channel_id = ?, "
            "content_hash = ?, updated_at = ? WHERE id = ?",
            (
                str(message_id) if message_id is not None else None,
                str(posted_channel_id) if posted_channel_id is not None else None,
                content_hash,
                now_iso(),
                board_id,
            ),
        )
        await self.conn.commit()

    async def set_board_enabled(self, board_id: int, enabled: bool) -> bool:
        cur = await self.conn.execute(
            "UPDATE boards SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, board_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_board_promoted(self, board_id: int, promoted: bool) -> bool:
        """Promote a board to (or demote from) its production channel.

        Changing promotion changes the board's *effective* channel, so the
        stored message is dropped — the next refresh re-posts it cleanly in the
        new location instead of trying to edit a message in the old one.
        """
        cur = await self.conn.execute(
            "UPDATE boards SET promoted = ?, message_id = NULL, content_hash = NULL, "
            "updated_at = ? WHERE id = ?",
            (1 if promoted else 0, now_iso(), board_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_board(self, board_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def board_baseline(self, board_type: str) -> tuple[dict[str, int], str | None]:
        """The current movement baseline for a board: {entity_key: rank} + when."""
        cur = await self.conn.execute(
            "SELECT entity_key, rank, captured_at FROM board_baselines WHERE board_type = ?",
            (board_type,),
        )
        rows = await cur.fetchall()
        ranks = {r["entity_key"]: r["rank"] for r in rows}
        captured_at = rows[0]["captured_at"] if rows else None
        return ranks, captured_at

    async def roll_board_baseline(self, board_type: str, ranking: dict[str, int]) -> None:
        """Replace a board's baseline with the current ranking (a weekly reset)."""
        now = now_iso()
        await self.conn.execute(
            "DELETE FROM board_baselines WHERE board_type = ?", (board_type,)
        )
        await self.conn.executemany(
            "INSERT INTO board_baselines (board_type, entity_key, rank, captured_at) "
            "VALUES (?, ?, ?, ?)",
            [(board_type, key, rank, now) for key, rank in ranking.items()],
        )
        await self.conn.commit()

    # -- M+ affixes -------------------------------------------------------------

    async def upsert_affix_week(
        self, week_key: str, region: str, affix_names: str, details: list
    ) -> None:
        """Store or overwrite the current week's M+ affixes for a region."""
        await self.conn.execute(
            "INSERT OR REPLACE INTO mythic_affixes "
            "(week_key, region, affix_names, details_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (week_key, region.lower(), affix_names, json.dumps(details), now_iso()),
        )
        await self.conn.commit()

    async def get_current_affixes(self, region: str) -> dict | None:
        """The most recently recorded affixes for a region, or None."""
        cur = await self.conn.execute(
            "SELECT * FROM mythic_affixes WHERE region = ? "
            "ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
            (region.lower(),),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "week_key": row["week_key"],
            "region": row["region"],
            "affix_names": row["affix_names"],
            "details": json.loads(row["details_json"] or "[]"),
            "recorded_at": row["recorded_at"],
        }

    # -- realm progression ------------------------------------------------------

    async def upsert_realm_guild(
        self,
        guild_name: str,
        realm_slug: str,
        region: str,
        raid_slug: str,
        difficulty: str,
        mythic_kills: int,
        *,
        summary: str | None = None,
        realm_rank: int | None = None,
    ) -> bool:
        """Upsert a guild's raid progress on the realm.

        Returns True when the kill count increased (new milestone worth alerting).
        Returns False on first insert or when kills are unchanged or lower.
        """
        cur = await self.conn.execute(
            "SELECT mythic_kills FROM realm_progress "
            "WHERE lower(guild_name) = lower(?) AND realm_slug = ? AND region = ? "
            "AND raid_slug = ? AND lower(difficulty) = lower(?)",
            (guild_name, realm_slug.lower(), region.lower(), raid_slug, difficulty),
        )
        row = await cur.fetchone()
        old_kills: int | None = row["mythic_kills"] if row else None
        now = now_iso()
        if row is None:
            await self.conn.execute(
                "INSERT INTO realm_progress "
                "(guild_name, realm_slug, region, raid_slug, difficulty, "
                "mythic_kills, summary, realm_rank, first_seen_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_name, realm_slug.lower(), region.lower(),
                    raid_slug, difficulty.lower(),
                    mythic_kills, summary, realm_rank, now, now,
                ),
            )
        else:
            await self.conn.execute(
                "UPDATE realm_progress "
                "SET mythic_kills = ?, summary = ?, realm_rank = ?, updated_at = ? "
                "WHERE lower(guild_name) = lower(?) AND realm_slug = ? AND region = ? "
                "AND raid_slug = ? AND lower(difficulty) = lower(?)",
                (
                    mythic_kills, summary, realm_rank, now,
                    guild_name, realm_slug.lower(), region.lower(),
                    raid_slug, difficulty,
                ),
            )
        await self.conn.commit()
        return old_kills is not None and mythic_kills > old_kills

    async def get_realm_progress(
        self, realm_slug: str, region: str, raid_slug: str, difficulty: str = "mythic"
    ) -> list[dict]:
        """All guilds on a realm for a given raid, sorted by kills descending."""
        cur = await self.conn.execute(
            "SELECT * FROM realm_progress "
            "WHERE realm_slug = ? AND region = ? AND raid_slug = ? "
            "AND lower(difficulty) = lower(?) "
            "ORDER BY mythic_kills DESC, realm_rank ASC",
            (realm_slug.lower(), region.lower(), raid_slug, difficulty),
        )
        return [
            {
                "guild_name": r["guild_name"],
                "realm_slug": r["realm_slug"],
                "region": r["region"],
                "raid_slug": r["raid_slug"],
                "difficulty": r["difficulty"],
                "mythic_kills": r["mythic_kills"],
                "summary": r["summary"],
                "realm_rank": r["realm_rank"],
                "updated_at": r["updated_at"],
            }
            for r in await cur.fetchall()
        ]

    # -- news archive -----------------------------------------------------------

    async def record_news(self, items: list[dict], *, keep: int = 60) -> int:
        """Archive news items (idempotent by source+external_id). Returns inserts.

        Each item: {source, external_id, event_type, title, url, summary, tier,
        published_at}. The first record wins (we never overwrite an item's stored
        copy), and we prune to the most-recent ``keep`` rows so the table stays
        bounded no matter how long the feed runs.
        """
        inserted = 0
        for it in items:
            if not it.get("external_id") or not it.get("title"):
                continue
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO news_items "
                "(source, external_id, event_type, title, url, summary, tier, "
                "published_at, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    it.get("source") or "news",
                    it["external_id"],
                    it.get("event_type"),
                    it["title"],
                    it.get("url"),
                    it.get("summary"),
                    (it.get("tier") or "low"),
                    it.get("published_at"),
                    now_iso(),
                ),
            )
            inserted += cur.rowcount or 0
        # Prune to the most-recent ``keep`` rows (newest published first, then
        # by insert order for items without a timestamp).
        await self.conn.execute(
            "DELETE FROM news_items WHERE rowid NOT IN ("
            "  SELECT rowid FROM news_items "
            "  ORDER BY COALESCE(published_at, recorded_at) DESC, rowid DESC LIMIT ?"
            ")",
            (keep,),
        )
        await self.conn.commit()
        return inserted

    async def recent_news(
        self, *, limit: int = 10, tier: str | None = None
    ) -> list[dict]:
        """Most-recent archived news (newest first), optionally one tier only."""
        sql = (
            "SELECT source, external_id, event_type, title, url, summary, tier, "
            "published_at FROM news_items "
        )
        params: list = []
        if tier:
            sql += "WHERE tier = ? "
            params.append(tier)
        sql += "ORDER BY COALESCE(published_at, recorded_at) DESC, rowid DESC LIMIT ?"
        params.append(limit)
        cur = await self.conn.execute(sql, params)
        return [
            {
                "source": r["source"],
                "external_id": r["external_id"],
                "event_type": r["event_type"],
                "title": r["title"],
                "url": r["url"],
                "summary": r["summary"],
                "tier": r["tier"],
                "published_at": r["published_at"],
            }
            for r in await cur.fetchall()
        ]

    # -- calendar events (Raid-Helper) ------------------------------------------

    async def upsert_calendar_event(
        self,
        event_id: str,
        *,
        title: str | None = None,
        start_epoch: int | None = None,
        url: str | None = None,
        leader: str | None = None,
        signup_count: int | None = None,
        channel_id: str | None = None,
        status: str = "scheduled",
        raw: dict | None = None,
        prune_before_epoch: int | None = None,
    ) -> None:
        """Insert or update one event's current state (latest webhook wins).

        ``prune_before_epoch`` (when given) drops events that started before it,
        so the table stays bounded as nights come and go.
        """
        await self.conn.execute(
            "INSERT INTO calendar_events "
            "(event_id, title, start_epoch, url, leader, signup_count, channel_id, "
            "status, raw_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET "
            "title = excluded.title, start_epoch = excluded.start_epoch, "
            "url = excluded.url, leader = excluded.leader, "
            "signup_count = excluded.signup_count, channel_id = excluded.channel_id, "
            "status = excluded.status, raw_json = excluded.raw_json, "
            "updated_at = excluded.updated_at",
            (
                str(event_id), title, start_epoch, url, leader, signup_count,
                channel_id, status, json.dumps(raw) if raw is not None else None,
                now_iso(),
            ),
        )
        if prune_before_epoch is not None:
            await self.conn.execute(
                "DELETE FROM calendar_events WHERE start_epoch IS NOT NULL "
                "AND start_epoch < ?",
                (prune_before_epoch,),
            )
        await self.conn.commit()

    async def upcoming_events(
        self, *, now_epoch: int, within_days: float = 7.0, limit: int = 10
    ) -> list[dict]:
        """Scheduled events starting in (now, now + within_days], soonest first."""
        horizon = now_epoch + int(within_days * 86400)
        cur = await self.conn.execute(
            "SELECT * FROM calendar_events "
            "WHERE status = 'scheduled' AND start_epoch IS NOT NULL "
            "AND start_epoch >= ? AND start_epoch <= ? "
            "ORDER BY start_epoch ASC LIMIT ?",
            (now_epoch, horizon, limit),
        )
        return [
            {
                "event_id": r["event_id"],
                "title": r["title"],
                "start_epoch": r["start_epoch"],
                "url": r["url"],
                "leader": r["leader"],
                "signup_count": r["signup_count"],
                "channel_id": r["channel_id"],
                "status": r["status"],
            }
            for r in await cur.fetchall()
        ]

    # -- feed digest queue ---------------------------------------------------------

    async def enqueue_digest(
        self,
        channel_id: str,
        source: str,
        event_json: str,
        style_json: str,
        *,
        rule_id: int | None = None,
    ) -> int:
        """Buffer a feed event for a later digest post. Returns the row id."""
        cur = await self.conn.execute(
            "INSERT INTO feed_digest_queue "
            "(rule_id, channel_id, source, event_json, style_json, queued_at, flushed) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (rule_id, str(channel_id), source, event_json, style_json, now_iso()),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def pending_digests(
        self, *, channel_id: str | None = None
    ) -> list[dict]:
        """Unflushed digest items, oldest first (optionally filtered by channel)."""
        if channel_id is not None:
            cur = await self.conn.execute(
                "SELECT * FROM feed_digest_queue "
                "WHERE flushed = 0 AND channel_id = ? ORDER BY id",
                (channel_id,),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM feed_digest_queue WHERE flushed = 0 ORDER BY channel_id, id"
            )
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "rule_id": r["rule_id"],
                "channel_id": r["channel_id"],
                "source": r["source"],
                "event_json": r["event_json"],
                "style_json": r["style_json"],
            }
            for r in rows
        ]

    async def flush_digest_batch(self, ids: list[int]) -> None:
        """Mark a batch of digest items as flushed."""
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self.conn.execute(
            f"UPDATE feed_digest_queue SET flushed = 1 WHERE id IN ({placeholders})",
            ids,
        )
        await self.conn.commit()

    # -- M+ personal bests ---------------------------------------------------------

    async def get_mplus_best(self, name: str, realm: str, region: str) -> int:
        """Highest known timed key level for a character (0 if none recorded)."""
        cur = await self.conn.execute(
            "SELECT best_level FROM mplus_bests "
            "WHERE lower(character_name) = lower(?) AND lower(realm_slug) = lower(?) "
            "AND lower(region) = lower(?)",
            (name, realm, region),
        )
        row = await cur.fetchone()
        return int(row["best_level"]) if row else 0

    async def set_mplus_best(
        self, name: str, realm: str, region: str, level: int
    ) -> None:
        """Upsert the personal-best key level; only advances, never rolls back."""
        await self.conn.execute(
            "INSERT INTO mplus_bests "
            "(character_name, realm_slug, region, best_level, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(character_name, realm_slug, region) DO UPDATE SET "
            "best_level = excluded.best_level, updated_at = excluded.updated_at "
            "WHERE excluded.best_level > best_level",
            (name, realm, region, level, now_iso()),
        )
        await self.conn.commit()

    # -- feed subscriptions --------------------------------------------------------

    async def add_subscription(self, discord_user_id: str, source: str) -> None:
        """Subscribe a Discord user to pings from a feed source (idempotent)."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO feed_subscriptions "
            "(discord_user_id, source, subscribed_at) VALUES (?, ?, ?)",
            (str(discord_user_id), source, now_iso()),
        )
        await self.conn.commit()

    async def remove_subscription(self, discord_user_id: str, source: str) -> bool:
        """Remove a subscription; returns True if one existed."""
        cur = await self.conn.execute(
            "DELETE FROM feed_subscriptions WHERE discord_user_id = ? AND source = ?",
            (str(discord_user_id), source),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def source_subscribers(self, source: str) -> list[str]:
        """Discord user IDs subscribed to alerts from this source."""
        cur = await self.conn.execute(
            "SELECT discord_user_id FROM feed_subscriptions "
            "WHERE source = ? ORDER BY subscribed_at",
            (source,),
        )
        return [r["discord_user_id"] for r in await cur.fetchall()]

    async def user_subscriptions(self, discord_user_id: str) -> list[str]:
        """Feed sources this Discord user has opted into."""
        cur = await self.conn.execute(
            "SELECT source FROM feed_subscriptions WHERE discord_user_id = ? ORDER BY source",
            (str(discord_user_id),),
        )
        return [r["source"] for r in await cur.fetchall()]
