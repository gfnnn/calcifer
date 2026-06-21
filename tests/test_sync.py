import dataclasses

from ftabot import sync as sync_mod
from ftabot.config import Config
from ftabot.models import Member
from ftabot.sync import SyncResult, _build_snapshot, _parse_iso, sync_guild


def test_build_snapshot_computes_guild_metrics():
    members = [
        Member("A", role="TANK", item_level=630, mythic_plus_score=3000,
               raid_progression="Palace: 8/8 N, 2/8 M",
               last_login="2026-06-01T00:00:00+00:00"),
        Member("B", role="HEALER", item_level=620, mythic_plus_score=2800,
               last_login="2026-06-01T00:00:00+00:00"),
        Member("C", role="DPS", item_level=640, mythic_plus_score=3200),
    ]
    snap = _build_snapshot(members, Config())
    assert snap["member_count"] == 3
    assert snap["avg_item_level"] == (630 + 620 + 640) / 3
    assert snap["progression_summary"] == "Palace: 8/8 N, 2/8 M"
    assert snap["role_counts"] == {"TANK": 1, "HEALER": 1, "DPS": 1}
    assert snap["health_score"] is not None
    assert snap["taken_at"] is None  # filled in by the DB layer


def test_build_snapshot_health_includes_retention_with_prev_count():
    # The stored snapshot must factor the retention component (member-count
    # change vs the previous snapshot) so it is computed on the same basis as the
    # live GM view; otherwise the GM page's health delta compares unlike scores.
    members = [
        Member("A", role="TANK", item_level=630, mythic_plus_score=3000),
        Member("B", role="HEALER", item_level=620, mythic_plus_score=2800),
        Member("C", role="DPS", item_level=640, mythic_plus_score=3200),
    ]
    without = _build_snapshot(members, Config())["health_score"]
    grown = _build_snapshot(members, Config(), prev_member_count=1)["health_score"]
    # Adding the retention component (strong growth here) shifts the weighted score.
    assert grown != without


# --- SyncResult.summary -----------------------------------------------------


def test_sync_result_summary_includes_raid_team_and_health_extras():
    result = SyncResult(
        fetched=10, upserted=10, pruned=1, enriched=8,
        raiders=5, wishlist_items=12, attendance_events=3,
        health_score=72.0, sources=("Raider.IO", "WoW Audit"),
    )
    text = result.summary()
    assert "5 raider(s)" in text
    assert "12 wishlist item(s)" in text
    assert "3 raid night(s)" in text
    assert "Guild health: 72/100." in text


def test_sync_result_summary_reports_an_error():
    assert SyncResult(error="boom").summary() == "Sync failed: boom"


# --- _parse_iso -------------------------------------------------------------


def test_parse_iso_none_and_garbage_return_none():
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("not-a-date") is None


def test_parse_iso_fills_utc_for_naive_timestamps():
    dt = _parse_iso("2026-06-01T00:00:00")
    assert dt is not None and dt.tzinfo is not None


# --- sync_guild end-to-end (offline, fake service clients) ------------------


