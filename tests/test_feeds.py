"""Tests for the integration hub: persona, Wowhead RSS, router, runner."""

import random

from ftabot import persona, publisher, router
from ftabot.config import Config
from ftabot.feeds.base import FeedEvent, FeedSource
from ftabot.feeds.runner import drain_outbox, poll_once
from ftabot.feeds.wowhead import parse_rss
from ftabot.posting import PostBlocked

# --- persona ----------------------------------------------------------------


def test_persona_tone_off_is_silent():
    assert persona.flavour("wowhead.news", tone="off") is None


def test_persona_lines_obey_the_no_em_dash_house_rule():
    # Calcifer never writes em dashes (design-system §5); the persona flavour
    # bank must follow the same rule as brand._BANKS, since publisher renders
    # these lines straight into feed embeds.
    for key, lines in persona._LINES.items():
        for line in lines:
            assert "—" not in line, f"em dash in persona bank {key}: {line!r}"


def test_persona_full_always_speaks():
    line = persona.flavour("wowhead.patch-notes", tone="full", rng=random.Random(1))
    assert isinstance(line, str) and line


def test_authored_feed_titles_obey_the_no_em_dash_house_rule():
    """Titles Calcifer composes (raid/prog reminders + /feed samples) carry no
    em dash, so every surface reads as one voice. Relayed external headlines
    (a Wowhead title) are deliberately left untouched and not checked here.
    """
    from ftabot.cogs.feeds import _SAMPLES
    from ftabot.feeds.wcl_prog import prog_events
    from ftabot.feeds.wowaudit_raids import _reminder_event
    from ftabot.services.raidhelper import _TITLES

    for tmpl in _TITLES.values():
        assert "—" not in tmpl, tmpl
    for _etype, title, summary, _cats, _sev in _SAMPLES.values():
        assert "—" not in title, title
        assert "—" not in summary, summary

    bosses = [{"boss": "Boss", "difficulty": "Mythic", "pulls": 5,
               "best_pct": 4.0, "killed": False}]
    assert all("—" not in e.title for e in prog_events("RPT", bosses, threshold=30.0))
    ev = _reminder_event({"instance": "Palace", "difficulty": "Heroic",
                          "present": 18, "total": 20, "start": None})
    assert "—" not in ev.title


def test_render_embed_sanitizes_calcifer_line_but_not_relayed_title(monkeypatch):
    # The persona flavour line is copy Calcifer authors, so it is sanitized to
    # the house style (no em dash). The relayed external title is left verbatim.
    monkeypatch.setattr(persona, "flavour", lambda *a, **k: "hot — and fast")
    event = FeedEvent(
        source="wowhead", event_type="wowhead.news", external_id="x",
        title="External headline — verbatim", summary="Body.",
        authored=False,  # external relay: title must be left verbatim
    )
    decision = router.RouteDecision(channel_id="1", style={"persona": True})
    embed = publisher.render_embed(event, decision, tone="full")
    assert "—" not in (embed.description or "")
    assert "—" in embed.title  # external content untouched


def test_persona_colors_by_severity():
    assert persona.color_for("info") == persona.BLUE_FLAME
    assert persona.color_for("warning") == persona.AMBER
    assert persona.color_for("critical") == persona.EMBER_RED


# --- Wowhead RSS parsing ----------------------------------------------------

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Wowhead News</title>
  <item>
    <title>Patch 12.0.7 PTR Notes</title>
    <link>https://www.wowhead.com/news/ptr-1</link>
    <guid>https://www.wowhead.com/news/ptr-1</guid>
    <pubDate>Wed, 03 Jun 2026 10:00:00 +0000</pubDate>
    <description>&lt;p&gt;Class changes &amp; more.&lt;/p&gt;</description>
    <category>News</category>
  </item>
  <item>
    <title>Blue Post: Hotfixes</title>
    <link>https://www.wowhead.com/news/blue-1</link>
    <guid>blue-1</guid>
    <pubDate>Tue, 02 Jun 2026 09:00:00 +0000</pubDate>
    <description>A blue tracker update.</description>
    <category>Blue Post</category>
  </item>
