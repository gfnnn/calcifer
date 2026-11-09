"""The posting guardrail: deny-by-default routing for every outbound message."""

from __future__ import annotations

import pytest

from ftabot.posting import PostGate, PostingMode


def test_mode_parsing_defaults_to_off():
    assert PostingMode.parse("test") is PostingMode.TEST
    assert PostingMode.parse("LIVE") is PostingMode.LIVE
    assert PostingMode.parse(None) is PostingMode.OFF
    assert PostingMode.parse("nonsense") is PostingMode.OFF


def test_off_blocks_everything():
    gate = PostGate(PostingMode.OFF, "test-chan")
    assert gate.silent is True
    assert gate.resolve("prod", promoted=True) is None
    assert gate.resolve("prod", promoted=False) is None


def test_test_mode_redirects_all_posts_to_the_test_channel():
    gate = PostGate(PostingMode.TEST, "test-chan")
    assert gate.resolve("prod", promoted=True) == "test-chan"
    assert gate.resolve("prod", promoted=False) == "test-chan"
    # but only once a test channel has been confirmed
    assert PostGate(PostingMode.TEST, None).resolve("prod", promoted=True) is None


def test_live_mode_gates_on_promotion():
    gate = PostGate(PostingMode.LIVE, "test-chan")
    # confirmed-for-production surfaces reach their real channel
    assert gate.resolve("prod", promoted=True) == "prod"
    # anything not promoted is still sandboxed
    assert gate.resolve("prod", promoted=False) == "test-chan"
    # a promoted board with no production channel resolves to nothing
    assert gate.resolve(None, promoted=True) is None


def test_from_config_reads_the_mode_and_channel():
    from ftabot.config import Config

    gate = PostGate.from_config(Config(posting_mode="test", test_channel_id="123"))
    assert gate.mode is PostingMode.TEST
    assert gate.test_channel_id == "123"
    # the default config is silent
    assert PostGate.from_config(Config()).silent is True


# --- the feed sender honours the gate too -----------------------------------


class _FakeChannel:
    def __init__(self):
        self.sends: list[dict] = []

    async def send(self, **kwargs):
        self.sends.append(kwargs)


class _FakeBot:
    def __init__(self, channel):
        self.channel = channel
        self.asked: list[int] = []

    def get_channel(self, channel_id):
        self.asked.append(channel_id)
        return self.channel

    async def fetch_channel(self, channel_id):  # pragma: no cover - not hit in tests
        return self.channel


def _sender_cfg(tmp_path, **kw):
    # The sender resolves its gate per-send from the live settings overlay
    # (DB + Config); with no overrides saved the env-built Config wins.
    from ftabot.config import Config

    return Config(database_path=str(tmp_path / "sender.db"), **kw)


async def test_feed_sender_drops_when_guardrail_is_off(tmp_path):
    from ftabot.posting import PostBlocked
    from ftabot.serve import _make_sender

    channel = _FakeChannel()
    bot = _FakeBot(channel)
    send = _make_sender(bot, _sender_cfg(tmp_path, posting_mode="off"))

    # A blocked send raises PostBlocked so the caller leaves the event queued /
    # unseen (it must not be consumed) rather than silently dropping it.
    with pytest.raises(PostBlocked):
        await send("12345", embed=None, content="an alert")

    assert channel.sends == []  # nothing posted
    assert bot.asked == []  # never even resolved a channel


async def test_feed_sender_redirects_in_test_mode(tmp_path):
    from ftabot.serve import _make_sender

    channel = _FakeChannel()
    bot = _FakeBot(channel)
    send = _make_sender(
        bot, _sender_cfg(tmp_path, posting_mode="test", test_channel_id="999")
    )

    await send("12345", embed=None, content="an alert")

    assert len(channel.sends) == 1
    assert bot.asked == [999]  # redirected to the test channel, not 12345


async def test_feed_sender_uses_the_real_channel_when_live(tmp_path):
    from ftabot.serve import _make_sender

    channel = _FakeChannel()
    bot = _FakeBot(channel)
    send = _make_sender(
        bot, _sender_cfg(tmp_path, posting_mode="live", test_channel_id="999")
    )

    await send("12345", embed=None, content="an alert")

    assert len(channel.sends) == 1
    assert bot.asked == [12345]  # feeds route to their own channel when live


async def test_feed_sender_honours_a_live_settings_override(tmp_path):
    # A `/posting mode` change takes effect on the next send, with
    # no loop restart: saving an override flips the sender from live → off.
    from ftabot.db import Database
    from ftabot.posting import PostBlocked
    from ftabot.serve import _make_sender

    channel = _FakeChannel()
    bot = _FakeBot(channel)
    cfg = _sender_cfg(tmp_path, posting_mode="live", test_channel_id="999")
    send = _make_sender(bot, cfg)

    await send("12345", embed=None, content="first")
    assert bot.asked == [12345]

    async with Database(cfg.database_path) as db:
        await db.set_setting("posting_mode", "off")
    with pytest.raises(PostBlocked):
        await send("12345", embed=None, content="second")
