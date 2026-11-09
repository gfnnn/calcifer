"""WoWProgress ranking client (their JSON Data Export).

WoWProgress has no RSS feed; instead it publishes guild ranking data as JSON
(https://www.wowprogress.com/post/37_Data_Export). For a single guild the rank
is the guild URL + ``/json_rank``:

    https://www.wowprogress.com/guild/eu/hellfire/From the Ashes/json_rank
    -> {"score": "...", "world_rank": "...", "area_rank": "...", "realm_rank": "..."}

A tier/rating variant inserts ``/rating.<rating>`` before ``/json_rank``.

**Usage conditions (their terms):** the data is free, but any surface that shows
it must carry a direct link to https://www.wowprogress.com/ and state the data
comes from WoWProgress.com. The single-guild ``json_rank`` is the sanctioned way
to query *one* guild; their terms require the batch export
(https://www.wowprogress.com/export/ranks/, gzip per realm/tier) for *many*
guilds — so this client is deliberately single-guild only.
"""

from __future__ import annotations

from urllib.parse import quote

from ..models import normalize_realm
from ._http import BaseHTTPClient

BASE_URL = "https://www.wowprogress.com"
ATTRIBUTION = "Data from WoWProgress.com"

# Integer-ish fields in a json_rank payload (kept as -1 when missing/garbage so a
# caller can tell "unranked" from "rank 0").
_RANK_FIELDS = ("score", "world_rank", "area_rank", "realm_rank")


def guild_path(region: str, realm: str, name: str, *, rating: str | None = None) -> str:
    """The WoWProgress guild path, e.g. ``/guild/eu/hellfire/From%20the%20Ashes``."""
    realm_slug = normalize_realm(realm)
    base = f"/guild/{region.lower()}/{realm_slug}/{quote(name)}"
    return f"{base}/rating.{rating}" if rating else base


def guild_url(region: str, realm: str, name: str) -> str:
    """The human guild page (a wowprogress.com link, for required attribution)."""
    return f"{BASE_URL}{guild_path(region, realm, name)}"


def parse_rank(payload: dict | None) -> dict | None:
    """Coerce a json_rank payload to ints, or ``None`` if it carries no ranks."""
    if not isinstance(payload, dict):
        return None
    out: dict[str, int] = {}
    for key in _RANK_FIELDS:
        raw = payload.get(key)
        try:
            out[key] = int(raw)
        except (TypeError, ValueError):
            out[key] = -1
    # All-missing (e.g. an unranked guild returns {}) -> nothing to report.
    if all(v < 0 for v in out.values()):
        return None
    return out


class WowProgressClient(BaseHTTPClient):
    """Async WoWProgress ranking client. Use as an async context manager."""

    def _session_headers(self) -> dict[str, str]:
        # WoWProgress sits behind Cloudflare and 403s default fetchers, so send a
        # real User-Agent (and accept JSON) on a client-owned session.
        return {
            "Accept": "application/json",
            "User-Agent": "ftabot/1.0 (+https://github.com/gfnnn/calcifer)",
        }

    async def guild_rank(
        self, region: str, realm: str, name: str, *, rating: str | None = None
    ) -> dict | None:
        """Fetch one guild's ranking, or ``None`` if missing/unranked/blocked."""
        url = f"{BASE_URL}{guild_path(region, realm, name, rating=rating)}/json_rank"
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                # WoWProgress serves json_rank as text/plain, so parse leniently.
                payload = await resp.json(content_type=None)
        except (ValueError, OSError):
            return None
        return parse_rank(payload)
