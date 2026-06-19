"""Recruitment lead funnel: the scout_leads table, scout.lead_dicts (pure), and
the /recruit lead officer commands. Offline, using the real temp-file db fixture
and the same fake-Interaction harness as the other cog tests."""

from __future__ import annotations

import discord
from discord import app_commands

from ftabot import recruit, scout
from ftabot.cogs.recruit import RecruitCog

from .test_cogs import _Bot, _cfg, _Interaction, _Perms

# --- db: upsert ownership split ---------------------------------------------


async def test_upsert_lead_refreshes_discovered_but_keeps_officer_fields(db):
    lid = await db.upsert_lead(
        {"name": "Newpal", "realm": "hellfire", "region": "eu",
         "class_name": "Paladin", "score": 80, "item_level": 620, "lfg": True}
    )
    await db.set_lead_state(lid, "contacted")
    await db.set_lead_note(lid, "DM sent")

    # A re-scout brings new discovered values for the same character.
    lid2 = await db.upsert_lead(
        {"name": "Newpal", "realm": "hellfire", "region": "eu",
         "class_name": "Paladin", "score": 90, "item_level": 625, "lfg": False}
    )
    assert lid2 == lid  # same lead, updated in place
    lead = await db.get_lead(lid)
    assert lead["score"] == 90 and lead["item_level"] == 625  # discovered refreshed
    assert lead["lfg"] is False
    assert lead["state"] == "contacted"  # officer-owned state preserved
    assert lead["notes"] == "DM sent"    # officer-owned note preserved


async def test_leads_ranks_new_first_then_by_score(db):
    await db.upsert_lead({"name": "A", "realm": "r", "region": "eu", "score": 50})
    b = await db.upsert_lead({"name": "B", "realm": "r", "region": "eu", "score": 99})
    await db.set_lead_state(b, "contacted")  # no longer 'new', so it sinks
    await db.upsert_lead({"name": "C", "realm": "r", "region": "eu", "score": 70})
    assert [r["name"] for r in await db.leads()] == ["C", "A", "B"]


async def test_leads_filter_by_state(db):
    a = await db.upsert_lead({"name": "A", "realm": "r", "region": "eu"})
    await db.set_lead_state(a, "trialing")
    await db.upsert_lead({"name": "B", "realm": "r", "region": "eu"})
    assert [r["name"] for r in await db.leads(state="trialing")] == ["A"]


async def test_lead_mutators_return_false_for_unknown_id(db):
    assert await db.set_lead_state(999, "contacted") is False
    assert await db.set_lead_note(999, "x") is False
    assert await db.delete_lead(999) is False


async def test_delete_lead(db):
    lid = await db.upsert_lead({"name": "A", "realm": "r", "region": "eu"})
    assert await db.delete_lead(lid) is True
    assert await db.get_lead(lid) is None


# --- scout.lead_dicts (pure) ------------------------------------------------


def _report():
    cand = recruit.Candidate(
        name="Newflame", realm="hellfire", region="eu", class_name="Paladin",
        spec="Retribution", role="DPS", item_level=640, lfg_active=True,
        guild_name="Rival",
    )
    match = recruit.ScoutMatch(
        candidate=cand, score=92.0, upgrade_delta=0.2, poachability=0.8,
        parse_trajectory=recruit.trajectory([90.0]),
        mplus_trajectory=recruit.trajectory([3000.0]), flags=[],
    )
    slot = recruit.WeakSlot(
        class_name="Paladin", spec="Retribution", role="DPS",
        incumbent="Ashbringer", incumbent_strength=0.3,
    )
    return scout.SlotReport(slot=slot, matches=[match])


def test_lead_dicts_flattens_matches():
    (lead,) = scout.lead_dicts([_report()])
    assert lead["name"] == "Newflame"
    assert lead["score"] == 92.0
    assert lead["lfg"] is True
    assert lead["source"] == "scout"


def test_lead_dicts_skips_slots_without_matches():
    slot = recruit.WeakSlot(
        class_name="Mage", spec=None, role="DPS", incumbent="x", incumbent_strength=0.3
    )
    assert scout.lead_dicts([scout.SlotReport(slot=slot, matches=[])]) == []


# --- cog: /recruit lead -----------------------------------------------------


def _cog(db):
    return RecruitCog(_Bot(db, _cfg()))


def _officer():
    return _Interaction(perms=_Perms(manage_guild=True))


