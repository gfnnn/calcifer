from ftabot.services.wowaudit import (
    WowAuditClient,
    _curate_details,
    _first,
    parse_signups,
    parse_wishlists,
    select_recent_raids,
)


def _client():
    return WowAuditClient("dummy-key", default_region="eu", default_realm="hellfire")


def test_first_picks_first_present():
    assert _first({"a": None, "b": 2}, "a", "b") == 2
    assert _first({}, "a", "b") is None
    assert _first({"a": 0}, "a") == 0  # falsy-but-present values are returned


def test_to_member_maps_common_fields():
    raw = {
        "name": "Ashbringer",
        "realm": "Hellfire",
        "class": "Paladin",
        "spec": "Protection",
        "role": "tank",
        "race": "Human",
        "rank": 1,
        "blizzard_id": 12345,
        "equipped_item_level": 632.5,
        "average_item_level": 634,
        "mythic_plus_score": 2950,
        "best_parse_average": 88,
        "profile_url": "https://example.com/ashbringer",
    }
    m = _client().to_member(raw)
    assert m.character_name == "Ashbringer"
    assert m.realm_slug == "hellfire"
    assert m.region == "eu"
    assert m.class_name == "Paladin"
    assert m.active_spec == "Protection"
    assert m.role == "TANK"
    assert m.guild_rank == 1
    assert m.blizzard_id == 12345
    assert m.item_level == 632.5
    assert m.average_item_level == 634.0
    assert m.mythic_plus_score == 2950.0
    assert m.best_parse_avg == 88.0
    assert m.source == "wowaudit"
    assert m.details["raw"]["name"] == "Ashbringer"


def test_to_member_handles_nested_and_alternate_keys():
    raw = {
        "character_name": "Emberlyn",
        "realm": {"slug": "argent-dawn", "name": "Argent Dawn"},
        "wow_class": {"name": "Mage"},
        "current_spec_name": "Fire",
        "current_role": "dps",
        "id": 99,
        "item_level": 630,
    }
    m = _client().to_member(raw)
    assert m.character_name == "Emberlyn"
    assert m.realm_slug == "argent-dawn"
    assert m.class_name == "Mage"
    assert m.active_spec == "Fire"
    assert m.role == "DPS"
    assert m.blizzard_id == 99
    assert m.item_level == 630.0


def test_to_member_uses_defaults_when_missing():
    m = _client().to_member({"name": "Lonewolf"})
    assert m.realm_slug == "hellfire"
    assert m.region == "eu"
    assert m.class_name is None
    assert m.guild_rank is None


def test_raid_progression_only_kept_when_string():
    string_prog = _client().to_member({"name": "A", "raid_progression": "8/8 M"})
    assert string_prog.raid_progression == "8/8 M"
    dict_prog = _client().to_member(
        {"name": "B", "raid_progression": {"nerubar": "8/8"}}
    )
    assert dict_prog.raid_progression is None
    # ...but the structured data is still preserved in details.
    assert dict_prog.details["raw"]["raid_progression"] == {"nerubar": "8/8"}


def test_curate_details_keeps_known_and_raw():
    raw = {"name": "X", "gear": [{"slot": "head"}], "wishlist": ["item1"]}
    details = _curate_details(raw)
    assert details["gear"] == [{"slot": "head"}]
    assert details["wishlist"] == ["item1"]
    assert details["raw"] == raw


def test_to_member_prep_and_vault_fields():
    m = _client().to_member(
        {
            "name": "Prepless",
            "missing_enchants": ["chest", "wrist"],  # list -> count of 2
            "missing_gems": 1,
            "vault_slots_filled": 6,
            "last_login": 1700000000,  # epoch -> ISO string
        }
    )
    assert m.missing_enchants == 2
    assert m.missing_gems == 1
    assert m.vault_slots_filled == 6
    assert m.last_login is not None and m.last_login.startswith("20")


