"""Digest mode: events buffered, then flushed as weekly roundup embeds."""

from __future__ import annotations

import dataclasses
import random

from ftabot import publisher
from ftabot.config import Config
from ftabot.feeds.base import FeedEvent, FeedSource
from ftabot.feeds.runner import flush_digests, poll_once

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(**kw) -> FeedEvent:
    base = dict(source="wowhead", event_type="wowhead.news", external_id="x", title="T")
    base.update(kw)
    return FeedEvent(**base)


class _FakeSource(FeedSource):
    name = "wowhead"

    def __init__(self, events):
        self._events = events

    async def fetch(self, session):
        return self._events


class _Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, channel_id, embed, content=None):
        self.sent.append((channel_id, embed, content))


def _cfg(**kw):
    defaults = dict(feed_default_channel_id="42", persona_tone="off", digest_flush_hours=168)
    defaults.update(kw)
    return dataclasses.replace(Config(), **defaults)


async def _seed_rule(db, *, mode: str = "digest", channel: str = "99"):
    await db.add_feed_rule(
        "wowhead",
        channel,
        style={"mode": mode},
    )


# ---------------------------------------------------------------------------
# DB: digest queue
# ---------------------------------------------------------------------------

async def test_enqueue_and_pending(db):
    eid = await db.enqueue_digest("ch1", "wowhead", '{"a":1}', '{}', rule_id=7)
    assert eid > 0
    rows = await db.pending_digests()
    assert len(rows) == 1 and rows[0]["channel_id"] == "ch1"
    assert rows[0]["source"] == "wowhead"


async def test_flush_digest_batch_marks_flushed(db):
    eid = await db.enqueue_digest("ch1", "wowhead", '{}', '{}')
    await db.flush_digest_batch([eid])
    assert await db.pending_digests() == []


async def test_pending_digests_channel_filter(db):
    await db.enqueue_digest("ch1", "s", '{}', '{}')
    await db.enqueue_digest("ch2", "s", '{}', '{}')
    only = await db.pending_digests(channel_id="ch1")
    assert len(only) == 1 and only[0]["channel_id"] == "ch1"


async def test_flush_empty_batch_is_noop(db):
    await db.flush_digest_batch([])  # must not raise
    assert await db.pending_digests() == []


# ---------------------------------------------------------------------------
# runner: digest routing
# ---------------------------------------------------------------------------

async def test_digest_mode_buffers_instead_of_posting(db):
    """Events matching a digest rule are queued, not sent to Discord."""
    await _seed_rule(db)
    cfg = _cfg()
    send = _Recorder()
    e = _event(external_id="d1", title="Digest item")

    # Prime first.
    await poll_once(db, cfg, [_FakeSource([e])], None, send)
    e2 = _event(external_id="d2", title="New item")
    await poll_once(db, cfg, [_FakeSource([e2])], None, send)

    assert send.sent == []  # nothing sent
    rows = await db.pending_digests()
    assert len(rows) == 1 and rows[0]["channel_id"] == "99"


async def test_digest_mode_marks_seen_so_no_double_queue(db):
    await _seed_rule(db)
    cfg = _cfg()
    send = _Recorder()

    seed = _event(external_id="dq_seed", title="Seed")
    await poll_once(db, cfg, [_FakeSource([seed])], None, send)  # prime

    e = _event(external_id="once", title="Only once")
    await poll_once(db, cfg, [_FakeSource([e])], None, send)  # queued
    await poll_once(db, cfg, [_FakeSource([e])], None, send)  # already seen → not re-queued

    rows = await db.pending_digests()
    assert len(rows) == 1  # only queued once


async def test_realtime_rule_still_posts_immediately(db):
    """A rule with mode=realtime (default) still posts right away."""
    await db.add_feed_rule("wowhead", "77", style={"mode": "realtime"})
    cfg = _cfg()
    send = _Recorder()
    e = _event(external_id="rt1", title="Realtime item")

    await poll_once(db, cfg, [_FakeSource([e])], None, send)  # prime
    e2 = _event(external_id="rt2", title="Realtime new")
    await poll_once(db, cfg, [_FakeSource([e2])], None, send)

    assert any(emb.title == "Realtime new" for _, emb, _ in send.sent)


