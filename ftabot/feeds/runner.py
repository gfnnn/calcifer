"""The feed poll loop core: fetch → dedup → route → render → publish.

Pure-ish and injectable: it takes the sources, an aiohttp session and a ``send``
coroutine, so the whole pipeline is testable offline with a fake source + a
fake send + a real temp database.

Digest mode: a routing rule with ``{"mode": "digest"}`` in its style_json buffers
events to feed_digest_queue instead of posting immediately. ``flush_digests``
sweeps the queue and posts a single roundup embed per (channel, rule) group.
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

import aiohttp

from .. import publisher, router
from ..config import Config
from ..db import Database
from ..posting import PostBlocked
from .base import FeedEvent, FeedSource

log = logging.getLogger("ftabot.feeds")

SendFn = Callable[..., Awaitable[None]]


async def _content(style: dict, db: Database, source: str) -> str | None:
    """The message content (outside the embed) — role pings + subscriber mentions."""
    parts: list[str] = []
    if ping := style.get("ping"):
        parts.append(str(ping))
    subscribers = await db.source_subscribers(source)
    parts.extend(f"<@{uid}>" for uid in subscribers)
    return " ".join(parts) or None


async def drain_outbox(db: Database, config: Config, send: SendFn, *, rng=None) -> int:
    """Post any queued push events (e.g. Raid-Helper webhooks). Returns count."""
    posted = 0
    for row in await db.pending_outbox():
        # Claim the row before sending so an interleaved drain (the webhook and
        # the feed loop can both run a drain on the same loop) cannot post it twice.
        if not await db.claim_outbox(row["id"]):
            continue  # another drain already took this one
        event = FeedEvent.from_json(row["event_json"])
        style = json.loads(row["style_json"] or "{}")
        decision = router.RouteDecision(channel_id=row["channel_id"], style=style)
        tone = style.get("tone") or config.persona_tone
        embed = publisher.render_embed(event, decision, tone=tone, rng=rng)
        content = await _content(style, db, event.source)
        try:
            await send(row["channel_id"], embed, content)
        except PostBlocked:
            # Blocked before reaching Discord: requeue (no attempt burned) so it
            # posts once the guardrail opens.
            await db.release_outbox(row["id"])
            continue
        except Exception as exc:  # noqa: BLE001 - ambiguous: may have been delivered
            # Discord might have accepted the message before this raised, so we
            # must NOT release-and-instantly-repost. Leave the row claimed; the
            # visibility timeout re-delivers it later, bounded by max attempts.
            log.warning("outbox publish failed (%s): %s", row["id"], exc)
            continue
        await db.mark_outbox_sent(row["id"])
        posted += 1
    return posted


async def poll_once(
    db: Database,
    config: Config,
    sources: list[FeedSource],
    session: aiohttp.ClientSession,
    send: SendFn,
    *,
    rng=None,
) -> int:
    """Run one polling pass over all sources. Returns how many alerts posted."""
    rules = await db.feed_rules()
    muted = await db.muted_sources()
    default_channel = config.feed_default_channel_id
    tone = config.persona_tone
    posted = 0

    for source in sources:
        if source.name in muted:
            continue
        try:
            events = await source.fetch(session)
        except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't stop the rest
            log.warning("feed %s fetch failed: %s", source.name, exc)
            continue

        primed = await db.source_primed(source.name)
        # Oldest first so a channel reads chronologically.
        unseen = list(reversed(await db.filter_unseen(events)))

        for event in unseen:
            # First ever poll for this source: prime the dedup table silently so
            # we never dump a backlog of historical items into the channel.
            if not primed:
                await db.mark_seen(event.source, event.external_id)
                continue

            decision = router.resolve(event, rules, default_channel_id=default_channel)
            if decision is None:
                await db.mark_seen(event.source, event.external_id)  # nowhere to go
                continue

            mode = decision.style.get("mode", "realtime")

            if mode == "digest":
                # Buffer for a periodic roundup instead of posting now.
                await db.enqueue_digest(
                    decision.channel_id,
                    event.source,
                    event.to_json(),
                    json.dumps(decision.style),
                    rule_id=decision.rule_id,
                )
                await db.mark_seen(event.source, event.external_id)
                continue

            # Per-rule tone override (falls back to the global config tone).
            rule_tone = decision.style.get("tone") or tone
            embed = publisher.render_embed(event, decision, tone=rule_tone, rng=rng)
            content = await _content(decision.style, db, event.source)
            try:
                await send(decision.channel_id, embed, content)
            except PostBlocked:
                # Guardrail off/unconfirmed: leave unseen so it posts once live.
                continue
            except Exception as exc:  # noqa: BLE001 - leave unseen so we retry next pass
                log.warning("feed publish failed (%s): %s", event.external_id, exc)
                continue
            await db.mark_seen(event.source, event.external_id)
            posted += 1

        # Mark the source primed even when this pass yielded no events, so a
        # normally-silent source (raid reminders, prog) does not swallow its
        # first real event later as if it were the initial backfill.
        if not primed:
            await db.prime_source(source.name)

    # Drain any push events queued by the webhook receiver (backstop; the
    # webhook also drains immediately when a sender is wired).
    posted += await drain_outbox(db, config, send, rng=rng)
    return posted


async def flush_digests(
    db: Database, config: Config, send: SendFn, *, rng=None
) -> int:
    """Post weekly digest roundups for buffered digest-mode items.

    Groups pending items by (channel_id, rule_id), renders one summary embed
    per group, posts it, then marks the batch flushed. Returns the number of
    groups posted.
    """
    rows = await db.pending_digests()
    if not rows:
        return 0

    # Group by (channel_id, rule_id) — same destination + same rule = same post.
    groups: dict[tuple[str, int | None], list[dict]] = {}
    for row in rows:
        key = (row["channel_id"], row["rule_id"])
        groups.setdefault(key, []).append(row)

    posted = 0
    for (channel_id, _rule_id), group in groups.items():
        events = [FeedEvent.from_json(r["event_json"]) for r in group]
        style = json.loads(group[0]["style_json"] or "{}")
        tone = style.get("tone") or config.persona_tone
        embed = publisher.render_digest_embed(events, style, tone=tone, rng=rng)
        source = events[0].source if events else ""
        content = await _content(style, db, source)
        try:
            await send(channel_id, embed, content)
        except PostBlocked:
            # Guardrail off/unconfirmed: leave buffered so it flushes once live.
            continue
        except Exception as exc:  # noqa: BLE001 - skip this group, try again next flush
            log.warning("digest flush failed for %s: %s", channel_id, exc)
            continue
        await db.flush_digest_batch([r["id"] for r in group])
        posted += 1

    return posted


def build_sources(config: Config) -> list[FeedSource]:
    """Instantiate the feed sources enabled by config."""
    from .wowhead import WowheadSource

    sources: list[FeedSource] = []
    if config.wowhead_enabled:
        sources.append(WowheadSource(config))
    if config.blizzard_news_enabled:
        from .rss_news import RssNewsSource

        sources.append(
            RssNewsSource(
                config,
                name="blizzard",
                url=config.blizzard_news_rss_url,
                event_type="blizzard.news",
                tier="high",
            )
        )
    if config.mplus_feed_enabled:
        from .raiderio_mplus import RaiderIOMythicSource

        sources.append(RaiderIOMythicSource(config))
    if config.raid_reminder_enabled and config.wowaudit_enabled:
        from .wowaudit_raids import WowAuditRaidSource

        sources.append(WowAuditRaidSource(config))
    if config.prog_feed_enabled and config.warcraftlogs_client_id:
        from .wcl_prog import WclProgSource

        sources.append(WclProgSource(config))
    if config.affixes_feed_enabled:
        from .raiderio_affixes import RaiderIOAffixesSource

        sources.append(RaiderIOAffixesSource(config))
    if config.realm_progress_feed_enabled and config.realm_progress_raids:
        from .raiderio_realm_progress import RaiderIORealmProgressSource

        sources.append(RaiderIORealmProgressSource(config))
    return sources
