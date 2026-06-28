"""Blizzard (Battle.net) Game Data + Profile API client — optional.

Used to enrich members with authoritative guild-rank, race, faction, character
level and equipped item level. Requires a client id/secret from
https://develop.battle.net/access/clients and uses the OAuth 2.0
client-credentials flow.

Docs: https://develop.battle.net/documentation/world-of-warcraft
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from ..models import Member, normalize_realm
from ._http import OAuthClientCredentials, retry_after_seconds

OAUTH_TOKEN_URL = "https://oauth.battle.net/token"


def _api_host(region: str) -> str:
    region = region.lower()
    if region == "cn":  # China uses a separate gateway entirely.
        return "https://gateway.battlenet.com.cn"
    return f"https://{region}.api.blizzard.com"


class BlizzardError(RuntimeError):
    pass


class BlizzardClient(OAuthClientCredentials):
    """Minimal async client for the WoW guild roster + character endpoints."""

    _token_url = OAUTH_TOKEN_URL
    _token_error = BlizzardError

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        region: str = "eu",
        locale: str = "en_GB",
        session: aiohttp.ClientSession | None = None,
    ):
        super().__init__(session=session)
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.lower()
        self.locale = locale

    async def _get(
        self, path: str, *, namespace: str, retries: int = 4
    ) -> dict[str, Any]:
        """GET with a one-shot 401 token re-mint and 429/5xx backoff.

        Blizzard rate-limits under load (a full guild roster + per-character
        enrichment is bursty), so we retry on 429 — honouring ``Retry-After`` —
        and on transient 5xx with exponential backoff, mirroring the Raider.IO
        client. A 401 re-mints the token once (it may have been revoked early).
        """
        url = f"{_api_host(self.region)}{path}"
        params = {"namespace": namespace, "locale": self.locale}
        delay = 1.0
        tried_refresh = False
        for attempt in range(retries + 1):
            token = await self._ensure_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with self.session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 401 and not tried_refresh:
                    tried_refresh = True
                    self._invalidate_token()
                    continue
                if resp.status == 404:
                    raise BlizzardError("not found")
                retryable = resp.status == 429 or 500 <= resp.status < 600
                if retryable and attempt < retries:
                    wait = (
                        retry_after_seconds(resp.headers, fallback=delay)
                        if resp.status == 429
                        else delay
                    )
                    await asyncio.sleep(min(wait, 10.0))
                    delay *= 2
                    continue
                body = await resp.text()
                raise BlizzardError(f"HTTP {resp.status}: {body[:200]}")
        raise BlizzardError("retries exhausted")  # pragma: no cover

    # -- endpoints -----------------------------------------------------------

    async def guild_roster(
        self, name: str, realm: str
    ) -> list[tuple[str, str, int, int]]:
        """Return [(character_name, realm_slug, level, guild_rank), ...]."""
        realm_slug = normalize_realm(realm)
        name_slug = normalize_realm(name)
        data = await self._get(
            f"/data/wow/guild/{realm_slug}/{name_slug}/roster",
            namespace=f"profile-{self.region}",
        )
        out: list[tuple[str, str, int, int]] = []
        for entry in data.get("members", []):
            char = entry.get("character") or {}
            char_name = char.get("name")
            if not char_name:
                continue
            char_realm = (char.get("realm") or {}).get("slug", realm_slug)
            out.append(
                (
                    char_name,
                    char_realm,
                    int(char.get("level", 0)),
                    int(entry.get("rank", 99)),
                )
            )
        return out

    async def enrich_character(self, member: Member) -> Member:
        """Fill race, faction, level and equipped item level from Blizzard."""
        try:
            # Fail fast on the per-member hot path: this runs once per character
            # in a sequential loop, so a sustained Blizzard outage must not pay
            # the full backoff per member (that would stall the whole sync).
            # retries=1 still rides out a brief 429 (honouring Retry-After); the
            # critical single guild_roster call keeps the generous default.
            summary = await self._get(
                f"/profile/wow/character/{member.realm_slug}/"
                f"{member.character_name.lower()}",
                namespace=f"profile-{self.region}",
                retries=1,
            )
        except BlizzardError:
            return member

        member.race = (summary.get("race") or {}).get("name") or member.race
        member.faction = (summary.get("faction") or {}).get("name") or member.faction
        member.class_name = (
            (summary.get("character_class") or {}).get("name") or member.class_name
        )
        member.active_spec = (
            (summary.get("active_spec") or {}).get("name") or member.active_spec
        )
        if summary.get("level") is not None:
            member.level = int(summary["level"])
        if summary.get("equipped_item_level") is not None:
            member.item_level = float(summary["equipped_item_level"])
        return member
