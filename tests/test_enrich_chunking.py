"""enrich_all processes every member, in bounded chunks (sync hardening)."""

from __future__ import annotations

import asyncio

from ftabot.models import Member
from ftabot.services.raiderio import RaiderIOClient


async def test_enrich_all_enriches_every_member_across_chunks():
    client = RaiderIOClient()
    seen: list[str] = []

    async def fake_enrich(member):
        seen.append(member.character_name)
        member.item_level = 600.0
        return member

    client.enrich_character = fake_enrich  # no network

    members = [Member(f"C{i}") for i in range(7)]
    result = await client.enrich_all(members, concurrency=2, chunk_size=3)

    assert len(result) == 7
    assert sorted(seen) == sorted(f"C{i}" for i in range(7))
    assert all(m.item_level == 600.0 for m in members)


async def test_enrich_all_respects_the_concurrency_cap():
    client = RaiderIOClient()
    in_flight = 0
    peak = 0

    async def fake_enrich(member):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)  # hold the slot briefly
        in_flight -= 1
        return member

    client.enrich_character = fake_enrich

    members = [Member(f"C{i}") for i in range(10)]
    await client.enrich_all(members, concurrency=2, chunk_size=25)

    assert peak <= 2  # never more than the cap in flight at once


async def test_enrich_all_empty_is_safe():
    client = RaiderIOClient()
    assert await client.enrich_all([], concurrency=2) == []
