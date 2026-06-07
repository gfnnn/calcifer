"""Warcraft Logs API v2 client — optional.

Fetches a character's best raid parse average (the "best performance average"
across the current raid zone). Requires a v2 client id/secret from
https://www.warcraftlogs.com/api/clients/.

The raid tier (zone) changes every ~6 months, so by default the client
auto-detects the *current* tier from the API (the newest raid zone in the
latest expansion). Pass ``zone_id`` (or set ``WARCRAFTLOGS_ZONE_ID``) only to
pin a specific historical tier.

Docs: https://www.warcraftlogs.com/api/docs
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..models import Member, normalize_realm
from ._http import OAuthClientCredentials

log = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"

# Warcraft Logs uses its own region naming; map ours onto theirs.
_REGION_MAP = {"us": "US", "eu": "EU", "kr": "KR", "tw": "TW", "cn": "CN"}

_ZONE_RANKINGS_QUERY = """
query($name: String!, $server: String!, $region: String!, $zone: Int!) {
  characterData {
    character(name: $name, serverSlug: $server, serverRegion: $region) {
      zoneRankings(zoneID: $zone)
    }
  }
}
"""

# All raid zones plus their expansion, used to auto-detect the current tier.
_ZONES_QUERY = """
query {
  worldData {
    zones {
      id
      name
      frozen
      expansion { id }
    }
  }
}
"""

# Discover top performers for an encounter, filtered to a class/spec/region.
# This is the recruitment scout's candidate-discovery vector.
_CHARACTER_RANKINGS_QUERY = """
query($encounter: Int!, $class: String, $spec: String,
      $metric: CharacterRankingMetricType, $page: Int, $region: String) {
  worldData {
    encounter(id: $encounter) {
      characterRankings(
        className: $class
        specName: $spec
        metric: $metric
        page: $page
        serverRegion: $region
      )
    }
  }
}
"""

# Enumerate the bosses of a raid tier so we can scout against one of them.
_ZONE_ENCOUNTERS_QUERY = """
query($zone: Int!) {
  worldData {
    zone(id: $zone) {
      id
      name
      encounters { id name }
    }
  }
}
"""

# A guild's recent logs + the fights within one, for the live-prog feed.
_GUILD_REPORTS_QUERY = """
query($name: String!, $server: String!, $region: String!, $limit: Int!) {
  reportData {
    reports(guildName: $name, guildServerSlug: $server, guildServerRegion: $region, limit: $limit) {
      data { code title startTime endTime zone { name } }
    }
  }
}
"""

_REPORT_FIGHTS_QUERY = """
query($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      endTime
      fights(killType: Encounters) {
        id name kill difficulty bossPercentage fightPercentage encounterID
      }
    }
  }
}
"""

# Healer specs are ranked by healing; everyone else by damage.
_HEALER_SPECS = {
    "Holy", "Discipline", "Restoration", "Mistweaver", "Preservation",
}


def pick_current_raid_zone(zones: list[dict[str, Any]]) -> int | None:
    """Pick the current raid tier's zone id from ``worldData.zones``.

    Zone ids are assigned in release order, so the current raid is the
    highest-id raid zone within the latest expansion. Mythic+ zones live in the
    same list, so they're filtered out by name.
    """
    raids = [
        z
        for z in zones
        if z.get("id") is not None
        and (z.get("expansion") or {}).get("id") is not None
        and "mythic+" not in (z.get("name") or "").lower()
    ]
    if not raids:
        return None
    latest_expansion = max(z["expansion"]["id"] for z in raids)
    current = [z for z in raids if z["expansion"]["id"] == latest_expansion]
    return max(current, key=lambda z: z["id"])["id"]


def metric_for(spec: str | None, role: str | None) -> str:
    """Pick the Warcraft Logs ranking metric for a spec/role."""
    if (role or "").upper() == "HEALER" or (spec or "") in _HEALER_SPECS:
        return "hps"
    return "dps"


# WCL's region naming is reversed for parsing back (EU -> eu).
_REGION_UNMAP = {v: k for k, v in {"us": "US", "eu": "EU", "kr": "KR", "tw": "TW", "cn": "CN"}.items()}


def _parse_ranking(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one characterRankings entry; tolerant of missing keys."""
    name = entry.get("name")
    if not name:
        return None
    server = entry.get("server") or {}
    realm_display = server.get("name") or entry.get("serverName") or ""
    region_raw = server.get("region") or entry.get("serverRegion") or ""
    guild = entry.get("guild") or {}
    return {
        "name": name,
        "realm_slug": normalize_realm(realm_display) if realm_display else "",
        "region": _REGION_UNMAP.get(region_raw, str(region_raw).lower()),
        "class_name": entry.get("class"),
        "spec": entry.get("spec"),
        "amount": entry.get("amount"),
        "rank_percent": entry.get("rankPercent"),
        "guild_name": guild.get("name") if isinstance(guild, dict) else None,
    }


