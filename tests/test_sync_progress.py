"""A slow first sync still fills the roster and shows progress.

Raider.IO answers an uncached character profile in seconds (sometimes tens of
seconds), so enriching a ~675-character guild takes tens of minutes. These
tests pin the hardening that keeps that from looking like (or becoming) a hang:
a bounded per-request timeout, one retry per profile, per-chunk progress, and
rows persisted as the sync goes rather than only at the very end.
"""

from __future__ import annotations

import asyncio
import copy
import logging

import aiohttp
import pytest

from ftabot import sync
from ftabot.config import Config
from ftabot.models import Member
from ftabot.services import raiderio
from ftabot.services._http import DEFAULT_TIMEOUT, new_session
from ftabot.services.raiderio import RaiderIOClient
from ftabot.sync import sync_guild

from .test_raiderio import _FakeResp, _FakeSession


async def test_new_session_carries_a_bounded_timeout():
    session = new_session()
    try:
        assert session.timeout is DEFAULT_TIMEOUT
        assert 0 < session.timeout.total <= 60
        assert session.timeout.connect is not None
    finally:
        await session.close()


async def test_new_session_accepts_an_explicit_timeout():
    custom = aiohttp.ClientTimeout(total=5)
    session = new_session(timeout=custom)
    try:
        assert session.timeout is custom
    finally:
        await session.close()


async def test_enrich_all_reports_progress_after_each_chunk():
    client = RaiderIOClient()

    async def fake_enrich(member):
        member.item_level = 600.0
        return member

    client.enrich_character = fake_enrich  # no network
    calls: list[tuple[list[str], int, int]] = []

    async def on_batch(batch, done, total):
        assert all(m.item_level == 600.0 for m in batch)  # already enriched
        calls.append(([m.character_name for m in batch], done, total))

    members = [Member(f"C{i}") for i in range(7)]
    await client.enrich_all(members, concurrency=2, chunk_size=3, on_batch=on_batch)

    assert [(done, total) for _names, done, total in calls] == [(3, 7), (6, 7), (7, 7)]
    assert calls[-1][0] == ["C6"]


async def test_enrich_character_retries_a_5xx_only_once(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(raiderio.asyncio, "sleep", no_sleep)
    session = _FakeSession([_FakeResp(500)])
    client = RaiderIOClient(session=session)
    client._catalog = []  # skip the catalog fetch

    member = Member("Slowpoke", realm_slug="hellfire", region="eu")
    result = await client.enrich_character(member)

    assert result is member  # left as-is, refreshed on the next sync
    assert session.calls == 2  # the first try + one retry, not five


def _roster() -> list[Member]:
    return [
        Member(f"Char{i}", realm_slug="hellfire", region="eu", class_name="Mage")
        for i in range(5)
    ]


def _interrupted_rio(roster: list[Member], seen_in_db: list[int]):
    """A fake client whose enrichment saves one chunk, then is cancelled.

    Models a container restart mid-sync: the second chunk never completes.
    """

    class _Rio:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def guild_members(self, name, realm, region):
            return [copy.deepcopy(m) for m in roster]

        async def raid_catalog(self, region):
            return []

        async def guild_overview(self, name, realm, region):
            return {}

        async def enrich_all(self, members, *, concurrency=2, on_batch=None):
            seen_in_db.append(len(await db_ref[0].all_members()))
            first = members[:2]
            for m in first:
                m.item_level = 640.0
            await on_batch(first, 2, len(members))
            raise asyncio.CancelledError

    db_ref: list = []
    return _Rio, db_ref


async def test_interrupted_first_sync_keeps_the_roster_and_finished_chunks(
    db, monkeypatch, caplog
):
    seen_in_db: list[int] = []
    rio, db_ref = _interrupted_rio(_roster(), seen_in_db)
    db_ref.append(db)
    monkeypatch.setattr(sync, "RaiderIOClient", rio)

    with caplog.at_level(logging.INFO, logger="ftabot.sync"):
        with pytest.raises(asyncio.CancelledError):
            await sync_guild(db, Config())

    # The whole roster was written before enrichment began...
    assert seen_in_db == [5]
    stored = {m.character_name: m for m in await db.all_members()}
    assert set(stored) == {f"Char{i}" for i in range(5)}
    # ...and the chunk that finished kept its enriched stats.
    assert stored["Char0"].item_level == 640.0
    assert stored["Char1"].item_level == 640.0
    assert stored["Char4"].item_level is None
    # Progress is visible in the log.
    text = caplog.text
    assert "enriching 5 via Raider.IO" in text
    assert "enriched 2/5" in text


async def test_resync_does_not_rewrite_existing_rows_before_enrichment(
    db, monkeypatch
):
    # Char4 is already stored (enriched long ago) and falls in the chunk the
    # interruption cuts off. It must keep its old timestamp, or the next run
    # would treat stale stats as freshly enriched and skip it.
    roster = _roster()
    existing = copy.deepcopy(roster[4])
    existing.item_level = 610.0
    row_id = await db.upsert_synced(existing)
    old = "2026-01-01T00:00:00+00:00"
    await db.conn.execute(
        "UPDATE members SET last_synced = ? WHERE id = ?", (old, row_id)
    )
    await db.conn.commit()

    seen_in_db: list[int] = []
    rio, db_ref = _interrupted_rio(roster, seen_in_db)
    db_ref.append(db)
    monkeypatch.setattr(sync, "RaiderIOClient", rio)
    with pytest.raises(asyncio.CancelledError):
        await sync_guild(db, Config())

    after = await db.get_member("Char4", "hellfire", "eu")
    assert after.last_synced == old
    assert after.item_level == 610.0
    assert seen_in_db == [5]  # the four new characters + the existing one
