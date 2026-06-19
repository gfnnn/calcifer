"""Coverage for the WoW Audit client's remaining edge branches.

test_wowaudit.py covers the main mappers and test_wowaudit_http.py the happy-path
endpoints; this fills the gaps — the numeric/bool/timestamp coercion fallbacks,
the wishlist scoring helpers, the raid date/time parsing guards, and the
defensive empty/error branches in the endpoint methods. Reuses the fake session
from test_wowaudit_http so everything stays offline.
"""

from __future__ import annotations

import pytest

from ftabot.services.wowaudit import (
    WowAuditError,
    _as_bool,
    _as_float,
    _as_int,
    _as_iso,
    _as_list,
    _count_like,
    _iter_encounter_items,
    _norm_time,
    _wishlist_is_bis,
    _wishlist_upgrade,
    parse_signups,
    parse_wishlists,
    raid_start_dt,
    select_next_raid,
    select_upcoming_raids,
)

from .test_wowaudit_http import _client, _FakeResp, _FakeSession

# --- numeric / bool / timestamp coercion ------------------------------------


def test_as_float_and_int_swallow_bad_values():
    assert _as_float("not a number") is None
    assert _as_float(None) is None
    assert _as_float("3.5") == 3.5
    assert _as_int("not a number") is None
    assert _as_int(None) is None
    assert _as_int("7") == 7


def test_as_bool_handles_strings_and_objects():
    assert _as_bool("yes") is True
    assert _as_bool("FALSE") is False
    assert _as_bool("y") is True
    assert _as_bool(1) is True  # non-string falls through to bool()
    assert _as_bool(0) is False


def test_count_like_interprets_bool_list_and_int():
    assert _count_like(None) is None
    assert _count_like(True) == 1
    assert _count_like(False) == 0
    assert _count_like(["a", "b", "c"]) == 3
    assert _count_like(("x",)) == 1
    assert _count_like("4") == 4


def test_as_iso_from_epoch_string_and_overflow():
    assert _as_iso(None) is None
    assert _as_iso(0).startswith("1970-01-01")  # epoch seconds -> ISO
    assert _as_iso("2026-06-06T20:00:00+00:00") == "2026-06-06T20:00:00+00:00"
    assert _as_iso(10**20) is None  # out-of-range epoch -> swallowed


# --- wishlist scoring helpers -----------------------------------------------


def test_wishlist_upgrade_prefers_score_by_spec_then_wishes():
    by_spec = {"score_by_spec": {"Holy": {"percentage": 8.5}}}
    assert _wishlist_upgrade(by_spec) == 8.5
    # No spec scores, but a wish percentage is present.
    by_wish = {"wishes": [{"percentage": 4.0}]}
    assert _wishlist_upgrade(by_wish) == 4.0
    assert _wishlist_upgrade({}) is None


def test_wishlist_is_bis_on_top_bucket():
    assert _wishlist_is_bis({"score_by_spec": {"Holy": {"score": "Huge"}}}) is True
    assert _wishlist_is_bis({"score_by_spec": {"Holy": {"score": "minor"}}}) is False
    assert _wishlist_is_bis({}) is False


def test_iter_encounter_items_ignores_non_dict():
    assert list(_iter_encounter_items("not a dict")) == []
    assert list(_iter_encounter_items({"x": "not a list"})) == []


