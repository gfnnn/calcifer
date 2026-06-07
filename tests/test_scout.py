"""End-to-end orchestration test for the recruitment scout (offline).

Seeds a real (temp) roster, fakes the WCL/Raider.IO clients, and checks the
gap-analysis → market-scan → rank flow.
"""

import dataclasses

import pytest

from ftabot import scout
from ftabot.config import Config
from ftabot.models import Member

# --- fake service clients ---------------------------------------------------


class _FakeWCL:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def zone_encounters(self, zone_id):
        return [{"id": 100, "name": "First"}, {"id": 200, "name": "End Boss"}]

    async def character_rankings(self, encounter_id, **kwargs):
        # Two Paladins discovered: one external climber, one already in our guild.
        return [
            {"name": "Climber", "realm_slug": "ravencrest", "region": "eu",
             "class_name": "Paladin", "spec": "Holy", "guild_name": "Casual Embers"},
            {"name": "Ourpal", "realm_slug": "hellfire", "region": "eu",
             "class_name": "Paladin", "spec": "Holy", "guild_name": "From the Ashes"},
        ]

    async def parse_curve(self, name, realm, region, zone_ids):
        return {"Climber": [78.0, 84.0, 90.0]}.get(name, [None, None, None])


class _FakeRIO:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def guild_strength(self, name, realm, region):
        return None  # unknown → neutral poachability

    async def character_card(self, name, realm, region, seasons):
        cards = {
            "Climber": {
                "name": "Climber", "realm_slug": "ravencrest", "region": "eu",
                "class_name": "Paladin", "active_spec": "Holy", "role": "HEALER",
                "item_level": 289.0, "guild_name": "Casual Embers",
                "mplus_seasons": [2500, 2750, 3000], "lfg_active": False,
            },
        }
        return cards.get(name)


@pytest.fixture
def scout_config():
    return dataclasses.replace(
        Config(),
        warcraftlogs_client_id="id",
        warcraftlogs_client_secret="secret",
        warcraftlogs_zone_ids=(10, 20, 30),
        recruit_seasons=("s1", "s2", "s3"),
        recruit_min_item_level=275.0,
    )


async def _seed(db):
    # A weak Paladin (our incumbent, bottom of the team) plus several strong
    # raiders so the Paladin lands in the weak quartile and the strong classes
    # are never scouted.
    await db.upsert_synced(Member(
        character_name="Ourpal", class_name="Paladin", active_spec="Holy",
        role="HEALER", best_parse_avg=45.0, mythic_plus_score=1900.0, item_level=277.0,
    ))
    for name, klass, spec, parse, mplus, ilvl in [
        ("Goodek", "Death Knight", "Frost", 95.0, 3300.0, 294.0),
        ("Goodmage", "Mage", "Frost", 92.0, 3200.0, 293.0),
        ("Goodpriest", "Priest", "Shadow", 90.0, 3100.0, 292.0),
    ]:
        await db.upsert_synced(Member(
            character_name=name, class_name=klass, active_spec=spec,
            role="DPS", best_parse_avg=parse, mythic_plus_score=mplus, item_level=ilvl,
        ))


async def test_scout_finds_external_upgrade_and_skips_strong_class(db, scout_config, monkeypatch):
    monkeypatch.setattr(scout, "WarcraftLogsClient", _FakeWCL)
    monkeypatch.setattr(scout, "RaiderIOClient", _FakeRIO)
    await _seed(db)

    reports = await scout.run_scout(db, scout_config)

    # The weak Paladin is scouted; the strong DK class is not.
    scouted_classes = {r.slot.class_name for r in reports}
    assert "Paladin" in scouted_classes
    assert "Death Knight" not in scouted_classes

    pal = next(r for r in reports if r.slot.class_name == "Paladin")
    names = [m.candidate.name for m in pal.matches]
    assert "Climber" in names  # external upgrade surfaced
    assert "Ourpal" not in names  # our own guild member filtered out
    assert pal.matches[0].upgrade_delta > 0


async def test_scout_requires_credentials(db):
    await _seed(db)
    with pytest.raises(scout.ScoutUnavailable):
        await scout.run_scout(db, Config())  # no WCL creds


