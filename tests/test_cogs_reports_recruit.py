"""Populated/happy-path coverage for the ``reports`` and ``recruit`` cogs.

``test_cogs.py`` already covers the empty-state and denial branches of these
two cogs; their *populated* command bodies (raidcheck, the loot/wishlist/
lootpriority/trends render paths, and the scout embed builder) were the part
still uncovered. Same offline harness — invoke the command callback against the
real temp-file ``db`` fixture, seeding loot/wishlist/snapshots as needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from ftabot.cogs.recruit import RecruitCog
from ftabot.cogs.reports import ReportsCog, _fmt
from ftabot.models import LootEntry, WishlistItem

from .test_cogs import _Bot, _cfg, _Interaction, _Perms, _seed, _team_caller

# --- reports cog: populated render paths -------------------------------------


async def test_raidcheck_renders_board(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.raidcheck.callback(cog, it, team=None)
    assert it.response.deferred is True
    assert isinstance(it.followup.embed, discord.Embed)
    # Both seeded raiders show up on the readiness board.
    assert "Ashbringer" in it.followup.embed.description
    assert "Emberlyn" in it.followup.embed.description


async def test_raidcheck_with_unknown_team_is_empty(db):
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.raidcheck.callback(cog, it, team="Nonexistent")
    assert "No raiders tracked yet" in it.followup.content


async def test_loot_for_a_player_with_history(db):
    await _seed(db)
    await db.add_loot(
        [
            LootEntry("Ashbringer", item_id=1, item_name="Truthguard",
                      response="BiS", boss="Council", awarded_at="2026-05-01T20:00:00+00:00"),
        ]
    )
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.loot.callback(cog, it, player="Ashbringer")
    assert isinstance(it.followup.embed, discord.Embed)
    assert "Truthguard" in it.followup.embed.description


async def test_loot_fairness_overview(db):
    await _seed(db)
    await db.add_loot(
        [
            LootEntry("Ashbringer", item_id=1, awarded_at="2026-05-01T20:00:00+00:00"),
            LootEntry("Ashbringer", item_id=2, awarded_at="2026-05-08T20:00:00+00:00"),
            LootEntry("Emberlyn", item_id=3, awarded_at="2026-05-08T20:00:00+00:00"),
        ]
    )
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.loot.callback(cog, it, player=None)  # no player => overview
    assert isinstance(it.followup.embed, discord.Embed)
    assert "fairness" in it.followup.embed.title
    assert "Ashbringer" in it.followup.embed.description


async def test_wishlist_with_items(db):
    await _seed(db)
    await db.replace_wishlist(
        "Ashbringer",
        [
            WishlistItem("Ashbringer", item_id=7, item_name="Axe of the Pyre",
                         is_bis=True, upgrade_pct=8.0, obtained=False, boss="Boss"),
            WishlistItem("Ashbringer", item_id=8, item_name="Ember Ring",
                         obtained=True, boss="Boss"),
        ],
    )
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.wishlist.callback(cog, it, player="Ashbringer")
    assert isinstance(it.followup.embed, discord.Embed)
    assert "Axe of the Pyre" in it.followup.embed.description
    assert "BiS" in it.followup.embed.description


async def test_lootpriority_with_candidates(db):
    await _seed(db)
    await db.replace_wishlist(
        "Ashbringer",
        [WishlistItem("Ashbringer", item_id=99, item_name="Shiny Trinket",
                      is_bis=True, upgrade_pct=6.0)],
    )
    await db.replace_wishlist(
        "Emberlyn",
        [WishlistItem("Emberlyn", item_id=99, item_name="Shiny Trinket",
                      is_bis=False, upgrade_pct=2.0)],
    )
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.lootpriority.callback(cog, it, item="Shiny")
    assert isinstance(it.followup.embed, discord.Embed)
    assert "Ashbringer" in it.followup.embed.description
    assert "Advisory only" in it.followup.embed.footer.text


async def test_trends_with_history(db):
    await db.add_snapshot(
        dict(member_count=10, active_count=8, health_score=70,
             avg_item_level=620, avg_mythic_plus=2400)
    )
    await db.add_snapshot(
        dict(member_count=12, active_count=11, health_score=78,
             avg_item_level=628, avg_mythic_plus=2600)
    )
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=42)))
    it = _Interaction()  # default user id 42 == GM
    await ReportsCog.trends.callback(cog, it)
    assert isinstance(it.followup.embed, discord.Embed)
    assert "Members" in it.followup.embed.description
    assert "▲" in it.followup.embed.description  # an upward delta is annotated


async def test_trends_skips_fields_with_no_data(db):
    """A trend field that is all-None across snapshots is skipped (line 260)."""
    # avg_mythic_plus omitted in both snapshots -> its series is all None.
    await db.add_snapshot(
        dict(member_count=10, active_count=8, health_score=70, avg_item_level=620)
    )
    await db.add_snapshot(
        dict(member_count=12, active_count=11, health_score=78, avg_item_level=628)
    )
    cog = ReportsCog(_Bot(db, _cfg(gm_discord_id=42)))
    it = _Interaction()  # default user id 42 == GM
    await ReportsCog.trends.callback(cog, it)
    assert isinstance(it.followup.embed, discord.Embed)
    # The populated fields render; the empty Avg M+ field is omitted entirely.
    assert "Members" in it.followup.embed.description
    assert "Avg M+" not in it.followup.embed.description


async def test_wishlist_require_team_member_denies(db):
    """A caller with no team character is turned away from /wishlist (line 170)."""
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()  # not linked to any team character
    await ReportsCog.wishlist.callback(cog, it, player="Ashbringer")
    assert it.response.ephemeral is True
    assert "team roster" in it.response.content


async def test_lootpriority_require_team_member_denies(db):
    """A non-team caller is turned away from /lootpriority (line 206)."""
    await _seed(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()  # not linked to any team character
    await ReportsCog.lootpriority.callback(cog, it, item="Shiny")
    assert it.response.ephemeral is True
    assert "team roster" in it.response.content


async def test_lootpriority_shows_recent_wins_tag(db):
    """A candidate with a recent loot win gets the 'recent win(s)' tag (line 223)."""
    await _seed(db)
    await db.replace_wishlist(
        "Ashbringer",
        [WishlistItem("Ashbringer", item_id=99, item_name="Shiny Trinket",
                      is_bis=True, upgrade_pct=6.0)],
    )
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    await db.add_loot(
        [LootEntry("Ashbringer", item_id=5, item_name="Old item", awarded_at=recent)]
    )
    await _team_caller(db)
    cog = ReportsCog(_Bot(db, _cfg()))
    it = _Interaction()
    await ReportsCog.lootpriority.callback(cog, it, item="Shiny")
    assert isinstance(it.followup.embed, discord.Embed)
    assert "recent win" in it.followup.embed.description


def test_fmt_handles_non_float_values():
    """The _fmt fallback renders ints/strings via str() (line 279)."""
    assert _fmt(5) == "5"
    assert _fmt("n/a") == "n/a"
    # The float branch (line 278) for completeness alongside the fallback.
    assert _fmt(3.0) == "3"
    assert _fmt(3.5) == "3.5"
    assert _fmt(None) == "–"


# --- recruit cog: scout embed builder ---------------------------------------


def _slot_report():
    """A SlotReport with one strong candidate, built from the real dataclasses so
    the cog's embed builder (_slot_embed/_match_title/_match_body) runs for real."""
    from ftabot import recruit, scout

    cand = recruit.Candidate(
        name="Newflame",
        realm="hellfire",
        region="eu",
        class_name="Paladin",
        spec="Retribution",
        role="DPS",
        item_level=640,
        parse_seasons=[80.0, 85.0, 90.0],
        mplus_seasons=[2500.0, 2700.0, 2900.0],
        guild_name="Rival Guild",
        lfg_active=True,
    )
    match = recruit.ScoutMatch(
        candidate=cand,
        score=92.0,
        upgrade_delta=0.2,
        poachability=0.8,
        parse_trajectory=recruit.trajectory(cand.parse_seasons),
        mplus_trajectory=recruit.trajectory(cand.mplus_seasons),
        flags=["🔥 climbing"],
    )
    slot = recruit.WeakSlot(
        class_name="Paladin", spec="Retribution", role="DPS",
        incumbent="Ashbringer", incumbent_strength=0.3,
    )
    return scout.SlotReport(slot=slot, matches=[match])


