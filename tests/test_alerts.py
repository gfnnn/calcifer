"""Feed alert subscriptions: opt-in per-mention pings."""

from __future__ import annotations

import dataclasses

from ftabot.config import Config
from ftabot.feeds.base import FeedEvent, FeedSource
from ftabot.feeds.runner import poll_once

# ---------------------------------------------------------------------------
# DB CRUD
# ---------------------------------------------------------------------------

async def test_add_subscription_is_idempotent(db):
    await db.add_subscription("111", "wowhead")
    await db.add_subscription("111", "wowhead")  # second add must not raise
    subs = await db.user_subscriptions("111")
    assert subs == ["wowhead"]


async def test_remove_subscription_returns_true_when_removed(db):
    await db.add_subscription("222", "raiderio_mplus")
    removed = await db.remove_subscription("222", "raiderio_mplus")
    assert removed is True
    assert await db.source_subscribers("raiderio_mplus") == []


async def test_remove_subscription_returns_false_when_absent(db):
    assert await db.remove_subscription("333", "nonexistent") is False


async def test_source_subscribers_returns_correct_users(db):
    await db.add_subscription("aaa", "wowhead")
    await db.add_subscription("bbb", "wowhead")
    await db.add_subscription("ccc", "raiderio_mplus")
    subs = await db.source_subscribers("wowhead")
    assert set(subs) == {"aaa", "bbb"}


async def test_user_subscriptions_returns_all_sources(db):
    await db.add_subscription("xyz", "wowhead")
    await db.add_subscription("xyz", "raiderio_mplus")
    sources = await db.user_subscriptions("xyz")
    assert "wowhead" in sources and "raiderio_mplus" in sources


async def test_source_subscribers_empty_when_none(db):
    assert await db.source_subscribers("no_one_subscribed") == []


# ---------------------------------------------------------------------------
# runner: subscription pings appear in content
# ---------------------------------------------------------------------------

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


def _event(**kw):
    base = dict(source="wowhead", event_type="wowhead.news", external_id="x", title="T")
    base.update(kw)
    return FeedEvent(**base)


async def test_subscriber_mention_appended_to_content(db):
    """A subscribed user gets a mention in the content string alongside the embed."""
    await db.add_subscription("999", "wowhead")

    cfg = dataclasses.replace(Config(), feed_default_channel_id="42", persona_tone="off")
    send = _Recorder()

    seed = _event(external_id="s1", title="Seed")
    await poll_once(db, cfg, [_FakeSource([seed])], None, send)  # prime

    new = _event(external_id="s2", title="Hot item")
    await poll_once(db, cfg, [_FakeSource([new])], None, send)

    assert send.sent
    _, _, content = send.sent[-1]
    assert content is not None and "<@999>" in content


async def test_no_subscribers_means_content_is_none(db):
    """Without any subscriptions, content is None (no ping overhead)."""
    cfg = dataclasses.replace(Config(), feed_default_channel_id="42", persona_tone="off")
    send = _Recorder()

    seed = _event(external_id="ns1", title="Seed")
    await poll_once(db, cfg, [_FakeSource([seed])], None, send)  # prime

    new = _event(external_id="ns2", title="News")
    await poll_once(db, cfg, [_FakeSource([new])], None, send)

    assert send.sent
    _, _, content = send.sent[-1]
    assert content is None


async def test_rule_ping_combined_with_subscriber_mention(db):
    """Static role ping from rule style + dynamic subscriber mention both appear."""
    await db.add_subscription("777", "wowhead")
    await db.add_feed_rule("wowhead", "ch77", style={"ping": "@here"})

    cfg = dataclasses.replace(Config(), feed_default_channel_id=None, persona_tone="off")
    send = _Recorder()

    seed = _event(external_id="p1", title="Seed")
    await poll_once(db, cfg, [_FakeSource([seed])], None, send)  # prime

    new = _event(external_id="p2", title="Alert")
    await poll_once(db, cfg, [_FakeSource([new])], None, send)

    assert send.sent
    _, _, content = send.sent[-1]
    assert content is not None
    assert "@here" in content
    assert "<@777>" in content