def test_as_list_unwraps_bare_list_and_filters_non_dicts():
    assert _as_list([{"a": 1}, "skip", {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert _as_list({"characters": [{"a": 1}]}, "characters") == [{"a": 1}]
    assert _as_list("nope") == []


# --- raid date/time parsing -------------------------------------------------


def test_norm_time_variants():
    assert _norm_time(None) == "00:00:00"
    assert _norm_time("20:00") == "20:00:00"  # HH:MM gets seconds
    assert _norm_time("20:00:00") == "20:00:00"  # already full -> unchanged


def test_raid_start_dt_guards():
    assert raid_start_dt({}) is None  # no date
    assert raid_start_dt({"date": "2026-06-06", "time": "not-a-time"}) is None


def test_select_upcoming_raids_skips_non_dict_entries():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    raids = [None, {"date": "2026-06-06", "start_time": "20:00"}]
    due = select_upcoming_raids(raids, now, within_hours=6)
    assert len(due) == 1  # the None was skipped, the real raid kept


# --- signup / wishlist parsing guards ---------------------------------------


def test_parse_signups_tolerates_non_dicts():
    assert parse_signups("not a dict") == []
    detail = {"signups": [None, {"character": {"name": "Testcaller"}, "status": "Present"}]}
    assert parse_signups(detail) == [("Testcaller", "present")]


def test_parse_wishlists_skips_non_dict_instances_and_difficulties():
    data = {
        "characters": [
            {
                "name": "Ashbringer",
                "realm": "hellfire",
                "instances": [
                    None,  # non-dict instance -> skipped
                    {"difficulties": [None]},  # non-dict difficulty -> skipped
                ],
            }
        ]
    }
    assert parse_wishlists(data, "hellfire", "eu") == []


# --- endpoint defensive branches --------------------------------------------


def test_session_headers_carry_the_api_key():
    headers = _client(_FakeSession({}))._session_headers()
    assert headers["Authorization"] == "key"
    assert headers["Accept"] == "application/json"


async def test_team_returns_dict_or_empty():
    ok = _client(_FakeSession({"/team": _FakeResp(200, {"name": "Cinder"})}))
    assert await ok.team() == {"name": "Cinder"}
    # A non-dict payload collapses to {}.
    weird = _client(_FakeSession({"/team": _FakeResp(200, ["unexpected"])}))
    assert await weird.team() == {}


async def test_raw_characters_unknown_envelope_is_empty():
    session = _FakeSession({"/characters": _FakeResp(200, {"unexpected": "shape"})})
    assert await _client(session).raw_characters() == []


async def test_vault_slots_skips_nameless_characters():
    payload = {"characters": [{"data": {"vault_options": {"raids": {"option_1": 1}}}}]}
    session = _FakeSession({"/historical_data": _FakeResp(200, payload)})
    assert await _client(session).vault_slots() == {}  # the nameless row was skipped


async def test_wishlists_endpoint_parses_rows():
    payload = {
        "characters": [
            {
                "name": "Ashbringer",
                "realm": "hellfire",
                "instances": [
                    {
                        "difficulties": [
                            {
                                "difficulty": "Mythic",
                                "wishlist": {
                                    "encounters": [
                                        {
                                            "name": "Council",
                                            "items": [
                                                {"id": 1, "name": "Truthguard",
                                                 "wishes": [{"percentage": 5.0}]}
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    session = _FakeSession({"/wishlists": _FakeResp(200, payload)})
    items = await _client(session).wishlists()
    assert len(items) == 1
    assert items[0].item_name == "Truthguard"
    assert items[0].upgrade_pct == 5.0
    assert items[0].boss == "Council"


async def test_recent_attendance_skips_raid_without_id():
    raids = {"raids": [{"date": "2020-01-01", "instance": "No id"}]}
    session = _FakeSession({"/raids": _FakeResp(200, raids)})
    assert await _client(session).recent_attendance() == []


async def test_recent_attendance_swallows_detail_errors():
    raids = {"raids": [{"id": 7, "date": "2020-01-01"}]}
    session = _FakeSession({
        "/raids/7": _FakeResp(404),  # raised -> caught -> raid skipped
        "/raids": _FakeResp(200, raids),
    })
    assert await _client(session).recent_attendance() == []


async def test_get_propagates_other_error_statuses():
    session = _FakeSession({"/team": _FakeResp(503, text="overloaded")})
    with pytest.raises(WowAuditError, match="HTTP 503"):
        await _client(session).team()


def test_select_next_raid_skips_non_dict_entries():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    raids = ["not-a-dict", {"date": "2026-06-08", "start_time": "20:00"}]
    chosen = select_next_raid(raids, now)
    assert chosen is not None
    raid, _start = chosen
    assert raid["date"] == "2026-06-08"  # the non-dict was skipped


async def test_upcoming_raid_signups_returns_next_with_signups():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    raids = {"raids": [{"id": 9, "date": "2026-06-08", "start_time": "20:00",
                        "instance": "Palace", "difficulty": "Mythic"}]}
    detail = {"signups": [{"character": {"name": "Testcaller"}, "status": "Present"}]}
    session = _FakeSession({
        "/raids/9": _FakeResp(200, detail),
        "/raids": _FakeResp(200, raids),
    })
    out = await _client(session).upcoming_raid_signups(now=now)
    assert out["instance"] == "Palace"
    assert out["signups"] == [("Testcaller", "present")]


async def test_upcoming_raid_signups_none_when_nothing_scheduled():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    # The only raid is in the past, so nothing is scheduled at/after now.
    raids = {"raids": [{"id": 1, "date": "2020-01-01", "start_time": "20:00"}]}
    session = _FakeSession({"/raids": _FakeResp(200, raids)})
    assert await _client(session).upcoming_raid_signups(now=now) is None


async def test_upcoming_raid_signups_swallows_detail_error():
    from datetime import datetime, timezone

    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    raids = {"raids": [{"id": 9, "date": "2026-06-08", "start_time": "20:00",
                        "instance": "Palace"}]}
    session = _FakeSession({
        "/raids/9": _FakeResp(500, text="boom"),  # detail fetch errors -> []
        "/raids": _FakeResp(200, raids),
    })
    out = await _client(session).upcoming_raid_signups(now=now)
    assert out["instance"] == "Palace"
    assert out["signups"] == []  # error swallowed, raid still returned