async def test_lead_capture_persists_scouted_leads(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _reports(database, config, *, only_class=None):
        return [_report()]

    monkeypatch.setattr(scout_mod, "run_scout", _reports)
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_capture.callback(cog, it, wow_class=None)
    assert any(r["name"] == "Newflame" for r in await db.leads())
    assert "Captured" in it.followup.content


async def test_lead_capture_is_officer_only(db):
    cog = _cog(db)
    it = _Interaction()  # no perms
    await RecruitCog.lead_capture.callback(cog, it, wow_class=None)
    assert await db.leads() == []
    assert "Manage Server" in it.response.content


async def test_lead_list_set_note_remove_cycle(db):
    lid = await db.upsert_lead(
        {"name": "Newflame", "realm": "hellfire", "region": "eu",
         "class_name": "Paladin", "score": 92, "item_level": 640, "lfg": True}
    )
    cog = _cog(db)

    it = _officer()
    await RecruitCog.lead_list.callback(cog, it, state=None)
    assert isinstance(it.response.embed, discord.Embed)
    assert "Newflame" in it.response.embed.description

    it = _officer()
    await RecruitCog.lead_set.callback(
        cog, it, lid, app_commands.Choice(name="Contacted", value="contacted")
    )
    assert (await db.get_lead(lid))["state"] == "contacted"

    it = _officer()
    await RecruitCog.lead_note.callback(cog, it, lid, "trial Tuesday")
    assert (await db.get_lead(lid))["notes"] == "trial Tuesday"

    it = _officer()
    await RecruitCog.lead_remove.callback(cog, it, lid)
    assert await db.get_lead(lid) is None


async def test_lead_set_unknown_id(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_set.callback(
        cog, it, 999, app_commands.Choice(name="Contacted", value="contacted")
    )
    assert "No lead" in it.response.content


async def test_lead_list_empty(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_list.callback(cog, it, state=None)
    assert "No leads" in it.response.content


async def test_lead_list_empty_scoped_by_state(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_list.callback(
        cog, it, state=app_commands.Choice(name="Trialing", value="trialing")
    )
    assert "trialing" in it.response.content  # the scope note names the state


# --- lead-funnel officer gating (the _guard early returns) ------------------


async def test_every_lead_command_is_officer_gated(db):
    """The list/set/note/remove lead commands all bail for a plain user."""
    lid = await db.upsert_lead({"name": "A", "realm": "r", "region": "eu"})
    cog = _cog(db)

    async def _denied(coro):
        it = _Interaction()  # no perms
        await coro(it)
        assert it.response.ephemeral is True
        assert "Manage Server" in it.response.content

    await _denied(lambda it: RecruitCog.lead_list.callback(cog, it, state=None))
    await _denied(
        lambda it: RecruitCog.lead_set.callback(
            cog, it, lid, app_commands.Choice(name="Contacted", value="contacted")
        )
    )
    await _denied(lambda it: RecruitCog.lead_note.callback(cog, it, lid, "x"))
    await _denied(lambda it: RecruitCog.lead_remove.callback(cog, it, lid))

    # Nothing changed: state still 'new', no note, lead still present.
    lead = await db.get_lead(lid)
    assert lead is not None and lead["state"] == "new" and lead["notes"] is None


# --- lead note / remove edge branches ---------------------------------------


async def test_lead_note_clears_when_blank(db):
    lid = await db.upsert_lead({"name": "A", "realm": "r", "region": "eu"})
    await db.set_lead_note(lid, "old note")
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_note.callback(cog, it, lid, "   ")
    assert "cleared" in it.response.content.lower()
    assert (await db.get_lead(lid))["notes"] is None


async def test_lead_note_unknown_id(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_note.callback(cog, it, 999, "x")
    assert "No lead" in it.response.content


async def test_lead_remove_unknown_id(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_remove.callback(cog, it, 999)
    assert "No lead" in it.response.content
    assert "remove" in it.response.content.lower()


# --- lead capture: scout failures and the no-leads result -------------------


async def test_lead_capture_handles_scout_unavailable(db, monkeypatch):
    from ftabot import scout as scout_mod
    from ftabot.scout import ScoutUnavailable

    async def _boom(database, config, *, only_class=None):
        raise ScoutUnavailable("no WCL credentials")

    monkeypatch.setattr(scout_mod, "run_scout", _boom)
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_capture.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "Can't scout" in it.followup.content
    assert await db.leads() == []


async def test_lead_capture_surfaces_unexpected_error(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _boom(database, config, *, only_class=None):
        raise ValueError("raider.io exploded")

    monkeypatch.setattr(scout_mod, "run_scout", _boom)
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_capture.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "Scout failed" in it.followup.content


async def test_lead_capture_reports_when_no_leads_worth_tracking(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _no_matches(database, config, *, only_class=None):
        # A slot with no matches -> lead_dicts yields nothing to track.
        slot = recruit.WeakSlot(
            class_name="Mage", spec=None, role="DPS",
            incumbent="x", incumbent_strength=0.3,
        )
        return [scout.SlotReport(slot=slot, matches=[])]

    monkeypatch.setattr(scout_mod, "run_scout", _no_matches)
    cog = _cog(db)
    it = _officer()
    await RecruitCog.lead_capture.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "no upgrades worth tracking" in it.followup.content
    assert await db.leads() == []
