"""Tests for the WarcraftLogsClient GraphQL methods (offline).

Complements tests/test_warcraftlogs.py (pure ``pick_current_raid_zone``) and
tests/test_scout_services.py (rankings/curve) by covering ``enrich_character``,
zone auto-detect, the live-prog feed helpers, and the GraphQL error paths.

A fake session serves the token POST then the queued GraphQL payloads in order.
"""

from __future__ import annotations

import pytest

from ftabot.models import Member
from ftabot.services.warcraftlogs import WarcraftLogsClient, WarcraftLogsError


class _FakeResp:
    def __init__(self, status=200, payload=None, text="error body"):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakePostSession:
    """Serves queued responses to POST (token first, then GraphQL calls)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, **kwargs):
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp

    async def close(self):
        pass


_TOKEN = _FakeResp(200, {"access_token": "t", "expires_in": 3600})


async def test_enrich_character_sets_best_parse_avg():
    rankings = {
        "data": {
            "characterData": {
                "character": {
                    "zoneRankings": {"bestPerformanceAverage": 91.37}
                }
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, rankings)])
    member = Member("Topdps", realm_slug="hellfire", region="eu")
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        await wcl.enrich_character(member)

    assert member.best_parse_avg == 91.4  # rounded to one decimal


async def test_enrich_character_unchanged_when_no_character():
    empty = {"data": {"characterData": {"character": None}}}
    session = _FakePostSession([_TOKEN, _FakeResp(200, empty)])
    member = Member("Ghost", realm_slug="hellfire", region="eu")
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        await wcl.enrich_character(member)

    assert member.best_parse_avg is None


async def test_current_zone_id_auto_detects_and_caches():
    zones = {
        "data": {
            "worldData": {
                "zones": [
                    {"id": 38, "name": "Old Tier", "expansion": {"id": 5}},
                    {"id": 42, "name": "Current Tier", "expansion": {"id": 5}},
                ]
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, zones)])
    async with WarcraftLogsClient("id", "secret", session=session) as wcl:
        assert await wcl.current_zone_id() == 42
        # Cached: a second call does not issue another GraphQL request.
        assert await wcl.current_zone_id() == 42
    assert session.calls == 2  # token + one zones query only


async def test_enrich_character_noops_when_zone_unresolved():
    no_raids = {"data": {"worldData": {"zones": []}}}
    session = _FakePostSession([_TOKEN, _FakeResp(200, no_raids)])
    member = Member("Nobody", realm_slug="hellfire", region="eu")
    async with WarcraftLogsClient("id", "secret", session=session) as wcl:
        await wcl.enrich_character(member)

    assert member.best_parse_avg is None


async def test_zone_encounters_parses_bosses():
    payload = {
        "data": {
            "worldData": {
                "zone": {
                    "id": 42, "name": "Current Tier",
                    "encounters": [
                        {"id": 3001, "name": "First Boss"},
                        {"id": 3002, "name": "Second Boss"},
                        {"name": "No id -> skipped"},
                    ],
                }
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, payload)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        bosses = await wcl.zone_encounters(42)

    assert bosses == [
        {"id": 3001, "name": "First Boss"},
        {"id": 3002, "name": "Second Boss"},
    ]


async def test_guild_reports_parses_recent_logs():
    payload = {
        "data": {
            "reportData": {
                "reports": {
                    "data": [
                        {"code": "abcd", "title": "Raid Night",
                         "startTime": 1, "endTime": 2,
                         "zone": {"name": "Current Tier"}},
                        {"title": "no code -> skipped"},
                    ]
                }
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, payload)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        reports = await wcl.guild_reports("From the Ashes", "hellfire", "eu")

    assert len(reports) == 1
    assert reports[0]["code"] == "abcd"
    assert reports[0]["zone"] == "Current Tier"


async def test_report_fights_returns_report_dict():
    payload = {
        "data": {
            "reportData": {
                "report": {
                    "title": "Prog",
                    "fights": [{"id": 1, "name": "Boss", "kill": False,
                                "bossPercentage": 12.5}],
                }
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, payload)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        report = await wcl.report_fights("abcd")

    assert report["title"] == "Prog"
    assert report["fights"][0]["bossPercentage"] == 12.5


async def test_graphql_errors_surface_as_empty_results():
    err = _FakeResp(200, {"errors": [{"message": "bad query"}]})
    session = _FakePostSession([_TOKEN, err])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        # report_fights swallows the error and returns an empty dict.
        assert await wcl.report_fights("abcd") == {}


async def test_http_error_on_query_raises_inside_query():
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        with pytest.raises(WarcraftLogsError):
            await wcl._query("query { x }", {})


async def test_query_retries_once_on_401_with_fresh_token():
    # token, 401 (revoked), fresh token, then success.
    session = _FakePostSession([
        _TOKEN,
        _FakeResp(401),
        _TOKEN,
        _FakeResp(200, {"data": {"ok": 1}}),
    ])
    async with WarcraftLogsClient("id", "secret", session=session) as wcl:
        data = await wcl._query("query { x }", {})
    assert data == {"ok": 1}
    assert session.calls == 4  # token, 401, token, 200


async def test_query_raises_on_persistent_401():
    session = _FakePostSession([_TOKEN, _FakeResp(401), _TOKEN, _FakeResp(401)])
    async with WarcraftLogsClient("id", "secret", session=session) as wcl:
        with pytest.raises(WarcraftLogsError):
            await wcl._query("query { x }", {})
    assert session.calls == 4  # retried exactly once
