"""Tests for ``serve._run`` (the all-in-one orchestrator) and ``serve.main``.

``_run`` decides *which* long-running tasks to launch from the configuration:
the ingest listener always, the bot + its feed/board/score/digest loops only when a
Discord token (and the relevant interval/channel) is set, and the periodic sync
only when its interval is set. That wiring was untested — exercising it here
guards against a loop silently never starting (or starting when disabled).

Technique: every heavy dependency is patched (the ingest listener, the bot, the
loop coroutines, ``build_sources``) so nothing touches the network. The fake
listener's ``serve()`` returns immediately, which makes ``_supervise`` see a
"long-running" task finish and tear everything down — so ``_run`` returns
promptly. A spy around ``asyncio.create_task`` records which named tasks were
launched.
"""

from __future__ import annotations

import asyncio

from ftabot import serve
from ftabot.config import Config


class _FakeIngest:
    """Ingest stand-in whose serve() returns at once, triggering the supervised
    shutdown so ``_run`` unwinds without real I/O."""

    def __init__(self, _config):
        self.bot = None
        self.feed_sender = None

    async def serve(self):
        return None


class _FakeBot:
    def __init__(self, config):
        self.config = config

    async def start(self, _token):
        await asyncio.Event().wait()  # runs until cancelled by the supervisor


async def _forever(*_a, **_k):
    """Stand-in for a long-running loop coroutine: never returns on its own."""
    await asyncio.Event().wait()


def _patch_common(monkeypatch, *, sources):
    """Patch every heavy piece ``_run`` touches and return the create_task spy's
    name list plus the ingest listener the run built."""
    created: list[str | None] = []
    captured: dict[str, object] = {}
    real_create_task = asyncio.create_task

    def _spy(coro, *, name=None):
        created.append(name)
        return real_create_task(coro, name=name)

    def _fake_ingest(config):
        captured["ingest"] = _FakeIngest(config)
        return captured["ingest"]

    monkeypatch.setattr(serve, "Ingest", _fake_ingest)
    monkeypatch.setattr("ftabot.bot.FtaBot", _FakeBot)
    monkeypatch.setattr("ftabot.feeds.runner.build_sources", lambda config: list(sources))
    monkeypatch.setattr(serve, "_wire_webhook_sender", _forever)
    monkeypatch.setattr(serve, "_feed_loop", _forever)
    monkeypatch.setattr(serve, "_digest_loop", _forever)
    monkeypatch.setattr(serve, "_board_loop", _forever)
    monkeypatch.setattr(serve, "_score_loop", _forever)
    monkeypatch.setattr(serve, "_periodic_sync", _forever)
    monkeypatch.setattr(serve.asyncio, "create_task", _spy)
    return created, captured


def _cfg(tmp_path, **over):
    base = dict(database_path=str(tmp_path / "run.db"))
    base.update(over)
    return Config(**base)


async def test_run_launches_every_task_when_fully_configured(tmp_path, monkeypatch):
    created, captured = _patch_common(monkeypatch, sources=["src"])
    cfg = _cfg(
        tmp_path,
        discord_token="tok",
        feed_poll_minutes=5,
        feed_default_channel_id="123",  # + poll>0 -> feeds_enabled
        digest_flush_hours=1,
        board_refresh_minutes=5,
        score_refresh_minutes=5,
        sync_interval_minutes=10,
    )

    await serve._run(cfg)

    assert set(created) == {
        "ingest", "bot", "webhook-sender", "feeds", "digest", "boards", "scores", "sync",
    }
    assert captured["ingest"].bot is not None  # bot exposed to /healthz


async def test_run_ingest_only_when_no_token_or_sync(tmp_path, monkeypatch):
    created, captured = _patch_common(monkeypatch, sources=["src"])
    cfg = _cfg(tmp_path, discord_token="", sync_interval_minutes=0)

    await serve._run(cfg)

    assert created == ["ingest"]  # no bot, no loops, no sync
    assert captured["ingest"].bot is None


async def test_run_bot_without_optional_loops(tmp_path, monkeypatch):
    # Token set, but feeds yield no sources and every optional interval is off:
    # only the listener, the bot, and the one-shot webhook wiring should start.
    created, _ = _patch_common(monkeypatch, sources=[])
    cfg = _cfg(
        tmp_path,
        discord_token="tok",
        feed_poll_minutes=5,
        feed_default_channel_id="123",  # feeds_enabled, but build_sources -> []
        digest_flush_hours=0,
        board_refresh_minutes=0,
        score_refresh_minutes=0,
        sync_interval_minutes=0,
    )

    await serve._run(cfg)

    assert set(created) == {"ingest", "bot", "webhook-sender"}


def test_main_builds_config_and_runs(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(serve.Config, "from_env", classmethod(lambda cls: cfg))

    seen: dict[str, object] = {}

    def _fake_run(coro):
        seen["ran"] = True
        coro.close()  # we don't actually execute _run here; avoid "never awaited"

    monkeypatch.setattr(serve.asyncio, "run", _fake_run)
    serve.main()
    assert seen["ran"] is True