async def test_scout_renders_candidate_embeds(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _reports(database, config, *, only_class=None):
        return [_slot_report()]

    monkeypatch.setattr(scout_mod, "run_scout", _reports)
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)

    assert it.followup.embeds is not None
    assert len(it.followup.embeds) == 1
    embed = it.followup.embeds[0]
    assert "Paladin" in embed.title
    assert "Newflame" in embed.fields[0].name  # _match_title rendered


async def test_scout_reports_no_clear_upgrades(db, monkeypatch):
    from ftabot import recruit, scout
    from ftabot import scout as scout_mod

    async def _reports(database, config, *, only_class=None):
        # A weak slot, but no matches => _slot_embed returns None for it.
        slot = recruit.WeakSlot(
            class_name="Paladin", spec=None, role="DPS",
            incumbent="Ashbringer", incumbent_strength=0.3,
        )
        return [scout.SlotReport(slot=slot, matches=[])]

    monkeypatch.setattr(scout_mod, "run_scout", _reports)
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "no clear upgrades" in it.followup.content


async def test_scout_surfaces_unexpected_error(db, monkeypatch):
    from ftabot import scout as scout_mod

    async def _boom(database, config, *, only_class=None):
        raise ValueError("raider.io exploded")

    monkeypatch.setattr(scout_mod, "run_scout", _boom)
    cog = RecruitCog(_Bot(db, _cfg()))
    it = _Interaction(perms=_Perms(manage_guild=True))
    await RecruitCog.scout_cmd.callback(cog, it, wow_class=None)
    assert it.followup.ephemeral is True
    assert "Scout failed" in it.followup.content
