"""The serve supervisor: a one-shot background task finishing must NOT shut the
process down (regression for the webhook-sender crash loop)."""

from __future__ import annotations

import asyncio

import pytest

from ftabot.serve import _supervise


async def test_finished_oneshot_does_not_trigger_shutdown():
    stop = asyncio.Event()

    async def long_running():
        await stop.wait()  # stays alive until we release it

    async def one_shot():
        return  # finishes immediately, like the webhook-sender setup

    watch = [asyncio.create_task(long_running())]
    background = [asyncio.create_task(one_shot())]
    sup = asyncio.create_task(_supervise(watch, background))

    await asyncio.sleep(0.05)
    assert background[0].done()  # the one-shot finished...
    assert not sup.done()        # ...but the supervisor kept everything running

    stop.set()                   # now a long-running task ends -> shutdown
    await asyncio.wait_for(sup, timeout=1)
    assert sup.done()
    assert watch[0].cancelled() or watch[0].done()


async def test_background_task_error_is_drained_not_masked(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    stop = asyncio.Event()

    async def long_running():
        await stop.wait()

    async def failing_setup():
        raise RuntimeError("webhook wiring failed")

    watch = [asyncio.create_task(long_running())]
    background = [asyncio.create_task(failing_setup())]
    sup = asyncio.create_task(_supervise(watch, background))

    await asyncio.sleep(0.05)
    assert background[0].done()  # the one-shot failed...
    assert not sup.done()        # ...but a background failure alone is not fatal

    stop.set()                   # a long-running task ending is what shuts us down
    await asyncio.wait_for(sup, timeout=1)
    assert sup.done() and sup.exception() is None  # drained cleanly, not re-raised
    assert "webhook wiring failed" in caplog.text  # but surfaced in the log


async def test_a_dying_watch_task_propagates_its_error():
    async def boom():
        raise RuntimeError("web server fell over")

    async def long_running():
        await asyncio.sleep(3600)

    watch = [asyncio.create_task(boom()), asyncio.create_task(long_running())]
    with pytest.raises(RuntimeError, match="fell over"):
        await _supervise(watch, [])
    # the other watch task was cancelled on the way out (let the loop settle)
    await asyncio.sleep(0)
    assert watch[1].cancelled()