# ---------------------------------------------------------------------------
# flush_digests
# ---------------------------------------------------------------------------

async def test_flush_digests_posts_and_marks_flushed(db):
    event = _event(external_id="f1", title="Roundup item")
    await db.enqueue_digest("ch5", "wowhead", event.to_json(), '{}', rule_id=1)
    await db.enqueue_digest("ch5", "wowhead", _event(external_id="f2", title="Another").to_json(), '{}', rule_id=1)

    send = _Recorder()
    cfg = _cfg()
    n = await flush_digests(db, cfg, send)

    assert n == 1
    assert len(send.sent) == 1
    channel_id, embed, _ = send.sent[0]
    assert channel_id == "ch5"
    assert "2" in embed.title  # "2 items" in title
    assert await db.pending_digests() == []


async def test_flush_digests_groups_by_channel_and_rule(db):
    e = _event(external_id="g1", title="A")
    await db.enqueue_digest("ch1", "s", e.to_json(), '{}', rule_id=1)
    await db.enqueue_digest("ch2", "s", e.to_json(), '{}', rule_id=2)

    send = _Recorder()
    n = await flush_digests(db, _cfg(), send)
    assert n == 2
    channels = {r[0] for r in send.sent}
    assert channels == {"ch1", "ch2"}


async def test_flush_digests_returns_zero_when_empty(db):
    send = _Recorder()
    assert await flush_digests(db, _cfg(), send) == 0
    assert send.sent == []


# ---------------------------------------------------------------------------
# render_digest_embed
# ---------------------------------------------------------------------------

def test_render_digest_mplus_title():
    events = [
        FeedEvent(source="raiderio_mplus", event_type="raiderio.mplus-log",
                  external_id="u", title="Stabby ran a +14 Temple"),
    ]
    embed = publisher.render_digest_embed(events, {}, tone="off")
    assert "Keys this week" in embed.title
    assert "Stabby" in (embed.description or "")


def test_render_digest_wowhead_title():
    events = [
        FeedEvent(source="wowhead", event_type="wowhead.news",
                  external_id="u", title="Big News"),
    ]
    embed = publisher.render_digest_embed(events, {}, tone="off")
    assert "WoW news this week" in embed.title


def test_render_digest_caps_at_20_items():
    events = [
        FeedEvent(source="wowhead", event_type="wowhead.news",
                  external_id=str(i), title=f"Item {i}")
        for i in range(25)
    ]
    embed = publisher.render_digest_embed(events, {}, tone="off")
    assert "5 more" in (embed.description or "")


def test_render_digest_empty_returns_placeholder():
    embed = publisher.render_digest_embed([], {}, tone="off")
    assert embed is not None  # at least returns a valid embed


# ---------------------------------------------------------------------------
# per-rule tone override
# ---------------------------------------------------------------------------

async def test_per_rule_tone_overrides_config(db):
    """A routing rule with {"tone": "off"} silences persona even when the
    global config tone is "full" (which would otherwise always add a quip)."""
    await db.add_feed_rule("wowhead", "55", style={"tone": "off"})
    # Config says full — the runner must use the rule's tone instead.
    cfg = dataclasses.replace(Config(), feed_default_channel_id=None, persona_tone="full")
    send = _Recorder()

    seed = _event(external_id="tone_seed", title="Seed")
    await poll_once(db, cfg, [_FakeSource([seed])], None, send)  # prime

    new = _event(external_id="tone_new", title="Big news", event_type="wowhead.news")
    await poll_once(db, cfg, [_FakeSource([new])], None, send, rng=random.Random(0))

    assert send.sent
    _, embed, _ = send.sent[-1]
    desc = embed.description or ""
    # tone=off → no persona opener, even though config says full
    assert not any(w in desc for w in ["crackling", "sparks", "forge", "gossip", "Ooh"])
