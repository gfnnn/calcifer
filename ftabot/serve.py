"""All-in-one entry point for cloud hosting.

Runs the browser/API web app, the Discord bot, and a periodic roster sync in a
single process, so a host only needs one service. Each piece turns itself on
based on configuration:

  * web app        — always (binds $PORT / WEB_PORT)
  * Discord bot    — only if DISCORD_TOKEN is set
  * periodic sync  — only if SYNC_INTERVAL_MINUTES > 0

Run with:  python -m ftabot.serve
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import uvicorn

from .config import Config
from .db import Database
from .posting import PostBlocked, PostGate
from .sync import sync_guild
from .web import create_app

log = logging.getLogger("ftabot.serve")


def _make_sender(bot, gate: PostGate):
    """A coroutine (channel_id, embed) -> None that posts via the bot.

    Every feed alert passes through the posting ``gate`` first: it is dropped
    while the guardrail is off, redirected to the confirmed test channel in test
    mode, and only reaches its real channel when posting is live.
    """

    async def _send(channel_id: str, embed, content: str | None = None) -> None:
        destination = gate.resolve(channel_id, promoted=True)
        if destination is None:
            log.debug("posting guardrail (%s): dropped alert for %s", gate.mode.value, channel_id)
            # Signal "blocked, not sent" so the caller leaves the event queued /
            # unseen rather than consuming it — it posts once the gate opens.
            raise PostBlocked(channel_id)
        channel = bot.get_channel(int(destination))
        if channel is None:
            channel = await bot.fetch_channel(int(destination))
        await channel.send(content=content, embed=embed)

    return _send


async def _wire_webhook_sender(app, config: Config, bot) -> None:
    """Once the bot is ready, let the web app's webhook receiver post instantly.

    Also drains anything that queued in feed_outbox before the bot came up.
    """
    from .feeds.runner import drain_outbox

    await bot.wait_until_ready()
    send = _make_sender(bot, PostGate.from_config(config))
    app.state.feed_sender = send
    try:
        async with Database(config.database_path) as db:
            await drain_outbox(db, config, send)
    except Exception as exc:  # noqa: BLE001 - best-effort backlog flush
        log.warning("outbox flush on startup failed: %s", exc)


async def _feed_loop(config: Config, bot, sources) -> None:
    """Poll the integration-hub feeds and post alerts through the bot."""
    from .feeds.runner import poll_once

    await bot.wait_until_ready()
    await asyncio.sleep(5)  # let slash-command sync settle first
    send = _make_sender(bot, PostGate.from_config(config))

    interval = config.feed_poll_minutes * 60
    async with aiohttp.ClientSession(headers={"User-Agent": "ftabot-calcifer/1.0"}) as session:
        while True:
            try:
                async with Database(config.database_path) as db:
                    posted = await poll_once(db, config, sources, session, send)
                if posted:
                    log.info("feeds: posted %d alert(s)", posted)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("feed loop error: %s", exc)
            await asyncio.sleep(interval)


async def _board_loop(config: Config, bot) -> None:
    """Re-render the living boards on an interval, editing each in place."""
    from .boards import REGISTRY, BotBoardMessenger, refresh_board

    await bot.wait_until_ready()
    await asyncio.sleep(8)  # let slash-command sync + feeds settle first
    messenger = BotBoardMessenger(bot)
    gate = PostGate.from_config(config)
    interval = config.board_refresh_minutes * 60
    while True:
        try:
            async with Database(config.database_path) as db:
                for row in await db.boards(enabled_only=True):
                    board = REGISTRY.get(row["board_type"])
                    if board is None:
                        continue
                    try:
                        await refresh_board(db, config, board, row, messenger, gate)
                    except Exception as exc:  # noqa: BLE001 - one board never kills the loop
                        log.warning("board %s failed: %s", row["board_type"], exc)
                    await asyncio.sleep(1)  # stagger edits across channels
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("board loop error: %s", exc)
        await asyncio.sleep(interval)


async def _score_loop(config: Config, bot) -> None:
    """Refresh the active key-runners' M+ scores on an interval, then nudge the
    boards. Covers solo grinders and pushers without polling the whole roster."""
    from .scores import refresh_boards, refresh_scores, select_active

    await bot.wait_until_ready()
    await asyncio.sleep(12)  # let startup + the first board render settle
    interval = config.score_refresh_minutes * 60
    async with aiohttp.ClientSession(headers={"Accept": "application/json"}) as session:
        while True:
            try:
                async with Database(config.database_path) as db:
                    members = await db.all_members(order_by="mythic_plus_score")
                    active = select_active(members, config)
                    changed = await refresh_scores(db, config, active, session=session)
                    if changed:
                        await refresh_boards(db, config, bot)
                        log.info("scores: %d updated, boards refreshed", len(changed))
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("score loop error: %s", exc)
            await asyncio.sleep(interval)


async def _digest_loop(config: Config, bot) -> None:
    """Flush digest-mode feed queues on a configured interval."""
    from .feeds.runner import flush_digests

    await bot.wait_until_ready()
    await asyncio.sleep(10)
    send = _make_sender(bot, PostGate.from_config(config))
    interval = config.digest_flush_hours * 3600
    while True:
        # Wait first: digests should accumulate before the first flush.
        await asyncio.sleep(interval)
        try:
            async with Database(config.database_path) as db:
                n = await flush_digests(db, config, send)
            if n:
                log.info("digest: flushed %d group(s)", n)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("digest loop error: %s", exc)


async def _periodic_sync(config: Config) -> None:
    """Refresh the roster on an interval, each run on its own connection."""
    interval = config.sync_interval_minutes * 60
    # Let the bot fully settle (gateway connect, command sync, first board
    # render) before the first heavy sync, so a restart can't immediately stack
    # a sync on top of a still-starting process.
    await asyncio.sleep(45)
    while True:
        try:
            async with Database(config.database_path) as db:
                result = await sync_guild(db, config)
            log.info("scheduled sync: %s", result.summary())
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.warning("scheduled sync failed: %s", exc)
        await asyncio.sleep(interval)


async def _supervise(
    watch: list[asyncio.Task], background: list[asyncio.Task]
) -> None:
    """Run until a long-running ``watch`` task ends, then cancel everything.

    One-shot ``background`` tasks (e.g. the webhook-sender setup) finishing
    normally must NOT bring the process down — only a long-running task dying
    means something is wrong and the host should restart us.
    """
    done, pending = await asyncio.wait(watch, return_when=asyncio.FIRST_COMPLETED)
    for task in (*pending, *background):
        task.cancel()
    # Drain the cancelled/finished tasks so their exceptions are retrieved (no
    # "task exception was never retrieved" noise) and a real failure in a
    # one-shot setup task is surfaced in the log rather than silently swallowed.
    for task in (*pending, *background):
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - report, don't mask the trigger below
            log.warning("background task %r ended with error: %s", task.get_name(), exc)
    for task in done:
        exc = task.exception()
        if exc:
            raise exc


async def _run(config: Config) -> None:
    # Long-running tasks whose completion means "bring the process down so the
    # host restarts us cleanly". One-shot setup tasks go in ``background``.
    tasks: list[asyncio.Task] = []
    background: list[asyncio.Task] = []

    # Web app (uvicorn run programmatically so it shares this event loop).
    app = create_app(config)
    uconfig = uvicorn.Config(
        app,
        host=config.web_host,
        port=config.web_port,
        log_level="info",
    )
    server = uvicorn.Server(uconfig)
    tasks.append(asyncio.create_task(server.serve(), name="web"))
    log.info("web app on %s:%s", config.web_host, config.web_port)

    # Discord bot (optional).
    if config.discord_token:
        from .bot import FtaBot

        bot = FtaBot(config)
        tasks.append(asyncio.create_task(bot.start(config.discord_token), name="bot"))
        log.info("Discord bot starting")

        # Let the web app's webhook receiver (Raid-Helper) post through the bot.
        # This is a ONE-SHOT setup task (wires the sender, drains the outbox, then
        # returns), so it must not count toward the shutdown trigger below — else
        # the whole process tears down the moment the bot becomes ready.
        background.append(
            asyncio.create_task(
                _wire_webhook_sender(app, config, bot), name="webhook-sender"
            )
        )

        # Integration hub: poll feeds and post through the bot (needs a channel).
        if config.feeds_enabled:
            from .feeds.runner import build_sources

            sources = build_sources(config)
            if sources:
                tasks.append(
                    asyncio.create_task(_feed_loop(config, bot, sources), name="feeds")
                )
                log.info(
                    "feed loop every %d min (%d source(s))",
                    config.feed_poll_minutes,
                    len(sources),
                )

        # Digest flush: post weekly roundups for buffered digest-mode events.
        if config.feeds_enabled and config.digest_flush_hours > 0:
            tasks.append(asyncio.create_task(_digest_loop(config, bot), name="digest"))
            log.info("digest flush every %d h", config.digest_flush_hours)

        # Living boards: re-render bot-owned messages in place (needs a board).
        if config.boards_enabled:
            tasks.append(asyncio.create_task(_board_loop(config, bot), name="boards"))
            log.info("board loop every %d min", config.board_refresh_minutes)

        # Recurring M+ score refresh for the active set, then nudge the boards.
        if config.scores_enabled:
            tasks.append(asyncio.create_task(_score_loop(config, bot), name="scores"))
            log.info("score refresh every %d min", config.score_refresh_minutes)
    else:
        log.info("DISCORD_TOKEN not set — running web only")

    # Periodic sync (optional).
    if config.sync_interval_minutes > 0:
        tasks.append(asyncio.create_task(_periodic_sync(config), name="sync"))
        log.info("scheduled sync every %d min", config.sync_interval_minutes)

    # Run until a long-running task dies; a one-shot background task finishing
    # (e.g. the webhook-sender setup) must never trigger a shutdown.
    await _supervise(tasks, background)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()
    asyncio.run(_run(config))


if __name__ == "__main__":  # pragma: no cover
    main()
