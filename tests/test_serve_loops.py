"""Tests for the ``serve.py`` background loop bodies and the post sender.

``serve.py`` was at 25% — only ``_supervise`` / ``_retry_delay`` were covered
(see ``test_serve_supervise.py``). The long-running loops (``_feed_loop``,
``_board_loop``, ``_score_loop``, ``_digest_loop``, ``_periodic_sync``) and the
sender plumbing (``_make_sender`` / ``_wire_webhook_sender``) were untested —
and they deliberately swallow ``Exception`` to stay alive, so a bug inside a
loop body would never surface. These tests exercise one iteration of each.

Technique: each loop is ``while True: <work>; await asyncio.sleep(...)``. We
make ``asyncio.sleep`` a no-op (so the upfront 'settle' sleeps don't actually
wait) and have the patched work raise ``asyncio.CancelledError`` on a chosen
call. ``CancelledError`` is a ``BaseException``, so the loops' ``except
Exception`` never catches it — it cleanly unwinds the loop after the iteration
we care about. Everything is injected (fake bot, patched DB-backed work), so no
network and no real waiting.
"""

from __future__ import annotations

import asyncio

import pytest

from ftabot import serve
from ftabot.config import Config
from ftabot.db import Database
from ftabot.posting import PostBlocked
from ftabot.sync import SyncResult

# --- fakes ------------------------------------------------------------------


class _Channel:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send(self, *, content=None, embed=None):
        self.sent.append((content, embed))


class _Bot:
    """Minimal stand-in for the discord.py client the loops drive."""

    def __init__(self, channel=None, *, fetch_only=False):
        self._channel = channel
        self._fetch_only = fetch_only
        self.fetched: list[int] = []

    async def wait_until_ready(self):
        return None

    def get_channel(self, cid):
        return None if self._fetch_only else self._channel

    async def fetch_channel(self, cid):
        self.fetched.append(cid)
        return self._channel


class _RaiseOnNth:
    """An async callable that returns ``result`` until its ``n``-th call, where
    it raises ``CancelledError`` to break the loop under test."""

    def __init__(self, n: int, result=None):
        self.n = n
        self.result = result
        self.calls = 0
        self.last_args: tuple = ()

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        self.last_args = args
        if self.calls >= self.n:
            raise asyncio.CancelledError
        return self.result


async def _noop_sleep(_seconds):
    """Replacement for asyncio.sleep: never actually waits."""
    return None