def test_parse_wishlists_real_shape():
    # Mirrors the real /wishlists response: characters -> instances ->
    # difficulties -> wishlist -> (encounter list) -> items.
    data = {
        "characters": [
            {
                "name": "Testwarden",
                "realm": "Arathor",
                "instances": [
                    {
                        "name": "The Voidspire",
                        "difficulties": [
                            {
                                "difficulty": "mythic",
                                "wishlist": {
                                    "updated_at": {"Retribution": None},
                                    "encounters": [
                                        {"name": "Salhadaar", "items": []},
                                        {
                                            "name": "Lightblinded Vanguard",
                                            "items": [
                                                {
                                                    "name": "Bellamy's Final Judgement",
                                                    "id": 249277,
                                                    "slot": "main_hand_2h",
                                                    "score_by_spec": {
                                                        "Retribution": {
                                                            "score": "huge",
                                                            "percentage": 6.4,
                                                        }
                                                    },
                                                }
                                            ],
                                        },
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    items = parse_wishlists(data, "hellfire", "eu")
    assert len(items) == 1
    w = items[0]
    assert w.character_name == "Testwarden"
    assert w.realm_slug == "arathor"
    assert w.item_id == 249277
    assert w.item_name == "Bellamy's Final Judgement"
    assert w.slot == "main_hand_2h"
    assert w.boss == "Lightblinded Vanguard"
    assert w.difficulty == "mythic"
    assert w.upgrade_pct == 6.4
    assert w.is_bis is True  # "huge" score -> flagged


def test_parse_wishlists_word_grade_scores():
    # WoW Audit often ships an ordinal word grade ("huge"/"big"/…) instead of a
    # numeric percentage; the parser must still produce a sortable upgrade_pct.
    data = {
        "characters": [
            {
                "name": "Gradey",
                "realm": "Hellfire",
                "instances": [
                    {
                        "name": "The Voidspire",
                        "difficulties": [
                            {
                                "difficulty": "mythic",
                                "wishlist": {
                                    "encounters": [
                                        {
                                            "name": "Vorasius",
                                            "items": [
                                                {
                                                    "name": "Heart of Ancient Hunger",
                                                    "id": 249342,
                                                    "slot": "trinket",
                                                    "score_by_spec": {
                                                        "Fury": {
                                                            "score": "huge",
                                                            "percentage": "huge",
                                                        }
                                                    },
                                                    "wishes": [],
                                                },
                                                {
                                                    "name": "Light Company Guidon",
                                                    "id": 249344,
                                                    "slot": "trinket",
                                                    "score_by_spec": {
                                                        "Fury": {
                                                            "score": "small",
                                                            "percentage": "small",
                                                        }
                                                    },
                                                    "wishes": [],
                                                },
                                            ],
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    items = parse_wishlists(data, "hellfire", "eu")
    by_name = {w.item_name: w for w in items}
    assert by_name["Heart of Ancient Hunger"].upgrade_pct == 100.0
    assert by_name["Heart of Ancient Hunger"].is_bis is True
    assert by_name["Light Company Guidon"].upgrade_pct == 30.0
    assert by_name["Light Company Guidon"].is_bis is False


def test_parse_wishlists_empty():
    assert parse_wishlists({"characters": []}, "hellfire", "eu") == []
    assert parse_wishlists({}, "hellfire", "eu") == []


def test_parse_signups_real_shape():
    detail = {
        "signups": [
            {"character": {"name": "Testcaller", "realm": "Silvermoon"}, "status": "Present"},
            {"character": {"name": "Gfn"}, "status": "Absent"},
            {"character": {"name": "Testmender"}, "status": "Tentative"},
            {"status": "Present"},  # malformed (no character) -> skipped
        ]
    }
    pairs = parse_signups(detail)
    assert pairs == [("Testcaller", "present"), ("Gfn", "absent"), ("Testmender", "tentative")]
    assert parse_signups({}) == []


def test_select_recent_raids_prefers_past():
    raids = [
        {"id": 1, "date": "2026-05-01"},
        {"id": 2, "date": "2026-05-08"},
        {"id": 3, "date": "2026-06-07"},  # future
        {"id": 4, "date": "2026-06-05"},  # today
    ]
    # Past preferred, newest first; future excluded.
    assert [r["id"] for r in select_recent_raids(raids, "2026-06-05", 8)] == [4, 2, 1]
    assert [r["id"] for r in select_recent_raids(raids, "2026-06-05", 1)] == [4]


def test_select_recent_raids_falls_back_to_upcoming():
    raids = [
        {"id": 3, "date": "2026-06-21"},
        {"id": 1, "date": "2026-06-07"},  # soonest upcoming
        {"id": 2, "date": "2026-06-14"},
    ]
    # No past raids -> soonest upcoming first.
    assert [r["id"] for r in select_recent_raids(raids, "2026-06-05", 2)] == [1, 2]


def test_select_recent_raids_same_day_datetime_counts_as_past():
    # WoW Audit may return a datetime string; the date prefix must still be
    # compared correctly so a same-day raid is classified as past, not future.
    raids = [
        {"id": 1, "date": "2026-06-05T19:00:00"},  # same day, time component
        {"id": 2, "date": "2026-06-07"},            # future
    ]
    result = select_recent_raids(raids, "2026-06-05", 8)
    assert [r["id"] for r in result] == [1]
