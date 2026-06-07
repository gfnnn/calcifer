"""Phase 5 tests: feed-rule management, mutes, and ping content."""

import dataclasses

from ftabot import router
from ftabot.config import Config
from ftabot.feeds.base import FeedEvent, FeedSource
from ftabot.feeds.runner import poll_once

# --- DB rule management -----------------------------------------------------


async def test_add_list_delete_feed_rule(db):
    rid = await db.add_feed_rule(
        "wowhead", "123", event_type="wowhead.patch-notes",
        match={"contains": ["hunter"]}, style={"color": "amber"},
    )
    rules = await db.all_feed_rules()
    assert len(rules) == 1 and rules[0]["id"] == rid
    assert rules[0]["channel_id"] == "123"
    assert rules[0]["match"]["contains"] == ["hunter"]

    assert await db.delete_feed_rule(rid) is True
    assert await db.all_feed_rules() == []
    assert await db.delete_feed_rule(rid) is False


async def test_disable_enable_feed_rule(db):
    rid = await db.add_feed_rule("wowhead", "123")
    # Active by default: routing sees it, list shows it enabled.
    assert [r["id"] for r in await db.feed_rules()] == [rid]
    assert (await db.all_feed_rules())[0]["enabled"] is True

    # Disabling keeps it in the management list but hides it from the router.
    assert await db.set_feed_rule_enabled(rid, False) is True
    assert await db.feed_rules() == []
    listed = await db.all_feed_rules()
    assert len(listed) == 1 and listed[0]["enabled"] is False

    # Re-enabling brings it back; an unknown id reports no change.
    assert await db.set_feed_rule_enabled(rid, True) is True
    assert [r["id"] for r in await db.feed_rules()] == [rid]
    assert await db.set_feed_rule_enabled(999, False) is False


async def test_mute_roundtrip(db):
    await db.mute_source("raiderio_mplus")
    assert "raiderio_mplus" in await db.muted_sources()
    assert await db.unmute_source("raiderio_mplus") is True
    assert await db.muted_sources() == set()


# --- stored rule drives routing --------------------------------------------


def test_router_uses_stored_rule_over_default():
    rule = {"id": 1, "source": "wowhead", "event_type": "wowhead.patch-notes",
            "match": {}, "channel_id": "999", "style": {"color": "amber"}}
    patch = FeedEvent("wowhead", "wowhead.patch-notes", "x", "Patch")
    news = FeedEvent("wowhead", "wowhead.news", "y", "News")
    # The patch-notes rule wins for a patch; news falls back to the default.
    assert router.resolve(patch, [rule], default_channel_id="111").channel_id == "999"
    assert router.resolve(news, [rule], default_channel_id="111").channel_id == "111"


# --- mute is honoured by the runner ----------------------------------------


class _Source(FeedSource):
    def __init__(self, name, events):
        self.name = name
        self._events = events

    async def fetch(self, session):
        return self._events


class _Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, channel_id, embed, content=None):
        self.sent.append((channel_id, embed.title, content))


def _cfg():
    return dataclasses.replace(Config(), feed_default_channel_id="42", persona_tone="off")


async def test_runner_skips_muted_source(db):
    cfg = _cfg()
    src = _Source("wowhead", [FeedEvent("wowhead", "wowhead.news", "n1", "Hello")])
    send = _Recorder()

    await poll_once(db, cfg, [src], None, send)  # prime
    await db.mute_source("wowhead")
    # Even with a brand-new item, a muted source posts nothing.
    src._events = [FeedEvent("wowhead", "wowhead.news", "n2", "Muted item")]
    posted = await poll_once(db, cfg, [src], None, send)
    assert posted == 0 and send.sent == []


async def test_runner_passes_ping_content(db):
    cfg = _cfg()
    await db.add_feed_rule("wowhead", "77", style={"ping": "<@&555>", "persona": False})
    src = _Source("wowhead", [FeedEvent("wowhead", "wowhead.news", "p1", "Prime")])
    send = _Recorder()

    await poll_once(db, cfg, [src], None, send)  # prime silently
    src._events = [FeedEvent("wowhead", "wowhead.news", "p2", "Pinged")]
    await poll_once(db, cfg, [src], None, send)

    assert send.sent == [("77", "Pinged", "<@&555>")]