async def test_scout_only_class_for_unrostered(db, scout_config, monkeypatch):
    monkeypatch.setattr(scout, "WarcraftLogsClient", _FakeWCL)
    monkeypatch.setattr(scout, "RaiderIOClient", _FakeRIO)
    await _seed(db)

    reports = await scout.run_scout(db, scout_config, only_class="paladin")
    assert len(reports) == 1
    assert reports[0].slot.class_name == "Paladin"


def test_config_parses_scout_env(monkeypatch):
    monkeypatch.setenv("WARCRAFTLOGS_ZONE_IDS", "10, 20 ,30")
    monkeypatch.setenv("RECRUIT_MIN_ITEM_LEVEL", "280")
    monkeypatch.setenv("RECRUIT_ROLES_OPEN", "HEALER,DPS")
    monkeypatch.setenv("RECRUIT_SEASONS", "season-tww-2,season-tww-3")
    cfg = Config.from_env()
    assert cfg.warcraftlogs_zone_ids == (10, 20, 30)
    assert cfg.recruit_min_item_level == 280.0
    assert cfg.recruit_roles_open == ("HEALER", "DPS")
    assert cfg.recruit_seasons == ("season-tww-2", "season-tww-3")
    assert cfg.scout_zone_ids == (10, 20, 30)


def test_sample_encounters_picks_spread():
    from ftabot.scout import _sample_encounters

    # Short tier: return all.
    assert _sample_encounters([{"id": 1}, {"id": 2}]) == [1, 2]
    # 3 exact: return all.
    assert _sample_encounters([{"id": 1}, {"id": 2}, {"id": 3}]) == [1, 2, 3]
    # 5 encounters: first (1), mid (3), last (5) — no duplicates.
    assert _sample_encounters(
        [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}]
    ) == [1, 3, 5]
    # Entry with no id is skipped.
    assert _sample_encounters([{"id": 1}, {}, {"id": 3}]) == [1, 3]


async def test_scout_queries_multiple_encounters(db, scout_config, monkeypatch):
    """run_scout must call character_rankings once per sampled encounter."""
    queried_encounters: list[int] = []

    class _TrackingWCL(_FakeWCL):
        async def character_rankings(self, encounter_id, **kwargs):
            queried_encounters.append(encounter_id)
            return await super().character_rankings(encounter_id, **kwargs)

    monkeypatch.setattr(scout, "WarcraftLogsClient", _TrackingWCL)
    monkeypatch.setattr(scout, "RaiderIOClient", _FakeRIO)
    await _seed(db)

    await scout.run_scout(db, scout_config)

    # Both encounters (100 and 200) must have been queried for the weak slot.
    assert 100 in queried_encounters
    assert 200 in queried_encounters


async def test_scout_lfg_candidate_ranks_highest(db, scout_config, monkeypatch):
    """An LFG-active candidate with equal stats should outrank a non-LFG one."""

    class _LFGRio(_FakeRIO):
        async def character_card(self, name, realm, region, seasons):
            base = {
                "realm_slug": "ravencrest", "region": "eu",
                "class_name": "Paladin", "active_spec": "Holy", "role": "HEALER",
                "item_level": 289.0, "guild_name": "Some Guild",
                "mplus_seasons": [2500, 2750, 3000],
            }
            if name == "LFGpal":
                return {"name": "LFGpal", **base, "lfg_active": True}
            if name == "Normalpal":
                return {"name": "Normalpal", **base, "lfg_active": False}
            return None

    class _MultiWCL(_FakeWCL):
        async def character_rankings(self, encounter_id, **kwargs):
            return [
                {"name": "LFGpal", "realm_slug": "ravencrest", "region": "eu",
                 "class_name": "Paladin", "spec": "Holy", "guild_name": "Some Guild"},
                {"name": "Normalpal", "realm_slug": "ravencrest", "region": "eu",
                 "class_name": "Paladin", "spec": "Holy", "guild_name": "Some Guild"},
            ]

        async def parse_curve(self, name, realm, region, zone_ids):
            return [78.0, 84.0, 90.0]

    monkeypatch.setattr(scout, "WarcraftLogsClient", _MultiWCL)
    monkeypatch.setattr(scout, "RaiderIOClient", _LFGRio)
    await _seed(db)

    reports = await scout.run_scout(db, scout_config)
    pal = next(r for r in reports if r.slot.class_name == "Paladin")
    top = pal.matches[0]
    assert top.candidate.name == "LFGpal"
    assert top.poachability == 1.0
    assert any("LFG" in f for f in top.flags)