class _RaiseSeq:
    """An async callable that raises the next exception in ``seq`` on each call
    (``None`` means 'return cleanly'); the last entry repeats once exhausted."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        exc = self.seq[self.calls] if self.calls < len(self.seq) else self.seq[-1]
        self.calls += 1
        if exc is not None:
            raise exc
        return None


class _SleepRaiseOnNth:
    """A no-op sleep that raises ``CancelledError`` on its ``n``-th call, used to
    break a loop after a chosen number of sleeps so a full iteration is exercised."""

    def __init__(self, n: int):
        self.n = n
        self.calls = 0

    async def __call__(self, _seconds):
        self.calls += 1
        if self.calls >= self.n:
            raise asyncio.CancelledError
        return None


class _FakeDBRaises:
    """Stand-in for ``serve.Database``: entering the context raises the next error,
    so a loop's *outer* try/except (its consecutive-failure backoff) is exercised,
    then the loop unwinds on the iteration that raises ``CancelledError``."""

    def __init__(self, *errors):
        self._errors = list(errors)

    def __call__(self, _path):
        return self

    async def __aenter__(self):
        raise self._errors.pop(0)

    async def __aexit__(self, *exc):
        return False


def _cfg(tmp_path, **over):
    base = dict(
        guild_name="From the Ashes",
        guild_realm="hellfire",
        guild_region="eu",
        database_path=str(tmp_path / "serve.db"),
        feed_poll_minutes=1,
        board_refresh_minutes=1,
        score_refresh_minutes=1,
        digest_flush_hours=1,
        sync_interval_minutes=1,
    )
    base.update(over)
    return Config(**base)


# --- _make_sender -----------------------------------------------------------


def _sender_cfg(tmp_path, **kw):
    # The sender now resolves its gate per-send from the settings overlay
    # (DB + Config); with nothing overridden the env-built Config wins.
    return Config(database_path=str(tmp_path / "sender.db"), **kw)


async def test_make_sender_posts_through_a_cached_channel(tmp_path):
    channel = _Channel()
    bot = _Bot(channel)
    send = serve._make_sender(
        bot, _sender_cfg(tmp_path, posting_mode="live", test_channel_id="9")
    )
    await send("123", embed="EMBED", content="hi")
    assert channel.sent == [("hi", "EMBED")]
    assert bot.fetched == []  # cache hit -> no fetch


async def test_make_sender_falls_back_to_fetch_channel(tmp_path):
    channel = _Channel()
    bot = _Bot(channel, fetch_only=True)  # get_channel returns None
    send = serve._make_sender(
        bot, _sender_cfg(tmp_path, posting_mode="live", test_channel_id="9")
    )
    await send("123", embed="EMBED")
    assert bot.fetched == [123]
    assert channel.sent == [(None, "EMBED")]


async def test_make_sender_blocks_when_guardrail_off(tmp_path):
    bot = _Bot(_Channel())
    send = serve._make_sender(bot, _sender_cfg(tmp_path, posting_mode="off"))
    with pytest.raises(PostBlocked):
        await send("123", embed="EMBED")


# --- _wire_webhook_sender ---------------------------------------------------


class _AppState:
    pass


class _App:
    def __init__(self):
        self.state = _AppState()


async def test_wire_webhook_sender_sets_sender_and_drains(tmp_path, monkeypatch):
    drained = []

    async def _fake_drain(db, config, send):
        drained.append(send)

    monkeypatch.setattr("ftabot.feeds.runner.drain_outbox", _fake_drain)
    app = _App()
    cfg = _cfg(tmp_path)
    await serve._wire_webhook_sender(app, cfg, _Bot(_Channel()))

    assert callable(app.state.feed_sender)
    assert drained == [app.state.feed_sender]  # the outbox was flushed with it


async def test_wire_webhook_sender_swallows_drain_errors(tmp_path, monkeypatch, caplog):
    import logging

    async def _boom(db, config, send):
        raise RuntimeError("db gone")

    monkeypatch.setattr("ftabot.feeds.runner.drain_outbox", _boom)
    caplog.set_level(logging.WARNING)
    app = _App()
    # Must not raise: a failed startup flush is best-effort.
    await serve._wire_webhook_sender(app, _cfg(tmp_path), _Bot(_Channel()))
    assert "outbox flush on startup failed" in caplog.text


# --- _feed_loop -------------------------------------------------------------


async def test_feed_loop_polls_then_unwinds(tmp_path, monkeypatch):
    poll = _RaiseOnNth(2, result=3)  # iter1 posts 3, iter2 stops the loop
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.feeds.runner.poll_once", poll)

    with pytest.raises(asyncio.CancelledError):
        await serve._feed_loop(_cfg(tmp_path), _Bot(_Channel()), sources=["src"])
    assert poll.calls == 2


async def test_feed_loop_survives_a_poll_error(tmp_path, monkeypatch, caplog):
    import logging

    calls = {"n": 0}

    async def _poll(db, config, sources, session, send):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream 500")  # swallowed; loop must stay alive
        raise asyncio.CancelledError  # second pass: stop the loop

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.feeds.runner.poll_once", _poll)
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await serve._feed_loop(_cfg(tmp_path), _Bot(_Channel()), sources=["src"])
    # The transient error was caught and logged, and the loop tried again.
    assert calls["n"] == 2
    assert "feed loop error" in caplog.text


async def test_feed_loop_nudges_news_board_after_a_wowhead_poll(tmp_path, monkeypatch):
    # A poll is the moment new Wowhead items land in the archive, so the loop
    # nudges the News board right after it (and only the News board).
    nudges: list[str | None] = []

    async def _refresh(db, config, bot, *, only_type=None):
        nudges.append(only_type)
        return 0

    poll = _RaiseOnNth(2, result=0)
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.feeds.runner.poll_once", poll)
    monkeypatch.setattr("ftabot.scores.refresh_boards", _refresh)

    class _WowheadSrc:
        name = "wowhead"

    with pytest.raises(asyncio.CancelledError):
        await serve._feed_loop(_cfg(tmp_path), _Bot(_Channel()), sources=[_WowheadSrc()])
    assert nudges == ["news_highlights"]  # one nudge per completed poll


async def test_feed_loop_skips_news_nudge_without_wowhead(tmp_path, monkeypatch):
    nudges: list[str | None] = []

    async def _refresh(db, config, bot, *, only_type=None):
        nudges.append(only_type)
        return 0

    poll = _RaiseOnNth(2, result=0)
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.feeds.runner.poll_once", poll)
    monkeypatch.setattr("ftabot.scores.refresh_boards", _refresh)

    class _OtherSrc:
        name = "raiderio_mplus"

    with pytest.raises(asyncio.CancelledError):
        await serve._feed_loop(_cfg(tmp_path), _Bot(_Channel()), sources=[_OtherSrc()])
    assert nudges == []  # nothing archived news, so no board nudge


# --- _board_loop ------------------------------------------------------------


async def test_board_loop_refreshes_enabled_boards(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    async with Database(cfg.database_path) as d:
        await d.add_board("hot20", "123")

    refresh = _RaiseOnNth(2)  # one enabled board -> called once per iteration
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.boards.refresh_board", refresh)

    with pytest.raises(asyncio.CancelledError):
        await serve._board_loop(cfg, _Bot(_Channel()))
    assert refresh.calls == 2


async def test_board_loop_skips_unknown_board_type(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    async with Database(cfg.database_path) as d:
        await d.add_board("bogus_type", "123")  # not in REGISTRY -> skipped

    refresh = _RaiseOnNth(1)  # would raise on its first call if ever reached
    # With only an unknown-type board, refresh_board is never called, so the loop
    # would spin forever on no-op sleeps; make the sleep itself stop the loop.
    stop = _RaiseOnNth(2)  # let the upfront settle sleep pass, then stop

    async def _sleep(_s):
        await stop()

    monkeypatch.setattr(serve.asyncio, "sleep", _sleep)
    monkeypatch.setattr("ftabot.boards.refresh_board", refresh)

    with pytest.raises(asyncio.CancelledError):
        await serve._board_loop(cfg, _Bot(_Channel()))
    assert refresh.calls == 0  # unknown type was skipped, never refreshed


# --- _score_loop ------------------------------------------------------------


async def test_score_loop_refreshes_scores_and_boards(tmp_path, monkeypatch):
    refresh_scores = _RaiseOnNth(99, result=["changed-member"])  # always returns
    refresh_boards = _RaiseOnNth(1)  # raises first call -> breaks the loop

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.scores.select_active", lambda members, config: members)
    monkeypatch.setattr("ftabot.scores.refresh_scores", refresh_scores)
    monkeypatch.setattr("ftabot.scores.refresh_boards", refresh_boards)

    with pytest.raises(asyncio.CancelledError):
        await serve._score_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert refresh_scores.calls == 1
    assert refresh_boards.calls == 1  # the 'if changed' branch fired


# --- _digest_loop -----------------------------------------------------------


async def test_digest_loop_flushes(tmp_path, monkeypatch):
    flush = _RaiseOnNth(2, result=2)  # iter1 flushes 2 groups, iter2 stops
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.feeds.runner.flush_digests", flush)

    with pytest.raises(asyncio.CancelledError):
        await serve._digest_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert flush.calls == 2


# --- _periodic_sync ---------------------------------------------------------


async def test_periodic_sync_runs_sync_guild(tmp_path, monkeypatch):
    result = SyncResult(fetched=2, upserted=2, sources=("Raider.IO",))
    sync = _RaiseOnNth(2, result=result)  # iter1 syncs + logs, iter2 stops
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(serve, "sync_guild", sync)

    with pytest.raises(asyncio.CancelledError):
        await serve._periodic_sync(_cfg(tmp_path))
    assert sync.calls == 2


# --- loop resilience: a bad board/page, and each loop's outer backoff ---------


async def test_board_loop_logs_a_bad_board_and_a_bad_page(tmp_path, monkeypatch, caplog):
    """One board or page raising is caught per-item so the loop keeps going and
    still reconciles the rest (the sticky-pages branch rides the same tick)."""
    import logging

    cfg = _cfg(tmp_path)
    async with Database(cfg.database_path) as d:
        await d.add_board("hot20", "111")
        await d.upsert_sticky_page("rules_info", "222")

    async def _board_boom(*a, **k):
        raise RuntimeError("board boom")

    # First page refresh raises (per-page except), the second stops the loop.
    page = _RaiseSeq([RuntimeError("page boom"), asyncio.CancelledError()])
    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr("ftabot.boards.refresh_board", _board_boom)
    monkeypatch.setattr("ftabot.pages.refresh_page", page)
    caplog.set_level(logging.WARNING)

    with pytest.raises(asyncio.CancelledError):
        await serve._board_loop(cfg, _Bot(_Channel()))

    assert "board hot20 failed" in caplog.text  # a bad board never kills the loop
    assert "page rules_info failed" in caplog.text  # nor does a bad page


async def test_board_loop_skips_unknown_sticky_page(tmp_path, monkeypatch):
    """A placed page whose name isn't in the registry is skipped, not crashed on
    (mirrors the unknown-board guard)."""
    cfg = _cfg(tmp_path)
    async with Database(cfg.database_path) as d:
        await d.upsert_sticky_page("bogus_page", "222")  # not in PAGES -> skipped

    refresh_page = _RaiseSeq([RuntimeError("should never be reached")])
    # No boards/pages do real work, so the outer retry sleep is what stops us:
    # settle sleep (1) then the outer sleep (2).
    monkeypatch.setattr(serve.asyncio, "sleep", _SleepRaiseOnNth(2))
    monkeypatch.setattr("ftabot.pages.refresh_page", refresh_page)

    with pytest.raises(asyncio.CancelledError):
        await serve._board_loop(cfg, _Bot(_Channel()))
    assert refresh_page.calls == 0  # unknown page never refreshed


async def test_board_loop_backs_off_on_db_error(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(serve, "Database", _FakeDBRaises(RuntimeError("db gone"),
                                                         asyncio.CancelledError()))
    caplog.set_level(logging.WARNING)
    with pytest.raises(asyncio.CancelledError):
        await serve._board_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert "board loop error (failure 1)" in caplog.text


async def test_score_loop_logs_and_resets_after_a_refresh(tmp_path, monkeypatch, caplog):
    """The success tail: changed scores -> boards nudged, the info log fires, and
    the failure counter resets."""
    import logging

    monkeypatch.setattr(serve.asyncio, "sleep", _SleepRaiseOnNth(2))  # settle, then stop
    monkeypatch.setattr("ftabot.scores.select_active", lambda members, config: members)

    async def _refresh_scores(db, config, active, *, session):
        return ["changed-member"]  # always "changed" -> drives the if-branch

    async def _refresh_boards(db, config, bot, **kw):
        return None  # succeeds, so the info log + failures reset run

    monkeypatch.setattr("ftabot.scores.refresh_scores", _refresh_scores)
    monkeypatch.setattr("ftabot.scores.refresh_boards", _refresh_boards)
    caplog.set_level(logging.INFO)

    with pytest.raises(asyncio.CancelledError):
        await serve._score_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert "scores: 1 updated, boards refreshed" in caplog.text


async def test_score_loop_backs_off_on_db_error(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(serve, "Database", _FakeDBRaises(RuntimeError("db gone"),
                                                         asyncio.CancelledError()))
    caplog.set_level(logging.WARNING)
    with pytest.raises(asyncio.CancelledError):
        await serve._score_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert "score loop error (failure 1)" in caplog.text


async def test_digest_loop_backs_off_on_db_error(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(serve, "Database", _FakeDBRaises(RuntimeError("db gone"),
                                                         asyncio.CancelledError()))
    caplog.set_level(logging.WARNING)
    with pytest.raises(asyncio.CancelledError):
        await serve._digest_loop(_cfg(tmp_path), _Bot(_Channel()))
    assert "digest loop error (failure 1)" in caplog.text


async def test_periodic_sync_backs_off_on_db_error(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(serve.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(serve, "Database", _FakeDBRaises(RuntimeError("db gone"),
                                                         asyncio.CancelledError()))
    caplog.set_level(logging.WARNING)
    with pytest.raises(asyncio.CancelledError):
        await serve._periodic_sync(_cfg(tmp_path))
    assert "scheduled sync failed (failure 1)" in caplog.text
