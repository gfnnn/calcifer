"""Tests for the recruitment scout scoring core (pure, no network)."""

import pytest

from ftabot import recruit
from ftabot.models import Member

# --- trajectory -------------------------------------------------------------


def test_trajectory_improving_is_detected():
    t = recruit.trajectory([2100, 2600, 2900])
    assert t.seasons == 3
    assert t.latest == 2900
    assert t.slope > 0
    assert t.trend == "improving"
    assert t.spike is False


def test_trajectory_declining_is_detected():
    t = recruit.trajectory([95, 80, 60])
    assert t.trend == "declining"
    assert t.slope < 0


def test_trajectory_single_season_is_a_spike():
    t = recruit.trajectory([99])
    assert t.seasons == 1
    assert t.spike is True
    assert t.trend == "unknown"


def test_trajectory_no_data_is_unknown():
    # All-None (no surviving seasons) -> the empty Trajectory.
    t = recruit.trajectory([None, None])
    assert t.seasons == 0
    assert t.latest is None
    assert t.slope == 0.0
    assert t.consistency == 0.0
    assert t.trend == "unknown"
    assert t.spike is False


def test_trajectory_drops_missing_seasons_but_counts_present():
    t = recruit.trajectory([None, 2000, None, 2400])
    assert t.seasons == 2
    assert t.latest == 2400


def test_consistency_higher_for_steady_series():
    steady = recruit.trajectory([80, 81, 82, 80])
    erratic = recruit.trajectory([20, 95, 30, 90])
    assert steady.consistency > erratic.consistency


def test_trajectory_phrase_reads_naturally():
    phrase = recruit.trajectory_phrase("M+", [2100, 2600, 2900])
    assert phrase == "M+ 2.1k → 2.6k → 2.9k over 3 season(s), improving"


def test_trajectory_phrase_none_without_data():
    assert recruit.trajectory_phrase("M+", [None, None]) is None


# --- weak-slot detection ----------------------------------------------------


def _m(name, klass, parse=None, mplus=None, ilvl=None, spec="DPS", role="DPS", team=None):
    return Member(
        character_name=name,
        class_name=klass,
        active_spec=spec,
        role=role,
        best_parse_avg=parse,
        mythic_plus_score=mplus,
        item_level=ilvl,
        raid_team=team,
    )


def test_weak_slots_flags_laggard_not_strong_class():
    roster = [
        _m("Goodpal", "Paladin", parse=95, mplus=3200, ilvl=290),
        _m("Badpal", "Paladin", parse=40, mplus=1800, ilvl=276),
        _m("Goodek", "Death Knight", parse=92, mplus=3100, ilvl=289),
    ]
    slots = recruit.weak_slots(roster, threshold=0.4)
    names = {s.incumbent for s in slots}
    # The weak Paladin is flagged; the strong DK (and strong Pal) are not.
    assert "Badpal" in names
    assert "Goodek" not in names
    assert "Goodpal" not in names


def test_weak_slots_picks_weakest_holder_of_each_class():
    roster = [
        _m("Pal1", "Paladin", parse=88, mplus=3000, ilvl=288),
        _m("Pal2", "Paladin", parse=45, mplus=1900, ilvl=277),
    ]
    slots = recruit.weak_slots(roster, threshold=0.5)
    assert [s.incumbent for s in slots] == ["Pal2"]


def test_player_strength_zero_when_no_measurable_stats():
    # A member with no parse / M+ / ilvl has zero measurable weight -> 0.0.
    blank = _m("Blank", "Paladin", parse=None, mplus=None, ilvl=None)
    other = _m("Other", "Mage", parse=80, mplus=2500, ilvl=290)
    assert recruit.player_strength(blank, [blank, other]) == 0.0


def test_weak_slots_team_filter():
    roster = [
        _m("A", "Mage", parse=30, mplus=1000, ilvl=275, team="Main"),
        _m("Strong", "Warrior", parse=95, mplus=3300, ilvl=294, team="Main"),
        _m("B", "Mage", parse=99, mplus=3400, ilvl=295, team="Alt"),
    ]
    slots = recruit.weak_slots(roster, threshold=0.6, team="Main")
    # Only the Main team is considered, so the weak Mage A is flagged and the
    # Alt-team Mage B is ignored entirely.
    assert [s.incumbent for s in slots] == ["A"]


# --- candidate scoring ------------------------------------------------------


def _slot(klass="Paladin", strength=0.2, incumbent="Badpal"):
    return recruit.WeakSlot(
        class_name=klass, spec="Holy", role="HEALER",
        incumbent=incumbent, incumbent_strength=strength,
    )


def test_requirements_gate_rejects_low_ilvl_and_wrong_region():
    req = recruit.Requirements(min_item_level=275, region="eu")
    low = recruit.Candidate("X", "ravencrest", "eu", "Paladin", item_level=270)
    ok, reasons = recruit.meets_requirements(low, req)
    assert ok is False and any("ilvl" in r for r in reasons)

    us = recruit.Candidate("Y", "illidan", "us", "Paladin", item_level=290)
    ok, reasons = recruit.meets_requirements(us, req)
    assert ok is False and any("region" in r for r in reasons)


def test_rank_keeps_only_like_for_like_and_upgrades():
    slot = _slot(klass="Paladin", strength=0.2)
    req = recruit.Requirements(min_item_level=275, region="eu")
    cands = [
        recruit.Candidate("Better", "ravencrest", "eu", "Paladin", item_level=290,
                          parse_seasons=[80, 85, 90], mplus_seasons=[2600, 2800, 3000]),
        recruit.Candidate("WrongClass", "ravencrest", "eu", "Mage", item_level=295,
                          parse_seasons=[95], mplus_seasons=[3400]),
    ]
    matches = recruit.rank_candidates(cands, slot, req)
    assert [m.candidate.name for m in matches] == ["Better"]
    assert matches[0].upgrade_delta > 0


