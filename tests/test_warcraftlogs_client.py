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


async def test_current_zone_id_returns_none_on_query_failure():
    # The zones auto-detect query fails (HTTP 500); the warning path returns None.
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    async with WarcraftLogsClient("id", "secret", session=session) as wcl:
        assert await wcl.current_zone_id() is None


async def test_enrich_character_swallows_query_error():
    # zone resolves (pinned), but the rankings query errors -> member unchanged.
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    member = Member("Errorer", realm_slug="hellfire", region="eu")
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        result = await wcl.enrich_character(member)
    assert result.best_parse_avg is None


async def test_zone_encounters_returns_empty_on_error():
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        assert await wcl.zone_encounters(42) == []


async def test_character_rankings_breaks_on_query_error():
    # The first page query errors -> the loop breaks and returns nothing.
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        assert await wcl.character_rankings(3001, region="eu") == []


async def test_character_rankings_breaks_when_rankings_not_a_dict():
    # A schema-drifted payload where characterRankings isn't a dict -> break.
    payload = {"data": {"worldData": {"encounter": {"characterRankings": None}}}}
    session = _FakePostSession([_TOKEN, _FakeResp(200, payload)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        assert await wcl.character_rankings(3001, region="eu") == []


async def test_character_rankings_parses_and_skips_nameless():
    payload = {
        "data": {
            "worldData": {
                "encounter": {
                    "characterRankings": {
                        "rankings": [
                            {"name": "Topdps", "class": "Mage", "spec": "Fire",
                             "amount": 123.0, "rankPercent": 99,
                             "server": {"name": "Hellfire", "region": "EU"},
                             "guild": {"name": "From the Ashes"}},
                            {"amount": 1},  # no name -> dropped by _parse_ranking
                        ],
                        "hasMorePages": False,
                    }
                }
            }
        }
    }
    session = _FakePostSession([_TOKEN, _FakeResp(200, payload)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        out = await wcl.character_rankings(
            3001, class_name="Death Knight", spec_name="Blood", region="eu"
        )
    assert len(out) == 1
    assert out[0]["name"] == "Topdps"
    assert out[0]["region"] == "eu"  # EU unmapped back to eu
    assert out[0]["guild_name"] == "From the Ashes"


async def test_guild_reports_returns_empty_on_error():
    session = _FakePostSession([_TOKEN, _FakeResp(500)])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        assert await wcl.guild_reports("From the Ashes", "hellfire", "eu") == []


async def test_parse_curve_yields_values_and_none_on_error():
    # First zone errors (-> None slot), second zone returns a parse average.
    rankings = {
        "data": {
            "characterData": {
                "character": {"zoneRankings": {"bestPerformanceAverage": 80.46}}
            }
        }
    }
    session = _FakePostSession([
        _TOKEN,
        _FakeResp(500),            # zone 41 -> WarcraftLogsError -> None
        _FakeResp(200, rankings),  # zone 42 -> 80.5
    ])
    async with WarcraftLogsClient("id", "secret", zone_id=42, session=session) as wcl:
        curve = await wcl.parse_curve("Topdps", "hellfire", "eu", [41, 42])
    assert curve == [None, 80.5]
