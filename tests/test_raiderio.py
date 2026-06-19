import pytest

from ftabot.services.raiderio import RaiderIOClient, RaiderIOError


class _FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self._payload = payload or {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return "error body"


class _FakeSession:
    """Returns queued responses in order; repeats the last one."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp

    async def close(self):
        pass


async def test_retries_on_429_then_succeeds():
    session = _FakeSession(
        [
            _FakeResp(429, headers={"Retry-After": "0"}),
            _FakeResp(429, headers={"Retry-After": "0"}),
            _FakeResp(200, {"name": "Ashbringer"}),
        ]
    )
    client = RaiderIOClient(session=session)
    data = await client._get("/characters/profile", {"name": "Ashbringer"})
    assert data == {"name": "Ashbringer"}
    assert session.calls == 3  # two 429s, then success


async def test_retries_exhausted_raises():
    session = _FakeSession([_FakeResp(429, headers={"Retry-After": "0"})])
    client = RaiderIOClient(session=session)
    with pytest.raises(RaiderIOError):
        await client._get("/characters/profile", {"name": "X"}, retries=2)
    assert session.calls == 3  # initial try + 2 retries


async def test_404_raises_immediately_without_retry():
    session = _FakeSession([_FakeResp(404)])
    client = RaiderIOClient(session=session)
    with pytest.raises(RaiderIOError):
        await client._get("/guilds/profile", {"name": "Nope"})
    assert session.calls == 1


async def test_guild_strength_ratio_from_progression():
    payload = {"raid_progression": {"current-tier": {
        "total_bosses": 8, "mythic_bosses_killed": 2}}}
    client = RaiderIOClient(session=_FakeSession([_FakeResp(200, payload)]))
    assert await client.guild_strength("G", "hellfire", "eu") == 0.25


async def test_guild_strength_skips_non_dict_entries_without_crashing():
    # Defensive: a malformed/non-dict progression value must yield None, not an
    # AttributeError into the scout.
    payload = {"raid_progression": {"bad": "not-a-dict"}}
    client = RaiderIOClient(session=_FakeSession([_FakeResp(200, payload)]))
    assert await client.guild_strength("G", "hellfire", "eu") is None


async def test_guild_strength_none_when_no_progression():
    client = RaiderIOClient(session=_FakeSession([_FakeResp(200, {})]))
    assert await client.guild_strength("G", "hellfire", "eu") is None


async def test_guild_strength_none_when_total_bosses_zero():
    # A raid entry with zero total bosses can't yield a ratio.
    payload = {"raid_progression": {"current-tier": {
        "total_bosses": 0, "mythic_bosses_killed": 0}}}
    client = RaiderIOClient(session=_FakeSession([_FakeResp(200, payload)]))
    assert await client.guild_strength("G", "hellfire", "eu") is None


def test_session_headers_accept_json():
    # The client-owned session is seeded to accept JSON.
    assert RaiderIOClient()._session_headers() == {"Accept": "application/json"}


async def test_get_with_no_attempts_raises_retries_exhausted():
    # retries=-1 makes the attempt loop empty, hitting the final guard rather
    # than any per-attempt raise.
    client = RaiderIOClient(session=_FakeSession([_FakeResp(200, {"ok": 1})]))
    with pytest.raises(RaiderIOError, match="retries exhausted"):
        await client._get("/characters/profile", {"name": "X"}, retries=-1)
