"""Raid-Helper integration — normalize its outbound webhooks into FeedEvents.

Raid-Helper POSTs to a callback URL when an event is created / updated / deleted.
We receive it at ``POST /webhooks/raidhelper`` (see web.py), verify a shared
secret, and turn the payload into a FeedEvent the hub can route + post.

The exact payload keys aren't published behind their Cloudflare docs, so the
parser is deliberately tolerant: every field is optional and read from a few
likely names. Validate against a real payload the first time it fires.

Docs: https://raid-helper.dev/documentation/api
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..feeds.base import FeedEvent

_ACTION_ALIASES = {
    "create": "created",
    "created": "created",
    "new": "created",
    "update": "updated",
    "updated": "updated",
    "edit": "updated",
    "delete": "deleted",
    "deleted": "deleted",
    "remove": "deleted",
}

_TITLES = {
    "created": "📅 New event: {title}",
    "updated": "✏️ Event updated: {title}",
    "deleted": "❌ Event cancelled: {title}",
}


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _to_epoch(value: Any) -> int | None:
    """Parse a start time (epoch seconds or ISO string) into epoch seconds."""
    if value is None:
        return None
    try:  # epoch seconds (Raid-Helper's usual form)
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp())
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_start(value: Any) -> str | None:
    """Render a start time from an epoch (s) or ISO string into a Discord stamp."""
    if value is None:
        return None
    ts = _to_epoch(value)
    if ts is not None:
        return f"<t:{ts}:F> (<t:{ts}:R>)"
    return str(value)


def parse_webhook(payload: dict, *, action: str | None = None) -> FeedEvent | None:
    """Normalize a Raid-Helper webhook body into a FeedEvent (or None)."""
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload

    raw_action = (
        action
        or _first(payload, "type", "action", "event", "eventType")
        or _first(event, "type", "action")
        or "updated"
    )
    norm = _ACTION_ALIASES.get(str(raw_action).lower(), "updated")

    event_id = _first(event, "id", "eventId", "messageId")
    if event_id is None:
        return None
    title = _first(event, "title", "name", "eventName", default="Untitled event")

    raw_start = _first(event, "startTime", "time", "date", "start")
    start = _format_start(raw_start)
    start_epoch = _to_epoch(raw_start)
    leader = _first(event, "leaderName", "leader", "organizer")
    signups = _first(event, "signUpCount", "signups", "attendees")
    signup_count = _as_int(signups)
    channel = _first(event, "channelId", "channel")
    url = _first(event, "url", "link")

    summary_bits = []
    if start:
        summary_bits.append(f"🕑 {start}")
    if leader:
        summary_bits.append(f"by **{leader}**")
    if signups is not None:
        summary_bits.append(f"· {signups} signed up")

    severity = "warning" if norm == "deleted" else "info"
    return FeedEvent(
        source="raidhelper",
        event_type=f"raidhelper.{norm}",
        external_id=f"raidhelper-{event_id}-{norm}",
        title=_TITLES.get(norm, "{title}").format(title=title),
        url=url,
        summary=" ".join(summary_bits) or None,
        categories=["Events"],
        severity=severity,
        # Structured fields the Weekly Events board persists + renders from. The
        # webhook payload is tolerant, so any of these may be None.
        extra={
            "event_id": str(event_id),
            "action": norm,
            "channel_id": channel,
            "title_plain": title,
            "start_epoch": start_epoch,
            "leader": leader,
            "signup_count": signup_count,
            "url": url,
        },
    )
