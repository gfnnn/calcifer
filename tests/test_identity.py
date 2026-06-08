"""Tests for the pure account-grouping logic."""

from ftabot.identity import build_accounts
from ftabot.models import IdentityRecord


def _rec(name, **kw):
    return IdentityRecord(character_name=name, realm_slug="hellfire", **kw)


def test_main_with_alts_forms_one_account():
    recs = [
        _rec("Calcifer", is_main=True, alts=["Emberling-Hellfire", "Cinder-Hellfire"]),
        _rec("Emberling"),
        _rec("Cinder"),
    ]
    accounts = build_accounts(recs)
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.main == ("calcifer", "hellfire")
    assert acc.alts == [("cinder", "hellfire"), ("emberling", "hellfire")]


def test_alt_pointing_at_main_groups_and_main_flag_wins():
    recs = [
        _rec("Calcifer", is_main=True),
        _rec("Emberling", main_name="Calcifer-Hellfire"),
    ]
    accounts = build_accounts(recs)
    assert len(accounts) == 1
    assert accounts[0].main == ("calcifer", "hellfire")
    assert accounts[0].alts == [("emberling", "hellfire")]


def test_transitive_links_collapse_into_one_account():
    # A links B, B links C -> all three are one person.
    recs = [
        _rec("Aaa", alts=["Bbb-Hellfire"]),
        _rec("Bbb", alts=["Ccc-Hellfire"]),
        _rec("Ccc"),
    ]
    accounts = build_accounts(recs)
    assert len(accounts) == 1
    assert len(accounts[0].members) == 3


def test_main_chosen_by_vote_when_no_flag():
    # Two alts both name Calcifer as main; nobody is flagged isMain.
    recs = [
        _rec("Emberling", main_name="Calcifer-Hellfire"),
        _rec("Cinder", main_name="Calcifer-Hellfire"),
        _rec("Calcifer"),
    ]
    accounts = build_accounts(recs)
    assert accounts[0].main == ("calcifer", "hellfire")


def test_main_chosen_by_earliest_join_when_no_flag_or_vote():
    recs = [
        _rec("Aaa", alts=["Bbb-Hellfire"], join_date="2022-01-01"),
        _rec("Bbb", join_date="2019-05-05"),
    ]
    accounts = build_accounts(recs)
    # Bbb joined earliest, so it anchors the account.
    assert accounts[0].main == ("bbb", "hellfire")


def test_separate_people_stay_separate():
    recs = [
        _rec("Calcifer", is_main=True, alts=["Emberling-Hellfire"]),
        _rec("Emberling"),
        _rec("Howl", is_main=True, alts=["Sophie-Hellfire"]),
        _rec("Sophie"),
    ]
    accounts = build_accounts(recs)
    mains = sorted(a.main for a in accounts)
    assert mains == [("calcifer", "hellfire"), ("howl", "hellfire")]


def test_cross_realm_alt_is_grouped():
    recs = [
        _rec("Calcifer", is_main=True, alts=["Emberling-Argent Dawn"]),
        IdentityRecord(character_name="Emberling", realm_slug="argent-dawn"),
    ]
    accounts = build_accounts(recs)
    assert len(accounts) == 1
    assert ("emberling", "argent-dawn") in accounts[0].alts


def test_extra_alt_groups_link_characters_records_missed():
    # Per-member records don't connect these two, but the alt-list table does.
    recs = [
        _rec("Calcifer", is_main=True),
        _rec("Emberling"),
    ]
    no_link = build_accounts(recs)
    assert len(no_link) == 2

    linked = build_accounts(
        recs, extra_alt_groups=[["Calcifer-Hellfire", "Emberling-Hellfire"]]
    )
    assert len(linked) == 1
    # The extra group only links; the main is still the isMain-flagged record.
    assert linked[0].main == ("calcifer", "hellfire")
    assert linked[0].alts == [("emberling", "hellfire")]


def test_extra_alt_groups_do_not_vote_for_main():
    # An alt-list group naming Emberling first must not make it the main; the
    # earliest-join record still wins because the group casts no main vote.
    recs = [
        _rec("Emberling", join_date="2022-01-01"),
        _rec("Calcifer", join_date="2019-05-05"),
    ]
    accounts = build_accounts(
        recs, extra_alt_groups=[["Emberling-Hellfire", "Calcifer-Hellfire"]]
    )
    assert len(accounts) == 1
    assert accounts[0].main == ("calcifer", "hellfire")