def test_consistent_climber_outranks_one_season_spike():
    slot = _slot(strength=0.1)
    req = recruit.Requirements(min_item_level=275, region="eu")
    climber = recruit.Candidate(
        "Climber", "ravencrest", "eu", "Paladin", item_level=288,
        parse_seasons=[78, 84, 90], mplus_seasons=[2500, 2750, 3000],
    )
    spike = recruit.Candidate(
        "Spiker", "ravencrest", "eu", "Paladin", item_level=288,
        parse_seasons=[95], mplus_seasons=[3300],
    )
    matches = recruit.rank_candidates([spike, climber], slot, req)
    top = matches[0]
    assert top.candidate.name == "Climber"
    spiker = next(m for m in matches if m.candidate.name == "Spiker")
    assert any("spike" in f for f in spiker.flags)


def test_poachability_prefers_unguilded_and_weaker_guilds():
    unguilded = recruit.Candidate("U", "r", "eu", "Paladin")
    assert recruit.poachability(unguilded, our_strength=0.5) == 1.0

    weaker = recruit.Candidate("W", "r", "eu", "Paladin", guild_name="Casuals", guild_strength=0.2)
    stronger = recruit.Candidate("S", "r", "eu", "Paladin", guild_name="Hardcore", guild_strength=0.95)
    assert recruit.poachability(weaker, our_strength=0.6) > recruit.poachability(stronger, our_strength=0.6)


def test_lfg_active_candidate_has_max_poachability():
    # LFG intent always beats guild-strength gap — confirmed intent > proxy.
    in_strong_guild = recruit.Candidate(
        "LFGer", "ravencrest", "eu", "Paladin",
        guild_name="World First Guild", guild_strength=1.0, lfg_active=True,
    )
    assert recruit.poachability(in_strong_guild, our_strength=0.3) == 1.0


def test_guild_strength_gap_affects_poachability():
    # Same candidate class and ilvl; only guild strength differs.
    # From-weaker-guild should be more poachable than from-stronger-guild.
    our = 0.5
    from_weak = recruit.Candidate(
        "Easy", "hellfire", "eu", "Paladin", guild_name="Casuals", guild_strength=0.1
    )
    from_strong = recruit.Candidate(
        "Hard", "hellfire", "eu", "Paladin", guild_name="Method", guild_strength=0.9
    )
    assert recruit.poachability(from_weak, our_strength=our) > recruit.poachability(from_strong, our_strength=our)
    # Known values: gap=0.4 → 0.9; gap=-0.4 → 0.1
    assert recruit.poachability(from_weak, our_strength=our) == pytest.approx(0.9, abs=0.01)
    assert recruit.poachability(from_strong, our_strength=our) == pytest.approx(0.1, abs=0.01)


def _cand(**kw):
    base = dict(name="X", realm="hellfire", region="eu", class_name="Priest",
                item_level=300.0)
    base.update(kw)
    return recruit.Candidate(**base)


def test_meets_requirements_rejects_a_closed_role():
    req = recruit.Requirements(min_item_level=275, roles_open=("DPS",), region="eu")
    ok, reasons = recruit.meets_requirements(_cand(role="HEALER"), req)
    assert ok is False
    assert any("HEALER" in r for r in reasons)


def test_meets_requirements_allows_open_role():
    req = recruit.Requirements(min_item_level=275, roles_open=("DPS",), region="eu")
    assert recruit.meets_requirements(_cand(role="DPS"), req)[0] is True


def test_meets_requirements_does_not_reject_unknown_role():
    # Can't measure the role -> don't reject (mirrors the ilvl skip-when-None).
    req = recruit.Requirements(min_item_level=275, roles_open=("DPS",), region="eu")
    assert recruit.meets_requirements(_cand(role=None), req)[0] is True


def test_score_candidate_flags_recent_decline():
    # A declining curve on either metric raises the 🟡 recent decline flag.
    slot = _slot(strength=0.1)
    decliner = recruit.Candidate(
        "Fader", "ravencrest", "eu", "Paladin", item_level=290,
        parse_seasons=[95, 80, 60], mplus_seasons=[3300, 2900, 2400],
    )
    match = recruit.score_candidate(decliner, slot)
    assert any("decline" in f for f in match.flags)


def test_rank_candidates_drops_gate_failures_and_non_upgrades():
    slot = _slot(klass="Paladin", strength=0.9)  # strong incumbent
    req = recruit.Requirements(min_item_level=275, region="eu")
    cands = [
        # Fails the ilvl gate -> dropped before scoring.
        recruit.Candidate("Lowilvl", "ravencrest", "eu", "Paladin", item_level=200,
                          parse_seasons=[90], mplus_seasons=[3000]),
        # Clears the gate but doesn't out-score the strong incumbent -> dropped
        # by upgrades_only.
        recruit.Candidate("NoUpgrade", "ravencrest", "eu", "Paladin", item_level=290,
                          parse_seasons=[20, 22, 25], mplus_seasons=[800, 850, 900]),
    ]
    assert recruit.rank_candidates(cands, slot, req, upgrades_only=True) == []
    # Without the upgrades_only filter the gated-out candidate is still gone but
    # the non-upgrade one is kept (so we know line 347 was the ilvl gate).
    kept = recruit.rank_candidates(cands, slot, req, upgrades_only=False)
    assert [m.candidate.name for m in kept] == ["NoUpgrade"]
