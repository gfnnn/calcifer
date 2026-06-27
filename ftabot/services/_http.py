"""Shared async HTTP plumbing for the WoW community API clients.

Raider.IO, WoW Audit, Blizzard and Warcraft Logs all need the same aiohttp
session lifecycle, and Blizzard + Warcraft Logs share the same OAuth2
client-credentials token cache. Centralising that here means a fix — e.g.
re-minting a token a server revoked before its nominal expiry — lands in every
client at once, instead of being re-derived (and occasionally mis-derived) in
each one.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Self

import aiohttp


def retry_after_seconds(headers: Mapping[str, str], *, fallback: float) -> float:
    """Seconds to wait per a ``Retry-After`` header.

    Handles both RFC 9110 forms — delta-seconds (``Retry-After: 5``) and an
    HTTP-date (``Retry-After: Wed, 21 Oct 2025 07:28:00 GMT``, which
    Cloudflare-fronted APIs send on 429/503) — and falls back to ``fallback``
    when the header is absent or unparseable.
    """
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return fallback
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return fallback
    if when is None:  # pragma: no cover - parsedate_to_datetime raises (never None) on 3.10+
        return fallback
    if when.tzinfo is None:  # an HTTP-date with no zone is implicitly UTC
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class BaseHTTPClient:
    """Shared aiohttp session lifecycle for the API clients.

    Pass an existing ``session`` to share one (it is left open on exit); omit it
    to let the client own and close its own session, used as an async context
    manager. Subclasses override :meth:`_session_headers` to seed default headers
    on a client-owned session.
    """

    def __init__(self, *, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    def _session_headers(self) -> dict[str, str] | None:
        """Default headers for a client-owned session (override per client)."""
        return None

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._session_headers())
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError(
                f"Use {type(self).__name__} as an async context manager."
            )
        return self._session


class OAuthClientCredentials(BaseHTTPClient):
    """A cached OAuth2 client-credentials token, shared by Blizzard + WCL.

    Subclasses set the ``_token_url`` / ``_token_error`` class attributes and the
    ``client_id`` / ``client_secret`` instance attributes. The token is cached
    until ~60s before expiry; :meth:`_invalidate_token` drops it so a 401 can
    re-mint — a token a server revoked early would otherwise fail every call
    until its nominal expiry.
    """

    _token_url: str = ""
    _token_error: type[Exception] = RuntimeError
    # Set by subclass __init__ (declared here for the type checker / contract).
    client_id: str
    client_secret: str

    def __init__(self, *, session: aiohttp.ClientSession | None = None) -> None:
        super().__init__(session=session)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    def _token_fresh(self) -> bool:
        return bool(self._token) and time.time() < self._token_expiry - 60

    async def _ensure_token(self) -> str:
        if self._token_fresh():
            return self._token  # type: ignore[return-value]
        # Serialise minting so concurrent callers that all see a stale token
        # issue a single token POST, not one each; re-check inside the lock in
        # case another coroutine refreshed it while we waited.
        async with self._token_lock:
            if self._token_fresh():
                return self._token  # type: ignore[return-value]
            basic = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()
            async with self.session.post(
                self._token_url,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise self._token_error(
                        f"OAuth token failed: HTTP {resp.status} {body[:200]}"
                    )
                payload = await resp.json()
            self._token = payload["access_token"]
            self._token_expiry = time.time() + int(payload.get("expires_in", 0))
            return self._token

    def _invalidate_token(self) -> None:
        """Drop the cached token so the next call re-mints (used after a 401)."""
        self._token = None
        self._token_expiry = 0.0
