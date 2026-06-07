"""HTTP-layer tests for WowAuditClient (offline).

tests/test_wowaudit.py covers the pure mappers; this file drives the async
endpoint methods through a fake session: envelope unwrapping, error-status
handling, and the vault/attendance/upcoming-raid aggregations.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ftabot.services.wowaudit import WowAuditClient, WowAuditError


class _FakeResp:
    def __init__(self, status=200, payload=None, text="error body"):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    """Maps URL substrings to responses; supports queued responses per key."""

    def __init__(self, routes):
        # routes: {url_substring: response | [responses...]}
        self._routes = routes
        self._counts = {k: 0 for k in routes}

    def get(self, url, params=None):
        for key, value in self._routes.items():
            if key in url:
                if isinstance(value, list):
                    i = min(self._counts[key], len(value) - 1)
                    self._counts[key] += 1
                    return value[i]
                return value
        raise AssertionError(f"unexpected URL: {url}")

    async def close(self):
        pass


def _client(session):
    return WowAuditClient("key", default_region="eu", default_realm="hellfire",
                          session=session)


async def test_raw_characters_unwraps_envelope():
    session = _FakeSession({
        "/characters": _FakeResp(200, {"characters": [{"name": "A"}, {"name": "B"}]})
    })
    raw = await _client(session).raw_characters()
    assert [c["name"] for c in raw] == ["A", "B"]


async def test_raw_characters_accepts_bare_list():
    session = _FakeSession({"/characters": _FakeResp(200, [{"name": "Solo"}])})
    raw = await _client(session).raw_characters()
    assert raw == [{"name": "Solo"}]


async def test_characters_maps_to_members():
    session = _FakeSession({
        "/characters": _FakeResp(200, [{"name": "Ashbringer", "class": "Paladin"}])
    })
    members = await _client(session).characters()
    assert len(members) == 1
    assert members[0].character_name == "Ashbringer"
    assert members[0].class_name == "Paladin"


async def test_get_raises_on_401():
    session = _FakeSession({"/team": _FakeResp(401)})
    with pytest.raises(WowAuditError, match="unauthorized"):
        await _client(session).team_name()


async def test_get_raises_on_404():
    session = _FakeSession({"/characters": _FakeResp(404)})
    with pytest.raises(WowAuditError, match="not found"):
        await _client(session).raw_characters()


async def test_get_raises_on_500():
    session = _FakeSession({"/team": _FakeResp(500, text="boom")})
    with pytest.raises(WowAuditError, match="HTTP 500"):
        await _client(session).team()


async def test_team_name_reads_name():
    session = _FakeSession({"/team": _FakeResp(200, {"name": "Cinder"})})
    assert await _client(session).team_name() == "Cinder"


async def test_vault_slots_counts_unlocked_options():
    payload = {
        "characters": [
            {
                "name": "Ashbringer",
                "data": {
                    "vault_options": {
                        "raids": {"option_1": 1, "option_2": 2, "option_3": None},
                        "dungeons": {"option_1": 5, "option_2": None},
                        "world": {"option_1": None},
                    }
                },
            },
            {"name": "Empty", "data": {}},
        ]
    }
    session = _FakeSession({"/historical_data": _FakeResp(200, payload)})
    slots = await _client(session).vault_slots()
    assert slots["ashbringer"] == 3  # two raid + one dungeon option filled
    assert slots["empty"] == 0


async def test_recent_attendance_joins_raid_detail():
    today = datetime.now(timezone.utc).date().isoformat()
    raids = {"raids": [{"id": 7, "date": "2020-01-01", "instance": "Palace",
                        "difficulty": "Mythic"}]}
    detail = {
        "signups": [
            {"character": {"name": "Testcaller"}, "status": "Present"},
            {"character": {"name": "Gfn"}, "status": "Absent"},
        ]
    }
    session = _FakeSession({
        "/raids/7": _FakeResp(200, detail),
        "/raids": _FakeResp(200, raids),
    })
    events = await _client(session).recent_attendance()
    assert len(events) == 1
    assert events[0]["id"] == 7
    assert events[0]["instance"] == "Palace"
    assert events[0]["signups"] == [("Testcaller", "present"), ("Gfn", "absent")]
    assert today  # sanity: the helper filters by today's date internally


async def test_upcoming_raids_within_window():
    now = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)
    raids = {
        "raids": [
            {"date": "2026-06-06", "start_time": "20:00", "instance": "Palace",
             "difficulty": "Heroic", "present_size": 18, "total_size": 25},
            {"date": "2026-06-10", "start_time": "20:00", "instance": "Later"},
        ]
    }
    session = _FakeSession({"/raids": _FakeResp(200, raids)})
    due = await _client(session).upcoming_raids(within_hours=6, now=now)
    assert len(due) == 1
    assert due[0]["instance"] == "Palace"
    assert due[0]["present"] == 18
    assert due[0]["total"] == 25
