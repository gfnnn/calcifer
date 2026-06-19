"""Tests for the shared HTTP client base (services/_http.py).

Covers the lifecycle + OAuth token cache that every API client now inherits,
and the Retry-After parsing (including the HTTP-date form the old per-client
helper couldn't handle). Network is faked, as elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from ftabot.services._http import (
    BaseHTTPClient,
    OAuthClientCredentials,
    retry_after_seconds,
)

# --- retry_after_seconds ----------------------------------------------------


def test_retry_after_delta_seconds():
    assert retry_after_seconds({"Retry-After": "5"}, fallback=1.0) == 5.0


def test_retry_after_missing_uses_fallback():
    assert retry_after_seconds({}, fallback=2.5) == 2.5


def test_retry_after_garbage_uses_fallback():
    assert retry_after_seconds({"Retry-After": "soon"}, fallback=3.0) == 3.0


def test_retry_after_negative_delta_clamps_to_zero():
    assert retry_after_seconds({"Retry-After": "-5"}, fallback=1.0) == 0.0


def test_retry_after_http_date_future():
    # The form Cloudflare-fronted APIs send; the old float-only helper fell back.
    when = datetime.now(timezone.utc) + timedelta(seconds=60)
    secs = retry_after_seconds({"Retry-After": format_datetime(when)}, fallback=1.0)
    assert 40.0 <= secs <= 61.0  # ~60s, allowing second-resolution + clock drift


def test_retry_after_http_date_past_clamps_to_zero():
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    out = retry_after_seconds({"Retry-After": format_datetime(when)}, fallback=9.0)
    assert out == 0.0


def test_retry_after_http_date_without_zone_is_treated_as_utc():
    # An HTTP-date carrying no timezone is implicitly UTC (RFC 9110). A far-past
    # such date clamps to zero rather than drifting by the local offset.
    out = retry_after_seconds(
        {"Retry-After": "Wed, 21 Oct 2015 07:28:00"}, fallback=9.0
    )
    assert out == 0.0


# --- session lifecycle ------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


async def test_owned_session_is_created_and_closed(monkeypatch):
    created = {}

    class _S:
        def __init__(self, **kwargs):
            created["headers"] = kwargs.get("headers")
            self.closed = False

        async def close(self):
            self.closed = True

    monkeypatch.setattr("ftabot.services._http.aiohttp.ClientSession", _S)

    client = BaseHTTPClient()
    async with client as c:
        assert c is client
        session = c.session
    assert session.closed is True  # client owns it -> closed on exit
    assert created["headers"] is None  # base supplies no default headers


async def test_shared_session_is_left_open():
    fake = _FakeSession()
    async with BaseHTTPClient(session=fake):
        pass
    assert fake.closed is False  # caller-provided -> not closed


def test_session_property_errors_before_enter_and_names_the_class():
    class FooClient(BaseHTTPClient):
        pass

    with pytest.raises(RuntimeError, match="FooClient"):
        _ = FooClient().session


async def test_subclass_session_headers_seed_owned_session(monkeypatch):
    created = {}

    class _S:
        def __init__(self, **kwargs):
            created["headers"] = kwargs.get("headers")

        async def close(self):
            pass

    monkeypatch.setattr("ftabot.services._http.aiohttp.ClientSession", _S)

    class Headered(BaseHTTPClient):
        def _session_headers(self):
            return {"Accept": "application/json"}

    async with Headered():
        pass
    assert created["headers"] == {"Accept": "application/json"}


# --- OAuth client-credentials token cache -----------------------------------


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


class _TokenSession:
    """Serves queued POST responses (one per token mint)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = 0

    def post(self, url, **kwargs):
        resp = self._responses[min(self.posts, len(self._responses) - 1)]
        self.posts += 1
        return resp

    async def close(self):
        pass


class _OAuthError(RuntimeError):
    pass


class _OAuthClient(OAuthClientCredentials):
    _token_url = "https://example.test/token"
    _token_error = _OAuthError

    def __init__(self, session=None):
        super().__init__(session=session)
        self.client_id = "id"
        self.client_secret = "secret"


def _token(value="t", expires_in=3600):
    return _FakeResp(200, {"access_token": value, "expires_in": expires_in})


async def test_token_is_cached_within_its_window():
    sess = _TokenSession([_token("a"), _token("b")])
    client = _OAuthClient(session=sess)
    assert await client._ensure_token() == "a"
    assert await client._ensure_token() == "a"  # served from cache
    assert sess.posts == 1


async def test_invalidate_token_forces_a_remint():
    sess = _TokenSession([_token("a"), _token("b")])
    client = _OAuthClient(session=sess)
    assert await client._ensure_token() == "a"
    client._invalidate_token()
    assert await client._ensure_token() == "b"
    assert sess.posts == 2


async def test_token_mint_failure_raises_the_subclass_error():
    sess = _TokenSession([_FakeResp(401, text="nope")])
    client = _OAuthClient(session=sess)
    with pytest.raises(_OAuthError):
        await client._ensure_token()