class _CM:
    """An async-context-manager wrapper around a fake service client."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


class _FakeRaiderIO:
    """Returns a fixed roster; enrich_all raises to hit the best-effort branch."""

    def __init__(self, members, *, enrich_raises=False):
        self._members = members
        self._enrich_raises = enrich_raises

    async def guild_members(self, name, realm, region):
        return list(self._members)

    async def enrich_all(self, members, *, concurrency=2):
        if self._enrich_raises:
            from ftabot.services.raiderio import RaiderIOError

            raise RaiderIOError("enrich boom")


class _FakeWowAudit:
    def __init__(self, members):
        self._members = members

    async def team_name(self):
        return "Cinder"

    async def characters(self):
        return list(self._members)

    async def vault_slots(self):
        return {}

    async def wishlists(self):
        return []

    async def recent_attendance(self):
        return []

    async def upcoming_raid_signups(self, *, tz=None):
        return None


class _BoomClient:
    """A client whose every awaited method raises (best-effort branch)."""

    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, _name):
        async def _raise(*a, **kw):
            raise RuntimeError("integration down")

        return _raise


def _sync_cfg(**over):
    base = dict(
        guild_name="From the Ashes", guild_realm="hellfire", guild_region="eu",
        wowaudit_api_key="wa-key",
        blizzard_client_id="bz", blizzard_client_secret="bz-secret",
        warcraftlogs_client_id="wcl", warcraftlogs_client_secret="wcl-secret",
    )
    base.update(over)
    return Config(**base)


async def test_sync_guild_adds_cross_realm_raider_and_survives_failures(db, monkeypatch):
    # Raider.IO roster has one member; WoW Audit adds a second raider who is NOT
    # on the Raider.IO roster (the cross-realm append branch). Every enrichment
    # client raises, exercising the three best-effort skip branches.
    roster = [Member("Ashbringer", class_name="Paladin", role="TANK")]
    wa_only = [Member("Crossrealm", class_name="Mage", role="DPS")]

    monkeypatch.setattr(
        sync_mod, "RaiderIOClient",
        lambda *a, **kw: _CM(_FakeRaiderIO(roster, enrich_raises=True)),
    )
    monkeypatch.setattr(
        sync_mod, "WowAuditClient",
        lambda *a, **kw: _CM(_FakeWowAudit(wa_only)),
    )
    monkeypatch.setattr(sync_mod, "BlizzardClient", lambda *a, **kw: _CM(_BoomClient()))
    monkeypatch.setattr(
        sync_mod, "WarcraftLogsClient", lambda *a, **kw: _CM(_BoomClient())
    )

    result = await sync_guild(db, _sync_cfg())

    assert result.error is None
    # Both the roster member and the cross-realm raider were upserted.
    assert result.fetched == 2
    names = {m.character_name for m in await db.all_members()}
    assert {"Ashbringer", "Crossrealm"} <= names
    # WoW Audit ran (team tag applied); the failed enrich/Blizzard/WCL steps were
    # swallowed, so Raider.IO + WoW Audit are the only recorded sources.
    assert set(result.sources) == {"Raider.IO", "WoW Audit"}
    # The cross-realm raider (WoW Audit only) was appended to the roster and
    # persisted, proving the append branch ran.
    assert await db.get_member("Crossrealm", "hellfire", "eu") is not None


async def test_sync_guild_canonicalises_role_from_spec(db, monkeypatch):
    # A source leaves a healer's role empty and spells a tank's role its own way;
    # the persist step stores both canonically, so the web filter, raid grouping
    # and role counts (which read the stored column) all agree.
    roster = [
        Member("Resto", class_name="Druid", active_spec="Restoration", role=None),
        Member("Tanky", class_name="Warrior", active_spec="Protection", role="Tank"),
    ]
    monkeypatch.setattr(
        sync_mod, "RaiderIOClient", lambda *a, **kw: _CM(_FakeRaiderIO(roster))
    )
    cfg = _sync_cfg(
        wowaudit_api_key=None, blizzard_client_id=None, warcraftlogs_client_id=None
    )
    result = await sync_guild(db, cfg)

    assert result.error is None
    assert (await db.get_member("Resto", "hellfire", "eu")).role == "HEALER"
    assert (await db.get_member("Tanky", "hellfire", "eu")).role == "TANK"


async def test_sync_guild_skips_prune_on_empty_roster(db, monkeypatch):
    # An existing member must survive a fetch that returns nothing (the empty
    # roster guard skips the prune so manual data is not wiped).
    await db.upsert_synced(Member("Keepme", class_name="Druid"))

    monkeypatch.setattr(
        sync_mod, "RaiderIOClient", lambda *a, **kw: _CM(_FakeRaiderIO([]))
    )
    cfg = dataclasses.replace(Config(guild_name="From the Ashes"))
    result = await sync_guild(db, cfg)

    assert result.fetched == 0 and result.pruned == 0
    assert await db.get_member("Keepme", "hellfire", "eu") is not None
