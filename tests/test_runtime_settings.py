"""The runtime settings overlay: a DB override beats the env-built Config, and
its absence falls back to the Config default. Pure-ish + offline (real SQLite)."""

import pytest

from ftabot import runtime_settings
from ftabot.config import Config
from ftabot.posting import PostingMode


async def test_effective_falls_back_to_config(db):
    cfg = Config(posting_mode="test", test_channel_id="999", feed_default_channel_id="42")
    # No overrides saved → the env-built Config wins.
    assert await runtime_settings.effective(db, cfg, "posting_mode") == "test"
    gate = await runtime_settings.effective_post_gate(db, cfg)
    assert gate.mode is PostingMode.TEST
    assert gate.test_channel_id == "999"
    assert await runtime_settings.effective_feed_channel(db, cfg) == "42"


async def test_override_beats_config(db):
    cfg = Config(posting_mode="off", test_channel_id=None)
    await db.set_setting("posting_mode", "live")
    await db.set_setting("test_channel_id", "12345")
    gate = await runtime_settings.effective_post_gate(db, cfg)
    assert gate.mode is PostingMode.LIVE
    assert gate.test_channel_id == "12345"


async def test_delete_setting_reverts_to_env(db):
    cfg = Config(posting_mode="off")
    await db.set_setting("posting_mode", "live")
    assert await runtime_settings.effective(db, cfg, "posting_mode") == "live"
    assert await db.delete_setting("posting_mode") is True
    assert await runtime_settings.effective(db, cfg, "posting_mode") == "off"


def test_normalise_validates_against_manifest():
    assert runtime_settings.normalise("posting_mode", "LIVE") == "live"
    assert runtime_settings.normalise("test_channel_id", " 123 ") == "123"
    # An empty channel clears the override (reverting to env on save).
    assert runtime_settings.normalise("test_channel_id", "") is None
    with pytest.raises(runtime_settings.SettingError):
        runtime_settings.normalise("posting_mode", "sideways")
    with pytest.raises(runtime_settings.SettingError):
        runtime_settings.normalise("test_channel_id", "not-a-number")
    with pytest.raises(runtime_settings.SettingError):
        runtime_settings.normalise("unknown_key", "x")


async def test_settings_overview_shape(db):
    cfg = Config(posting_mode="test", test_channel_id="999")
    await db.set_setting("posting_mode", "live")
    rows = {r["key"]: r for r in await runtime_settings.settings_overview(db, cfg)}
    assert rows["posting_mode"]["env"] == "test"
    assert rows["posting_mode"]["override"] == "live"
    assert rows["posting_mode"]["effective"] == "live"
    # An un-overridden key reports the env value as effective.
    assert rows["test_channel_id"]["override"] is None
    assert rows["test_channel_id"]["effective"] == "999"
