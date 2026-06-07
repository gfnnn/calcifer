"""Feed routing: decide which channel an event lands in, and in what style.

A rule is ``match → route → style``. Rules live in the ``feed_rules`` table
(editable later via the /feed officer commands) and are matched here. When no
rule matches, a configured default channel catches the event so nothing is lost.
The matching itself is pure so it is easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .feeds.base import FeedEvent


@dataclass
class RouteDecision:
    channel_id: str
    style: dict = field(default_factory=dict)
    rule_id: int | None = None  # None = matched the default fallback


def _matches(event: FeedEvent, rule: dict) -> bool:
    """Does ``rule`` apply to ``event``? Source + optional type + filters."""
    if rule.get("source") and rule["source"] != event.source:
        return False
    if rule.get("event_type") and rule["event_type"] != event.event_type:
        return False

    match = rule.get("match") or {}

    wanted = [c.lower() for c in match.get("categories", [])]
    if wanted:
        have = {c.lower() for c in event.categories}
        if not have.intersection(wanted):
            return False

    contains = [k.lower() for k in match.get("contains", [])]
    if contains:
        haystack = " ".join(
            filter(None, [event.title, event.summary, *event.categories])
        ).lower()
        if not any(k in haystack for k in contains):
            return False

    return True


def resolve(
    event: FeedEvent,
    rules: list[dict],
    *,
    default_channel_id: str | None = None,
) -> RouteDecision | None:
    """First matching rule wins; else the default channel; else drop."""
    for rule in rules:
        if _matches(event, rule):
            return RouteDecision(
                channel_id=str(rule["channel_id"]),
                style=rule.get("style") or {},
                rule_id=rule.get("id"),
            )
    if default_channel_id:
        return RouteDecision(channel_id=str(default_channel_id), style={}, rule_id=None)
    return None
