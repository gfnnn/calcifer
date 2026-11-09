from ftabot.config import Config


def test_defaults():
    c = Config()
    assert c.guild_name == "From the Ashes"
    assert c.web_port == 8080
    assert c.sync_interval_minutes == 0
    assert c.wowaudit_enabled is False


def test_port_prefers_PORT_then_WEB_PORT(monkeypatch):
    monkeypatch.setenv("PORT", "5555")
    monkeypatch.setenv("WEB_PORT", "8080")
    assert Config.from_env().web_port == 5555
    monkeypatch.delenv("PORT")
    assert Config.from_env().web_port == 8080


def test_sync_interval_from_env(monkeypatch):
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "360")
    assert Config.from_env().sync_interval_minutes == 360


def test_enabled_flags(monkeypatch):
    monkeypatch.setenv("WOWAUDIT_API_KEY", "secret")
    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "id")
    monkeypatch.setenv("BLIZZARD_CLIENT_SECRET", "shh")
    c = Config.from_env()
    assert c.wowaudit_enabled is True
    assert c.blizzard_enabled is True


def test_boolean_flags_are_case_insensitive(monkeypatch):
    # Regression: the falsy set must be matched case-insensitively, or FALSE /
    # No / Off would slip past it and read as *enabled* (the opposite of intent).
    for raw in ("0", "false", "False", "FALSE", "no", "No", "off", "Off"):
        monkeypatch.setenv("MPLUS_FEED_ENABLED", raw)
        assert Config.from_env().mplus_feed_enabled is False, raw
    for raw in ("1", "true", "True", "yes", "on", "anything"):
        monkeypatch.setenv("MPLUS_FEED_ENABLED", raw)
        assert Config.from_env().mplus_feed_enabled is True, raw


def test_boolean_flag_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("BOT_COMMANDS_ENABLED", raising=False)
    monkeypatch.delenv("MPLUS_FEED_ENABLED", raising=False)
    c = Config.from_env()
    assert c.bot_commands_enabled is True   # defaults on
    assert c.mplus_feed_enabled is False    # defaults off


def test_warcraftlogs_enabled_without_zone(monkeypatch):
    # The zone (raid tier) is auto-detected, so credentials alone enable WCL.
    monkeypatch.setenv("WARCRAFTLOGS_CLIENT_ID", "id")
    monkeypatch.setenv("WARCRAFTLOGS_CLIENT_SECRET", "shh")
    c = Config.from_env()
    assert c.warcraftlogs_enabled is True
    assert c.warcraftlogs_zone_id is None


def test_feed_bool_flags_respect_falsy_words(monkeypatch):
    # Regression: these two flags used to bypass _bool and treat "off"/"False"
    # (no lowercasing, no "off") as ENABLED.
    for raw in ("off", "Off", "False", "FALSE", "No", "0"):
        monkeypatch.setenv("AFFIXES_FEED_ENABLED", raw)
        monkeypatch.setenv("REALM_PROGRESS_FEED_ENABLED", raw)
        c = Config.from_env()
        assert c.affixes_feed_enabled is False, raw
        assert c.realm_progress_feed_enabled is False, raw


def test_feed_bool_flags_enable_on_truthy(monkeypatch):
    monkeypatch.setenv("AFFIXES_FEED_ENABLED", "1")
    monkeypatch.setenv("REALM_PROGRESS_FEED_ENABLED", "true")
    c = Config.from_env()
    assert c.affixes_feed_enabled is True
    assert c.realm_progress_feed_enabled is True


def test_non_numeric_int_and_float_env_fall_back_to_default(monkeypatch):
    # A garbage value for a numeric env var must not crash from_env; it parses to
    # None and the field keeps its default rather than raising.
    monkeypatch.setenv("DISCORD_GUILD_ID", "not-a-number")  # _int -> None
    monkeypatch.setenv("RECRUIT_MIN_ITEM_LEVEL", "high")    # _float -> None
    c = Config.from_env()
    assert c.discord_guild_id is None
    assert c.recruit_min_item_level == Config().recruit_min_item_level  # default kept


def test_int_list_skips_non_numeric_entries(monkeypatch):
    # WARCRAFTLOGS_ZONE_IDS is comma-separated ints; a stray non-int is dropped,
    # not fatal.
    monkeypatch.setenv("WARCRAFTLOGS_ZONE_IDS", "12, bad, 34")
    assert Config.from_env().warcraftlogs_zone_ids == (12, 34)


def test_scout_zone_ids_falls_back_to_single_zone():
    # No zone-ids list, but a single pinned zone -> that one becomes the scan set.
    assert Config(warcraftlogs_zone_id=42).scout_zone_ids == (42,)
    assert Config().scout_zone_ids == ()  # nothing configured
