"""The /recruit officer command group (recruitment board configuration).

Same offline harness as ``test_cogs.py``: invoke each command's callback with a
fake Interaction against the real temp-file ``db`` fixture, then assert on the
stored recruitment state and the ephemeral confirmations.
"""

from __future__ import annotations

import discord
from discord import app_commands

from ftabot.cogs.recruit import RecruitCog

from .test_cogs import _Bot, _cfg, _Interaction, _Perms

_TANK = app_commands.Choice(name="Tank", value="tank")


def _cog(db):
    return RecruitCog(_Bot(db, _cfg()))


def _officer():
    return _Interaction(perms=_Perms(manage_guild=True))


# --- gating -------------------------------------------------------------------


async def test_recruit_commands_are_officer_only(db):
    cog = _cog(db)
    it = _Interaction()  # no perms, no role
    await RecruitCog.slots.callback(cog, it, _TANK, 1)
    assert it.response.ephemeral is True
    assert "Manage Server" in it.response.content
    assert await db.recruitment_state() == {}


# --- slot configuration ---------------------------------------------------------


async def test_slots_sets_open_count(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.slots.callback(cog, it, _TANK, 2)
    assert (await db.recruitment_state())["slots"]["tank"]["open"] == 2
    assert "2" in it.response.content and it.response.ephemeral is True


async def test_slots_zero_marks_role_full(db):
    cog = _cog(db)
    await RecruitCog.slots.callback(cog, _officer(), _TANK, 1)
    it = _officer()
    await RecruitCog.slots.callback(cog, it, _TANK, 0)
    assert (await db.recruitment_state())["slots"]["tank"]["open"] == 0
    assert "full" in it.response.content


async def test_ilvl_sets_and_clears_the_hint(db):
    cog = _cog(db)
    await RecruitCog.ilvl.callback(cog, _officer(), _TANK, 485)
    assert (await db.recruitment_state())["slots"]["tank"]["ilvl"] == 485
    await RecruitCog.ilvl.callback(cog, _officer(), _TANK, 0)
    assert (await db.recruitment_state())["slots"]["tank"]["ilvl"] is None


async def test_note_sets_and_clears(db):
    cog = _cog(db)
    await RecruitCog.note.callback(cog, _officer(), _TANK, "CE-capable")
    assert (await db.recruitment_state())["slots"]["tank"]["note"] == "CE-capable"
    await RecruitCog.note.callback(cog, _officer(), _TANK, "  ")
    assert (await db.recruitment_state())["slots"]["tank"]["note"] is None


# --- trial rules ----------------------------------------------------------------


async def test_rule_add_list_remove_cycle(db):
    cog = _cog(db)
    await RecruitCog.rule_add.callback(cog, _officer(), "Be on time.")
    await RecruitCog.rule_add.callback(cog, _officer(), "Bring food.")

    it = _officer()
    await RecruitCog.rule_list.callback(cog, it)
    assert "**#1** Be on time." in it.response.content
    assert "**#2** Bring food." in it.response.content

    it = _officer()
    await RecruitCog.rule_remove.callback(cog, it, 1)
    assert "Be on time." in it.response.content
    assert (await db.recruitment_state())["rules"] == ["Bring food."]


async def test_rule_add_rejects_blank(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.rule_add.callback(cog, it, "   ")
    assert "empty rule" in it.response.content.lower()
    assert await db.recruitment_state() == {}


async def test_rule_remove_last_rule_warns_about_defaults(db):
    cog = _cog(db)
    await RecruitCog.rule_add.callback(cog, _officer(), "Only rule.")
    it = _officer()
    await RecruitCog.rule_remove.callback(cog, it, 1)
    assert "bundled default" in it.response.content
    assert (await db.recruitment_state())["rules"] == []


async def test_rule_remove_out_of_range(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.rule_remove.callback(cog, it, 3)
    assert "No rule **#3**" in it.response.content


async def test_rule_list_empty_points_at_defaults(db):
    cog = _cog(db)
    it = _officer()
    await RecruitCog.rule_list.callback(cog, it)
    assert "bundled defaults" in it.response.content


# --- pitch override + preview + reset --------------------------------------------


async def test_message_overrides_and_restores(db):
    cog = _cog(db)
    await RecruitCog.message.callback(cog, _officer(), "Join the fire.")
    assert (await db.recruitment_state())["message"] == "Join the fire."
    it = _officer()
    await RecruitCog.message.callback(cog, it, None)
    assert (await db.recruitment_state())["message"] is None
    assert "restored" in it.response.content


async def test_show_previews_the_board_embed(db):
    cog = _cog(db)
    await RecruitCog.slots.callback(cog, _officer(), _TANK, 1)
    it = _officer()
    await RecruitCog.show.callback(cog, it)
    # Render scans the roster, so the command defers and answers via followup.
    assert it.response.deferred is True
    assert isinstance(it.followup.embed, discord.Embed)
    assert it.followup.ephemeral is True
    assert "TANK" in it.followup.embed.description


async def test_reset_clears_everything(db):
    cog = _cog(db)
    await RecruitCog.slots.callback(cog, _officer(), _TANK, 1)
    await RecruitCog.rule_add.callback(cog, _officer(), "A rule.")
    it = _officer()
    await RecruitCog.reset.callback(cog, it)
    assert await db.recruitment_state() == {}
    assert "reset" in it.response.content.lower()