class WarcraftLogsError(RuntimeError):
    pass


class WarcraftLogsClient(OAuthClientCredentials):
    _token_url = OAUTH_TOKEN_URL
    _token_error = WarcraftLogsError

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        zone_id: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ):
        super().__init__(session=session)
        self.client_id = client_id
        self.client_secret = client_secret
        # When unset, the current tier is auto-detected and cached on first use.
        self.zone_id = zone_id
        self._resolved_zone_id: int | None = zone_id

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Retry once on 401: the cached token may have been revoked early (or our
        # clock skewed), so drop it and re-mint rather than failing every call
        # until the nominal expiry passes.
        for attempt in range(2):
            token = await self._ensure_token()
            async with self.session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status == 401 and attempt == 0:
                    self._invalidate_token()
                    continue
                if resp.status != 200:
                    body = await resp.text()
                    raise WarcraftLogsError(f"HTTP {resp.status}: {body[:200]}")
                payload = await resp.json()
            if "errors" in payload:
                raise WarcraftLogsError(str(payload["errors"])[:200])
            return payload.get("data", {})
        raise WarcraftLogsError("unreachable")  # pragma: no cover

    async def current_zone_id(self) -> int | None:
        """The configured zone, or the auto-detected current tier (cached)."""
        if self._resolved_zone_id is not None:
            return self._resolved_zone_id
        try:
            data = await self._query(_ZONES_QUERY, {})
        except WarcraftLogsError as exc:
            log.warning("WCL zone auto-detect failed: %s", exc)
            return None
        zones = (data.get("worldData") or {}).get("zones") or []
        self._resolved_zone_id = pick_current_raid_zone(zones)
        if self._resolved_zone_id is not None:
            log.info(
                "WCL auto-detected current raid zone: %s", self._resolved_zone_id
            )
        else:
            log.warning("WCL zone auto-detect found no raid zones")
        return self._resolved_zone_id

    async def enrich_character(self, member: Member) -> Member:
        """Set ``best_parse_avg`` from the character's zone rankings."""
        zone = await self.current_zone_id()
        if zone is None:
            return member
        region = _REGION_MAP.get(member.region.lower(), member.region.upper())
        try:
            data = await self._query(
                _ZONE_RANKINGS_QUERY,
                {
                    "name": member.character_name,
                    "server": member.realm_slug,
                    "region": region,
                    "zone": zone,
                },
            )
        except WarcraftLogsError:
            return member

        character = (data.get("characterData") or {}).get("character")
        if not character:
            return member
        rankings = character.get("zoneRankings")
        if isinstance(rankings, dict):
            best = rankings.get("bestPerformanceAverage")
            if best is not None:
                member.best_parse_avg = round(float(best), 1)
        return member

    # -- recruitment scout ---------------------------------------------------

    async def zone_encounters(self, zone_id: int) -> list[dict[str, Any]]:
        """Return ``[{id, name}, ...]`` for a raid tier's bosses (best-effort)."""
        try:
            data = await self._query(_ZONE_ENCOUNTERS_QUERY, {"zone": zone_id})
        except WarcraftLogsError:
            return []
        zone = (data.get("worldData") or {}).get("zone") or {}
        return [
            {"id": e.get("id"), "name": e.get("name")}
            for e in (zone.get("encounters") or [])
            if e.get("id") is not None
        ]

    async def character_rankings(
        self,
        encounter_id: int,
        *,
        class_name: str | None = None,
        spec_name: str | None = None,
        metric: str = "dps",
        region: str | None = None,
        pages: int = 1,
    ) -> list[dict[str, Any]]:
        """Discover top performers for an encounter (the candidate pool).

        Returns one dict per ranked character with name/realm/region/class/spec,
        the metric ``amount``, their ``rank_percent`` and current ``guild_name``.
        Defensive against WCL's schema drift — every field is optional.
        """
        wcl_region = _REGION_MAP.get((region or "").lower(), (region or "").upper() or None)
        # WCL class/spec names carry no spaces ("Death Knight" -> "DeathKnight").
        wcl_class = class_name.replace(" ", "") if class_name else None
        wcl_spec = spec_name.replace(" ", "") if spec_name else None
        out: list[dict[str, Any]] = []
        for page in range(1, max(1, pages) + 1):
            try:
                data = await self._query(
                    _CHARACTER_RANKINGS_QUERY,
                    {
                        "encounter": encounter_id,
                        "class": wcl_class,
                        "spec": wcl_spec,
                        "metric": metric,
                        "page": page,
                        "region": wcl_region,
                    },
                )
            except WarcraftLogsError:
                break
            encounter = (data.get("worldData") or {}).get("encounter") or {}
            rankings = encounter.get("characterRankings")
            if not isinstance(rankings, dict):
                break
            for entry in rankings.get("rankings") or []:
                parsed = _parse_ranking(entry)
                if parsed is not None:
                    out.append(parsed)
            if not rankings.get("hasMorePages"):
                break
        return out

    async def guild_reports(
        self, name: str, realm_slug: str, region: str, *, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Recent logs for a guild (newest first), for the live-prog feed."""
        wcl_region = _REGION_MAP.get(region.lower(), region.upper())
        try:
            data = await self._query(
                _GUILD_REPORTS_QUERY,
                {"name": name, "server": realm_slug, "region": wcl_region, "limit": limit},
            )
        except WarcraftLogsError:
            return []
        reports = (((data.get("reportData") or {}).get("reports") or {}).get("data")) or []
        out: list[dict[str, Any]] = []
        for r in reports:
            if r.get("code"):
                out.append(
                    {
                        "code": r.get("code"),
                        "title": r.get("title"),
                        "startTime": r.get("startTime"),
                        "endTime": r.get("endTime"),
                        "zone": (r.get("zone") or {}).get("name"),
                    }
                )
        return out

    async def report_fights(self, code: str) -> dict[str, Any]:
        """A report's encounter fights + metadata (empty dict on failure)."""
        try:
            data = await self._query(_REPORT_FIGHTS_QUERY, {"code": code})
        except WarcraftLogsError:
            return {}
        return (data.get("reportData") or {}).get("report") or {}

    async def parse_curve(
        self, name: str, realm_slug: str, region: str, zone_ids: list[int]
    ) -> list[float | None]:
        """Best-performance-average per zone (oldest → newest) for a trajectory.

        ``zone_ids`` should be ordered oldest tier first; missing data yields
        ``None`` in that slot so tenure is still visible to the scorer.
        """
        wcl_region = _REGION_MAP.get(region.lower(), region.upper())
        curve: list[float | None] = []
        for zone_id in zone_ids:
            value: float | None = None
            try:
                data = await self._query(
                    _ZONE_RANKINGS_QUERY,
                    {"name": name, "server": realm_slug, "region": wcl_region, "zone": zone_id},
                )
                character = (data.get("characterData") or {}).get("character") or {}
                rankings = character.get("zoneRankings")
                if isinstance(rankings, dict) and rankings.get("bestPerformanceAverage") is not None:
                    value = round(float(rankings["bestPerformanceAverage"]), 1)
            except WarcraftLogsError:
                value = None
            curve.append(value)
        return curve
