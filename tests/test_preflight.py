from dataclasses import replace

from ftabot.config import Config
from scripts.preflight import FAIL, OK, WARN, check, exit_code, format_findings

TOKEN = "MTAxMDEw.AbCdEf.0123456789abcdefghij"
GUILD = 123456789012345678
CHANNEL = "234567890123456789"


def _ready() -> Config:
    return Config(
        discord_token=TOKEN,
        discord_guild_id=GUILD,
        gm_discord_id=345678901234567890,
        sync_interval_minutes=360,
        board_refresh_minutes=30,
        feed_poll_minutes=15,
        feed_default_channel_id=CHANNEL,
        blizzard_client_id="id",
        blizzard_client_secret="secret",
    )


def _by_name(config: Config) -> dict:
    return {f.name: f for f in check(config)}


def test_a_complete_config_has_no_failures_or_warnings():
    findings = check(_ready())
    assert {f.level for f in findings} == {OK}
    assert exit_code(findings) == 0


def test_missing_token_fails_and_blocks():
    findings = check(replace(_ready(), discord_token=None))
    assert _by_name(replace(_ready(), discord_token=None))["Discord token"].level == FAIL
    assert exit_code(findings) == 1


def test_malformed_token_fails_without_echoing_it():
    secret = "not-a-token"
    findings = check(replace(_ready(), discord_token=secret))
    assert _by_name(replace(_ready(), discord_token=secret))["Discord token"].level == FAIL
    assert secret not in format_findings(findings)


def test_unset_guild_id_warns_and_short_id_fails():
    assert _by_name(replace(_ready(), discord_guild_id=None))["Server id"].level == WARN
    assert _by_name(replace(_ready(), discord_guild_id=12345))["Server id"].level == FAIL


def test_gm_id_states():
    assert _by_name(replace(_ready(), gm_discord_id=None))["GM id"].level == WARN
    assert _by_name(replace(_ready(), gm_discord_id=42))["GM id"].level == FAIL


def test_commands_disabled_warns():
    assert _by_name(replace(_ready(), bot_commands_enabled=False))["Slash commands"].level == WARN


def test_posting_mode_states():
    assert _by_name(replace(_ready(), posting_mode="off"))["Posting mode"].level == OK
    assert _by_name(replace(_ready(), posting_mode="live"))["Posting mode"].level == WARN
    assert _by_name(replace(_ready(), posting_mode="loud"))["Posting mode"].level == FAIL


def test_channel_ids_must_be_snowflakes():
    names = _by_name(
        replace(_ready(), test_channel_id="#calcifer-lab", raidhelper_default_channel_id="abc")
    )
    assert names["Test channel"].level == FAIL
    assert names["Raid-Helper channel"].level == FAIL
    assert "Default feed channel" not in names  # a valid id adds no finding


def test_loops_switched_off_warn():
    names = _by_name(
        replace(_ready(), sync_interval_minutes=0, board_refresh_minutes=0, feed_poll_minutes=0)
    )
    assert names["Roster sync"].level == WARN
    assert names["Board refresh"].level == WARN
    assert names["Feeds"].level == WARN


def test_feed_poll_without_channel_warns():
    names = _by_name(replace(_ready(), feed_default_channel_id=None))
    assert names["Feeds"].level == WARN
    assert "FEED_DEFAULT_CHANNEL_ID" in names["Feeds"].detail


def test_milestones_without_feed_loop_warns():
    names = _by_name(replace(_ready(), feed_poll_minutes=0, milestones_feed_enabled=True))
    assert names["Milestones"].level == WARN


def test_missing_blizzard_warns():
    names = _by_name(replace(_ready(), blizzard_client_id=None))
    assert names["Blizzard API"].level == WARN


def test_unknown_timezone_fails():
    assert _by_name(replace(_ready(), raid_tz="Mars/Olympus"))["Server timezone"].level == FAIL


def test_format_findings_summarises_and_flags_not_ready():
    text = format_findings(check(replace(_ready(), discord_token=None)))
    assert "NOT READY" in text
    assert "1 failed" in text
    assert "ready to start" in format_findings(check(_ready()))


def test_unparsed_numeric_setting_fails():
    names = _by_name(replace(_ready(), discord_guild_id=None, unparsed_env=("DISCORD_GUILD_ID",)))
    assert names["DISCORD_GUILD_ID"].level == FAIL
    assert exit_code(check(replace(_ready(), unparsed_env=("SYNC_INTERVAL_MINUTES",)))) == 1


def test_journey_names_the_portal_intent():
    assert "Journey" not in _by_name(_ready())  # off: silent
    on = _by_name(replace(_ready(), journey_enabled=True))["Journey"]
    assert on.level == OK and "Server Members intent" in on.detail
    no_guild = _by_name(replace(_ready(), journey_enabled=True, discord_guild_id=None))
    assert no_guild["Journey"].level == WARN