</channel></rss>
"""


def test_parse_rss_classifies_and_cleans():
    events = parse_rss(_RSS)
    assert len(events) == 2
    patch, blue = events
    assert patch.event_type == "wowhead.patch-notes"
    assert patch.external_id == "https://www.wowhead.com/news/ptr-1"
    assert patch.summary == "Class changes & more."  # tags stripped, entities decoded
    assert patch.published_at.startswith("2026-06-03")
    assert blue.event_type == "wowhead.blue-post"


def test_parse_rss_tolerates_garbage():
    assert parse_rss("not xml at all") == []


# --- router -----------------------------------------------------------------


def _event(**kw):
    base = dict(source="wowhead", event_type="wowhead.news", external_id="x", title="T")
    base.update(kw)
    return FeedEvent(**base)


def test_router_matches_event_type_and_keywords():
    rules = [
        {"id": 1, "source": "wowhead", "event_type": "wowhead.patch-notes",
         "match": {}, "channel_id": "111", "style": {"color": "amber"}},
        {"id": 2, "source": "wowhead", "event_type": None,
         "match": {"contains": ["hunter"]}, "channel_id": "222", "style": {}},
    ]
    patch = _event(event_type="wowhead.patch-notes", title="Patch notes")
    assert router.resolve(patch, rules).channel_id == "111"

    hunter = _event(title="Hunter rework incoming")
    assert router.resolve(hunter, rules).channel_id == "222"


def test_router_falls_back_to_default_then_drops():
    misc = _event(title="Nothing matches")
    assert router.resolve(misc, [], default_channel_id="999").channel_id == "999"
    assert router.resolve(misc, []) is None


def test_router_category_filter():
    rules = [{"id": 1, "source": "wowhead", "event_type": None,
              "match": {"categories": ["Blue Post"]}, "channel_id": "5", "style": {}}]
    blue = _event(categories=["Blue Post"])
    news = _event(categories=["News"])
    assert router.resolve(blue, rules).channel_id == "5"
    assert router.resolve(news, rules) is None


# --- publisher --------------------------------------------------------------


def test_render_embed_applies_style_and_persona():
    event = _event(title="Big News", summary="Details here", url="https://x", severity="warning")
    decision = router.RouteDecision(channel_id="1", style={"color": "amber", "persona": True})
    embed = publisher.render_embed(event, decision, tone="full", rng=random.Random(0))
    assert embed.title == "Big News"
    assert embed.color.value == persona.AMBER
    assert "Details here" in embed.description


def test_render_embed_sanitizes_authored_copy():
    # Calcifer-authored titles/summaries obey the no-em-dash house rule at render
    # time (the safety net under the source builders).
    event = _event(
        title="Raid reminder — Manaforge", summary="Starts soon — sign up", authored=True
    )
    decision = router.RouteDecision(channel_id="1", style={"persona": False})
    embed = publisher.render_embed(event, decision, tone="off")
    assert "—" not in embed.title
    assert "—" not in (embed.description or "")


def test_render_embed_preserves_relayed_external_copy():
    # A relayed Wowhead headline is reproduced verbatim, em dash and all — we
    # never rewrite someone else's words.
    headline = "Patch 11.2 — The Edge of Chaos"
    event = _event(title=headline, summary="From the source — untouched.", authored=False)
    decision = router.RouteDecision(channel_id="1", style={"persona": False})
    embed = publisher.render_embed(event, decision, tone="off")
    assert embed.title == headline
    assert "untouched" in embed.description


def test_wowhead_events_are_relayed_not_authored():
    # Wowhead items are external relays, so they are exempt from sanitization.
    for event in parse_rss(_RSS):
        assert event.authored is False


def test_feed_source_titles_render_without_em_dashes():
    # The authored feed builders + the publisher together never emit an em dash.
    from ftabot.feeds.wcl_prog import prog_events
    from ftabot.feeds.wowaudit_raids import _reminder_event
    from ftabot.services.raidhelper import parse_webhook

    built = [
        _reminder_event({"instance": "Manaforge", "difficulty": "Mythic",
                         "date": "2026-06-06", "time": "20:00", "present": 18, "total": 20}),
        prog_events("rep1", [{"boss": "Final Boss", "difficulty": "Mythic",
                              "pulls": 12, "best_pct": 4.0, "killed": False}])[0],
        parse_webhook({"type": "create", "event": {"id": "1", "title": "Heroic Night"}}),
    ]
    decision = router.RouteDecision(channel_id="1", style={"persona": False})
    for event in built:
        embed = publisher.render_embed(event, decision, tone="off")
        assert "—" not in embed.title, event.title


# --- runner end-to-end ------------------------------------------------------


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
        self.sent.append((channel_id, embed.title))


def _cfg(channel="42"):
    import dataclasses
    return dataclasses.replace(Config(), feed_default_channel_id=channel, persona_tone="off")


async def test_runner_primes_first_then_posts_new(db):
    cfg = _cfg()
    e1 = _event(external_id="a", title="First")
    send = _Recorder()

    # First poll: priming — nothing posts, but everything is marked seen.
    posted = await poll_once(db, cfg, [_FakeSource([e1])], session=None, send=send)
    assert posted == 0 and send.sent == []

    # Second poll: the same item is deduped; a new item posts.
    e2 = _event(external_id="b", title="Second")
    posted = await poll_once(db, cfg, [_FakeSource([e2, e1])], session=None, send=send)
    assert posted == 1
    assert send.sent == [("42", "Second")]


async def test_runner_dedups_across_polls(db):
    cfg = _cfg()
    send = _Recorder()
    await poll_once(db, cfg, [_FakeSource([_event(external_id="seed")])], None, send)  # prime
    new = _event(external_id="new", title="Fresh")
    await poll_once(db, cfg, [_FakeSource([new])], None, send)
    # Polling again with the same item must not repost it.
    await poll_once(db, cfg, [_FakeSource([new])], None, send)
    assert [s[1] for s in send.sent] == ["Fresh"]


async def test_runner_primes_a_silent_source_so_first_event_is_not_lost(db):
    # A source that emits nothing on early polls (raid reminders, prog) must
    # still be primed, so its first *real* event later is posted, not swallowed.
    cfg = _cfg()
    send = _Recorder()

    # First poll returns no events at all — but the source is now primed.
    posted = await poll_once(db, cfg, [_FakeSource([])], session=None, send=send)
    assert posted == 0
    assert await db.source_primed("wowhead") is True

    # The first real event now posts instead of being treated as a backfill.
    first = _event(external_id="real-1", title="First real")
    posted = await poll_once(db, cfg, [_FakeSource([first])], session=None, send=send)
    assert posted == 1
    assert send.sent == [("42", "First real")]


# --- outbox: claim-before-send prevents an interleaved double-post -----------


def _outbox_event():
    return FeedEvent(
        source="raidhelper", event_type="raidhelper.event",
        external_id="e1", title="Raid night",
    )


async def test_outbox_claim_is_atomic(db):
    rid = await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")

    assert await db.claim_outbox(rid) is True   # the first drain claims it
    assert await db.claim_outbox(rid) is False  # an interleaved drain cannot
    await db.release_outbox(rid)                 # a failed send requeues it
    assert await db.claim_outbox(rid) is True


async def test_drain_outbox_posts_each_row_once(db):
    cfg = _cfg()
    await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")
    send = _Recorder()

    assert await drain_outbox(db, cfg, send) == 1
    # A second drain (e.g. the feed loop, after the webhook already drained) finds
    # the row already claimed and does not re-send it.
    assert await drain_outbox(db, cfg, send) == 0
    assert [s[0] for s in send.sent] == ["chan"]


# --- guardrail-blocked sends must not consume the event ----------------------


class _BlockedSender:
    """Mimics the posting guardrail being off: every send is blocked."""

    def __init__(self):
        self.attempts = 0

    async def __call__(self, channel_id, embed, content=None):
        self.attempts += 1
        raise PostBlocked(channel_id)


async def test_runner_does_not_consume_events_blocked_by_guardrail(db):
    cfg = _cfg()
    blocked = _BlockedSender()
    # Prime the source first (priming never reaches the sender).
    await poll_once(db, cfg, [_FakeSource([_event(external_id="seed")])], None, blocked)

    # A real event arrives while the guardrail blocks it: nothing posts and the
    # event is NOT marked seen, so it survives to post once the gate opens.
    new = _event(external_id="fresh", title="Fresh")
    posted = await poll_once(db, cfg, [_FakeSource([new])], None, blocked)
    assert posted == 0
    assert blocked.attempts >= 1

    recorder = _Recorder()
    posted = await poll_once(db, cfg, [_FakeSource([new])], None, recorder)
    assert posted == 1
    assert recorder.sent == [("42", "Fresh")]


async def test_drain_outbox_requeues_when_blocked_by_guardrail(db):
    cfg = _cfg()
    await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")
    blocked = _BlockedSender()
    assert await drain_outbox(db, cfg, blocked) == 0  # nothing posts

    # The row was released, not consumed — it drains once the gate opens.
    recorder = _Recorder()
    assert await drain_outbox(db, cfg, recorder) == 1
    assert [s[0] for s in recorder.sent] == ["chan"]


# --- outbox: visibility-timeout delivery (no double-post, no loss) -----------


class _FlakySender:
    """Raises an ambiguous failure for the first N calls, then records."""

    def __init__(self, fail_times=1):
        self.calls = 0
        self.fail_times = fail_times
        self.sent = []

    async def __call__(self, channel_id, embed, content=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("connection reset after POST")
        self.sent.append((channel_id, embed.title))


async def _age_outbox(db, row_id):
    # Pretend the visibility timeout elapsed since the row was claimed.
    await db.conn.execute(
        "UPDATE feed_outbox SET claimed_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (row_id,),
    )
    await db.conn.commit()


async def test_outbox_ambiguous_failure_does_not_double_post(db):
    cfg = _cfg()
    await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")
    flaky = _FlakySender(fail_times=1)
    # The send raises *after* Discord may have accepted it: the row is left
    # claimed, not released, so an immediate re-drain cannot re-post it.
    assert await drain_outbox(db, cfg, flaky) == 0
    assert await drain_outbox(db, cfg, flaky) == 0
    assert flaky.sent == []
    assert flaky.calls == 1  # the second drain didn't even reach the sender


async def test_outbox_redelivers_after_visibility_timeout(db):
    cfg = _cfg()
    rid = await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")
    flaky = _FlakySender(fail_times=1)
    assert await drain_outbox(db, cfg, flaky) == 0  # ambiguous fail, left claimed
    await _age_outbox(db, rid)
    # Now reclaimable: the retry succeeds exactly once, then never again.
    assert await drain_outbox(db, cfg, flaky) == 1
    assert [s[0] for s in flaky.sent] == ["chan"]
    assert await drain_outbox(db, cfg, flaky) == 0  # delivered = terminal


async def test_outbox_dead_letters_after_max_attempts(db):
    from ftabot.db import _OUTBOX_MAX_ATTEMPTS

    cfg = _cfg()
    rid = await db.enqueue_outbox("chan", _outbox_event().to_json(), "{}")
    always_fail = _FlakySender(fail_times=99)
    for _ in range(_OUTBOX_MAX_ATTEMPTS + 2):
        await drain_outbox(db, cfg, always_fail)
        await _age_outbox(db, rid)
    # Retries are bounded, not infinite, and the row dead-letters.
    assert always_fail.calls == _OUTBOX_MAX_ATTEMPTS
    assert await db.pending_outbox() == []
