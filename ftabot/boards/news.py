"""News Highlights — Calcifer's two-zone news page, as a living board.

Replaces the news bots that each re-post the same article: the Wowhead feed
archives items into ``news_items`` (deduped across the bots' worth of
duplicates for free), and this board renders them as one calm page that edits
itself in place whenever the feed picks up a new post. Two priority zones:

* **Top stories** — the high-tier drops (retail blue-tracker posts, live patch
  notes/hotfixes, general WoW news), the larger list, freshest first.
* **PTR & Blizzard** — the lower-priority corner (test-realm coverage, other
  Blizzard games), a shorter list underneath.

The split is the tier recorded at archive time (``feeds/wowhead.news_tier``);
per-board ``config_json`` keys ``highlights``/``latest`` resize the two zones.
Renders straight from the DB (no network I/O of its own). The news *headlines*
are relayed external text, so they are shown verbatim; only Calcifer's own
framing (the header, zone labels, signoff) obeys the no-em-dash house rule.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import discord

from .. import brand
from .base import Board, BoardPayload, register

NEWS_TYPE = "news_highlights"
_DEFAULT_HIGHLIGHTS = 12  # the high-priority zone is the page's larger list
_DEFAULT_LATEST = 6  # the PTR/Blizzard corner stays short

# Per-event-type glyphs (unicode fallbacks; the cog/board layer can override by
# custom emoji name later, like the rest of brand.ICONS).
_TYPE_GLYPH = {
    "wowhead.patch-notes": "🛠️",
    "wowhead.blue-post": "🔵",
    "wowhead.news": "📰",
    "wowhead.ptr": "🧪",
    "wowhead.blizzard": "❄️",
    "blizzard.news": "📣",
}


def _glyph(event_type: str | None) -> str:
    return _TYPE_GLYPH.get(event_type or "", "📰")


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _clean_title(title: str) -> str:
    """A headline safe for one masked-link line (no newlines / link-breaking ])."""
    return " ".join(title.split()).replace("]", ")").strip()


def _link(item: dict) -> str:
    title = _clean_title(item["title"])
    return f"[{title}]({item['url']})" if item.get("url") else title


def _dated_line(item: dict, *, glyph: str = "") -> str:
    ts = _epoch(item.get("published_at"))
    when = f" · <t:{ts}:R>" if ts else ""
    lead = f"{glyph} " if glyph else ""
    return f"{lead}{_link(item)}{when}"


class NewsHighlightsBoard(Board):
    board_type = NEWS_TYPE

    async def render(self, db, config, board_config, emoji=None) -> BoardPayload | None:
        n_high = int(board_config.get("highlights", _DEFAULT_HIGHLIGHTS))
        n_low = int(board_config.get("latest", _DEFAULT_LATEST))

        top = await db.recent_news(limit=n_high, tier="high")
        lower = await db.recent_news(limit=n_low, tier="low")
        if not top and not lower:
            return None  # nothing archived yet; post nothing

        sections: list[str] = []
        if top:
            lines = [_dated_line(i, glyph=_glyph(i.get("event_type"))) for i in top]
            sections.append("**🔥 Top stories**\n" + "\n".join(lines))
        if lower:
            lines = [_dated_line(i, glyph=_glyph(i.get("event_type"))) for i in lower]
            sections.append("**🧪 PTR & Blizzard**\n" + "\n".join(lines))

        signoff = (
            brand.Voice(tone=config.persona_tone).line("board_signoff")
            or "Kept warm by Calcifer."
        )

        embed = discord.Embed(
            title="📰 News from the wider world",
            description="\n\n".join(sections),
            color=brand.BLUE_FLAME,  # Calcifer relaying an external feed
        )
        embed.set_footer(text=brand.sanitize(f"{signoff} Read at your leisure."))
        embed.timestamp = datetime.now(timezone.utc)

        # Fingerprint only the displayed set + order (ids and tier), so the board
        # re-edits when the news changes but not merely to bump a relative stamp.
        material = [
            (i["external_id"], i.get("tier"))
            for i in (top + lower)
        ]
        fingerprint = hashlib.sha1(
            json.dumps(material, ensure_ascii=False).encode()
        ).hexdigest()
        return BoardPayload(fingerprint=fingerprint, embed=embed)


register(NewsHighlightsBoard())
